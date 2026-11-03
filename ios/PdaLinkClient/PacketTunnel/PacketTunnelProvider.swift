import Foundation
import NetworkExtension
import PdaLinkCore
import tun2proxy

enum PacketTunnelError: LocalizedError {
    case invalidConfiguration
    case startCancelled
    case bridgeCreationFailed(Int32)
    case engineStopped(Int32)

    var errorDescription: String? {
        switch self {
        case .invalidConfiguration:
            return "The PdaLink packet tunnel configuration is invalid."
        case .startCancelled:
            return "The PdaLink packet tunnel start was cancelled."
        case let .bridgeCreationFailed(code):
            return "Could not create the packet-flow bridge (errno \(code))."
        case let .engineStopped(code):
            return "The PdaLink packet engine stopped unexpectedly (code \(code))."
        }
    }
}

final class PacketTunnelProvider: NEPacketTunnelProvider {
    private static let mtu: UInt16 = 1_500

    private let engineQueue = DispatchQueue(label: "dev.pdalink.client.tun2proxy", qos: .userInitiated)
    private let stateLock = NSLock()
    private var packetBridge: PacketFlowBridge?
    private var stopping = true
    private var generation: UInt64 = 0

    override func startTunnel(
        options: [String: NSObject]?,
        completionHandler: @escaping (Error?) -> Void
    ) {
        do {
            let settings = try loadSettings()
            let startGeneration = withStateLock { () -> UInt64 in
                generation &+= 1
                stopping = false
                return generation
            }
            let networkSettings = makeNetworkSettings(for: settings)
            setTunnelNetworkSettings(networkSettings) { [weak self] error in
                guard let self else {
                    completionHandler(PacketTunnelError.invalidConfiguration)
                    return
                }
                guard self.isActive(startGeneration) else {
                    completionHandler(PacketTunnelError.startCancelled)
                    return
                }
                if let error {
                    completionHandler(error)
                    return
                }
                do {
                    try self.startPacketEngine(
                        settings: settings,
                        generation: startGeneration
                    )
                    completionHandler(nil)
                } catch {
                    completionHandler(error)
                }
            }
        } catch {
            completionHandler(error)
        }
    }

    override func stopTunnel(
        with reason: NEProviderStopReason,
        completionHandler: @escaping () -> Void
    ) {
        let bridge = withStateLock { () -> PacketFlowBridge? in
            generation &+= 1
            stopping = true
            let bridge = packetBridge
            packetBridge = nil
            return bridge
        }
        _ = tun2proxy_stop()
        bridge?.stop()
        // The engine owns process-global tun2proxy state. Queue completion
        // behind the blocking run call so a rapid reconnect cannot observe the
        // previous run as still active.
        engineQueue.async {
            completionHandler()
        }
    }

    private func loadSettings() throws -> TunnelSettings {
        guard let tunnelProtocol = protocolConfiguration as? NETunnelProviderProtocol,
              let configuration = tunnelProtocol.providerConfiguration,
              let proxyHost = configuration["proxyHost"] as? String else {
            throw PacketTunnelError.invalidConfiguration
        }

        let proxyPort: Int
        if let value = configuration["proxyPort"] as? Int {
            proxyPort = value
        } else if let value = configuration["proxyPort"] as? NSNumber {
            proxyPort = value.intValue
        } else {
            throw PacketTunnelError.invalidConfiguration
        }
        do {
            return try TunnelSettings(proxyHost: proxyHost, proxyPort: proxyPort)
        } catch {
            throw PacketTunnelError.invalidConfiguration
        }
    }

    private func makeNetworkSettings(for settings: TunnelSettings) -> NEPacketTunnelNetworkSettings {
        let networkSettings = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: settings.proxyHost)
        networkSettings.mtu = NSNumber(value: Self.mtu)

        let ipv4 = NEIPv4Settings(
            addresses: ["10.66.0.2"],
            subnetMasks: ["255.255.255.0"]
        )
        ipv4.includedRoutes = [NEIPv4Route.default()]
        // The proxy socket must stay on the Android Wi-Fi path, outside the TUN.
        ipv4.excludedRoutes = [
            NEIPv4Route(
                destinationAddress: settings.proxyHost,
                subnetMask: "255.255.255.255"
            ),
        ]
        networkSettings.ipv4Settings = ipv4

        let dns = NEDNSSettings(servers: ["198.18.0.1"])
        dns.matchDomains = [""]
        networkSettings.dnsSettings = dns
        return networkSettings
    }

    private func startPacketEngine(
        settings: TunnelSettings,
        generation startGeneration: UInt64
    ) throws {
        let bridge = try PacketFlowBridge(packetFlow: packetFlow)
        let installed = withStateLock { () -> Bool in
            guard generation == startGeneration, !stopping else { return false }
            packetBridge = bridge
            return true
        }
        guard installed else {
            bridge.stop()
            throw PacketTunnelError.startCancelled
        }
        bridge.start()

        let arguments = "tun2proxy"
            + " --tun-fd \(bridge.engineFileDescriptor)"
            + " --close-fd-on-drop false"
            + " --proxy \(settings.proxyEndpoint)"
            + " --dns virtual"
            + " --dns-addr 8.8.8.8"
            + " --verbosity info"

        engineQueue.async { [weak self, bridge] in
            guard let self, self.isActive(startGeneration) else {
                bridge.stop()
                return
            }
            let result = arguments.withCString {
                tun2proxy_run_with_cli_args($0, Self.mtu, true)
            }
            let shouldCancel = self.withStateLock { () -> Bool in
                if self.packetBridge === bridge {
                    self.packetBridge = nil
                }
                return self.generation == startGeneration && !self.stopping
            }
            bridge.stop()
            if shouldCancel {
                self.cancelTunnelWithError(PacketTunnelError.engineStopped(result))
            }
        }
    }

    private func isActive(_ expectedGeneration: UInt64) -> Bool {
        withStateLock {
            generation == expectedGeneration && !stopping
        }
    }

    private func withStateLock<T>(_ operation: () throws -> T) rethrows -> T {
        stateLock.lock()
        defer { stateLock.unlock() }
        return try operation()
    }
}
