import Foundation
import NetworkExtension
import PdaLinkCore

enum TunnelControllerError: LocalizedError {
    case wifiJoinFailed(String)
    case vpnConfigurationMissing
    case vpnStartFailed(String)

    var errorDescription: String? {
        switch self {
        case let .wifiJoinFailed(message):
            return "Could not join the Android host Wi-Fi: \(message)"
        case .vpnConfigurationMissing:
            return "The saved PdaLink VPN configuration is incomplete."
        case let .vpnStartFailed(message):
            return "Could not start the PdaLink VPN: \(message)"
        }
    }
}

@MainActor
final class TunnelController {
    enum Progress {
        case joiningWiFi
        case checkingProxy
        case installingVPN
    }

    static var providerBundleIdentifier: String {
        (Bundle.main.bundleIdentifier ?? "dev.pdalink.client.ios") + ".packet-tunnel"
    }

    var statusHandler: ((NEVPNStatus) -> Void)?

    private var manager: NETunnelProviderManager?
    private var statusObserver: NSObjectProtocol?

    init() {
        statusObserver = NotificationCenter.default.addObserver(
            forName: .NEVPNStatusDidChange,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let connection = notification.object as? NEVPNConnection else {
                return
            }
            Task { @MainActor [weak self] in
                guard connection === self?.manager?.connection else { return }
                self?.statusHandler?(connection.status)
            }
        }
    }

    deinit {
        if let statusObserver {
            NotificationCenter.default.removeObserver(statusObserver)
        }
    }

    func connect(
        _ connection: PdaLinkConnection,
        progress: @escaping @MainActor (Progress) -> Void
    ) async throws {
        progress(.joiningWiFi)
        try await joinWiFi(connection)

        do {
            try Task.checkCancellation()
            progress(.checkingProxy)
            try await ProxyProbe.verify(connection.tunnelSettings)

            try Task.checkCancellation()
            progress(.installingVPN)
            let manager = try await loadManager()
            let tunnelProtocol = NETunnelProviderProtocol()
            tunnelProtocol.providerBundleIdentifier = Self.providerBundleIdentifier
            tunnelProtocol.serverAddress = connection.proxyHost
            tunnelProtocol.providerConfiguration = [
                "proxyHost": connection.proxyHost,
                "proxyPort": connection.proxyPort,
                "ssid": connection.ssid,
            ]
            manager.localizedDescription = "PdaLink"
            manager.protocolConfiguration = tunnelProtocol
            manager.isEnabled = true
            try await save(manager)
            try Task.checkCancellation()
            try await reload(manager)
            try Task.checkCancellation()

            do {
                try manager.connection.startVPNTunnel()
            } catch {
                throw TunnelControllerError.vpnStartFailed(error.localizedDescription)
            }
        } catch {
            NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: connection.ssid)
            throw error
        }
    }

    func disconnect() async {
        let activeManager = manager ?? (try? await loadManager())
        let ssid = (activeManager?.protocolConfiguration as? NETunnelProviderProtocol)?
            .providerConfiguration?["ssid"] as? String
        activeManager?.connection.stopVPNTunnel()
        if let ssid {
            NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: ssid)
        }
    }

    func refreshStatus() async -> NEVPNStatus {
        do {
            return try await loadManager().connection.status
        } catch {
            return .invalid
        }
    }

    private func joinWiFi(_ connection: PdaLinkConnection) async throws {
        let configuration = NEHotspotConfiguration(
            ssid: connection.ssid,
            passphrase: connection.password,
            isWEP: false
        )
        // The VPN must remain usable after the containing app enters the background.
        configuration.joinOnce = false

        let attempt = WiFiJoinAttempt(ssid: connection.ssid)
        try await withTaskCancellationHandler {
            try Task.checkCancellation()
            try await withCheckedThrowingContinuation { continuation in
                guard attempt.install(continuation) else { return }
                NEHotspotConfigurationManager.shared.apply(configuration) { error in
                    if let error = error as NSError? {
                        if error.domain == NEHotspotConfigurationErrorDomain,
                           error.code == NEHotspotConfigurationError.alreadyAssociated.rawValue {
                            attempt.finish()
                        } else {
                            attempt.finish(
                                throwing: TunnelControllerError.wifiJoinFailed(
                                    error.localizedDescription
                                )
                            )
                        }
                    } else {
                        attempt.finish()
                    }
                }
            }
        } onCancel: {
            attempt.cancel()
        }
    }

    private func loadManager() async throws -> NETunnelProviderManager {
        let managers: [NETunnelProviderManager] = try await withCheckedThrowingContinuation { continuation in
            NETunnelProviderManager.loadAllFromPreferences { managers, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: managers ?? [])
                }
            }
        }
        let selected = managers.first {
            ($0.protocolConfiguration as? NETunnelProviderProtocol)?.providerBundleIdentifier
                == Self.providerBundleIdentifier
        } ?? NETunnelProviderManager()
        manager = selected
        return selected
    }

    private func save(_ manager: NETunnelProviderManager) async throws {
        try await withCheckedThrowingContinuation { continuation in
            manager.saveToPreferences { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    private func reload(_ manager: NETunnelProviderManager) async throws {
        try await withCheckedThrowingContinuation { continuation in
            manager.loadFromPreferences { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
        guard manager.protocolConfiguration is NETunnelProviderProtocol else {
            throw TunnelControllerError.vpnConfigurationMissing
        }
    }
}

private final class WiFiJoinAttempt: @unchecked Sendable {
    private let ssid: String
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void, Error>?
    private var isFinished = false

    init(ssid: String) {
        self.ssid = ssid
    }

    func install(_ continuation: CheckedContinuation<Void, Error>) -> Bool {
        let shouldResumeCancellation = withLock { () -> Bool in
            guard !isFinished else { return true }
            self.continuation = continuation
            return false
        }
        if shouldResumeCancellation {
            continuation.resume(throwing: CancellationError())
            return false
        }
        return true
    }

    func cancel() {
        NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: ssid)
        finish(throwing: CancellationError())
    }

    func finish(throwing error: Error? = nil) {
        let continuation = withLock { () -> CheckedContinuation<Void, Error>? in
            guard !isFinished else { return nil }
            isFinished = true
            let continuation = self.continuation
            self.continuation = nil
            return continuation
        }
        guard let continuation else { return }
        if let error {
            continuation.resume(throwing: error)
        } else {
            continuation.resume()
        }
    }

    private func withLock<T>(_ operation: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return operation()
    }
}
