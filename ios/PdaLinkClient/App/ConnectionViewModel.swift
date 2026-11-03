import NetworkExtension
import PdaLinkCore
import SwiftUI

enum ConnectionViewModelError: LocalizedError {
    case clipboardEmpty

    var errorDescription: String? {
        switch self {
        case .clipboardEmpty:
            return "The clipboard does not contain a PdaLink connection URI."
        }
    }
}

@MainActor
final class ConnectionViewModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        case ready
        case joiningWiFi
        case checkingProxy
        case installingVPN
        case connecting
        case connected
        case disconnecting
        case failed
    }

    @Published var connection: PdaLinkConnection?
    @Published var phase: Phase = .idle
    @Published var statusDetail = "Scan the QR shown by the Android host."
    @Published var isScannerPresented = false
    @Published var isShowingError = false
    @Published var errorMessage = ""

    private let tunnelController: TunnelController
    private var connectionTask: Task<Void, Never>?
    private var connectionGeneration = 0

    init(tunnelController: TunnelController = TunnelController()) {
        self.tunnelController = tunnelController
        tunnelController.statusHandler = { [weak self] status in
            self?.applyVPNStatus(status)
        }
        let refreshGeneration = connectionGeneration
        Task {
            let status = await tunnelController.refreshStatus()
            guard connectionGeneration == refreshGeneration else { return }
            applyVPNStatus(status)
        }
    }

    var isBusy: Bool {
        switch phase {
        case .joiningWiFi, .checkingProxy, .installingVPN, .connecting, .disconnecting:
            return true
        default:
            return false
        }
    }

    var isConnected: Bool {
        phase == .connected
    }

    var statusTitle: String {
        switch phase {
        case .idle: "Not configured"
        case .ready: "Ready"
        case .joiningWiFi: "Joining Wi-Fi"
        case .checkingProxy: "Checking host proxy"
        case .installingVPN: "Preparing VPN"
        case .connecting: "Connecting"
        case .connected: "Connected"
        case .disconnecting: "Disconnecting"
        case .failed: "Needs attention"
        }
    }

    var statusSymbol: String {
        switch phase {
        case .connected: "checkmark.circle.fill"
        case .failed: "exclamationmark.triangle.fill"
        case .idle, .ready: "circle"
        default: "arrow.triangle.2.circlepath"
        }
    }

    var statusColor: Color {
        switch phase {
        case .connected: .green
        case .failed: .orange
        case .idle, .ready: .secondary
        default: .blue
        }
    }

    func acceptConnectionURI(_ value: String) {
        do {
            connection = try PdaLinkConnectionURI.parse(value)
            phase = .ready
            statusDetail = "Ready to join the Android host network."
        } catch {
            present(error)
        }
    }

    func clearConnection() {
        connection = nil
        phase = .idle
        statusDetail = "Scan the QR shown by the Android host."
    }

    func connect() {
        guard let connection, connectionTask == nil, !isBusy, !isConnected else {
            return
        }
        connectionGeneration += 1
        let generation = connectionGeneration
        connectionTask = Task {
            defer {
                if connectionGeneration == generation {
                    connectionTask = nil
                }
            }
            do {
                try await tunnelController.connect(connection) { [weak self] progress in
                    guard self?.connectionGeneration == generation else { return }
                    self?.apply(progress)
                }
                guard connectionGeneration == generation else { return }
                let status = await tunnelController.refreshStatus()
                guard connectionGeneration == generation else { return }
                if status == .connected || status == .connecting || status == .reasserting {
                    applyVPNStatus(status)
                } else {
                    phase = .connecting
                    statusDetail = "Waiting for iOS to activate the packet tunnel."
                }
            } catch is CancellationError {
                guard connectionGeneration == generation else { return }
                phase = .ready
                statusDetail = "Connection cancelled."
            } catch {
                guard connectionGeneration == generation else { return }
                present(error)
            }
        }
    }

    func disconnect() {
        guard phase != .disconnecting else {
            return
        }
        connectionGeneration += 1
        let pendingConnection = connectionTask
        pendingConnection?.cancel()
        connectionTask = nil
        phase = .disconnecting
        statusDetail = "Stopping the VPN and releasing the host Wi-Fi configuration."
        Task {
            await tunnelController.disconnect()
            await pendingConnection?.value
            if phase == .disconnecting {
                phase = connection == nil ? .idle : .ready
                statusDetail = "Disconnected."
            }
        }
    }

    func present(_ error: Error) {
        phase = .failed
        errorMessage = error.localizedDescription
        statusDetail = error.localizedDescription
        isShowingError = true
    }

    private func apply(_ progress: TunnelController.Progress) {
        switch progress {
        case .joiningWiFi:
            phase = .joiningWiFi
            statusDetail = "Approve the Wi-Fi request if iOS asks."
        case .checkingProxy:
            phase = .checkingProxy
            statusDetail = "Verifying the exact PAC endpoint from the QR."
        case .installingVPN:
            phase = .installingVPN
            statusDetail = "Approve the VPN configuration if iOS asks."
        }
    }

    private func applyVPNStatus(_ status: NEVPNStatus) {
        // Manager refreshes and save notifications can report the previous
        // disconnected state while a new pre-VPN workflow is still running.
        // The explicit connect task owns these phases.
        switch phase {
        case .joiningWiFi, .checkingProxy, .installingVPN:
            return
        default:
            break
        }
        switch status {
        case .connected:
            phase = .connected
            statusDetail = "IPv4 TCP traffic is using the Android PdaLink proxy."
        case .connecting, .reasserting:
            phase = .connecting
            statusDetail = "iOS is activating the PdaLink packet tunnel."
        case .disconnecting:
            phase = .disconnecting
            statusDetail = "The PdaLink tunnel is stopping."
        case .disconnected, .invalid:
            if phase != .failed {
                phase = connection == nil ? .idle : .ready
                statusDetail = connection == nil
                    ? "Scan the QR shown by the Android host."
                    : "Ready to join the Android host network."
            }
        @unknown default:
            phase = .failed
            statusDetail = "iOS reported an unknown VPN state."
        }
    }
}
