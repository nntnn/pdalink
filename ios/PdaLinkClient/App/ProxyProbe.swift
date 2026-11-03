import Foundation
import Network
import PdaLinkCore

enum ProxyProbeError: LocalizedError {
    case timedOut
    case connectionFailed(String)
    case responseTooLarge
    case invalidResponse
    case unexpectedStatus(Int)
    case endpointMismatch

    var errorDescription: String? {
        switch self {
        case .timedOut:
            return "The Android proxy did not respond within 8 seconds."
        case let .connectionFailed(message):
            return "The Android proxy is unreachable: \(message)"
        case .responseTooLarge:
            return "The Android proxy returned an oversized PAC response."
        case .invalidResponse:
            return "The Android proxy returned an invalid PAC response."
        case let .unexpectedStatus(status):
            return "The Android proxy returned HTTP status \(status)."
        case .endpointMismatch:
            return "The PAC endpoint does not match the proxy in the QR code."
        }
    }
}

enum ProxyProbe {
    static func verify(_ settings: TunnelSettings) async throws {
        let cancellation = ProbeCancellationHandle()
        try await withTaskCancellationHandler {
            try Task.checkCancellation()
            try await withCheckedThrowingContinuation { continuation in
                let attempt = ProbeAttempt(
                    settings: settings,
                    continuation: continuation
                )
                cancellation.install(attempt)
                attempt.start()
            }
        } onCancel: {
            cancellation.cancel()
        }
    }
}

private final class ProbeCancellationHandle: @unchecked Sendable {
    private let lock = NSLock()
    private var attempt: ProbeAttempt?
    private var isCancelled = false

    func install(_ attempt: ProbeAttempt) {
        let shouldCancel = withLock { () -> Bool in
            guard !isCancelled else { return true }
            self.attempt = attempt
            return false
        }
        if shouldCancel {
            attempt.cancel()
        }
    }

    func cancel() {
        let attempt = withLock { () -> ProbeAttempt? in
            isCancelled = true
            return self.attempt
        }
        attempt?.cancel()
    }

    private func withLock<T>(_ operation: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return operation()
    }
}

private final class ProbeAttempt: @unchecked Sendable {
    private let settings: TunnelSettings
    private let continuation: CheckedContinuation<Void, Error>
    private let queue = DispatchQueue(label: "dev.pdalink.client.proxy-probe")
    private var connection: NWConnection?
    private var response = Data()
    private var isFinished = false

    init(settings: TunnelSettings, continuation: CheckedContinuation<Void, Error>) {
        self.settings = settings
        self.continuation = continuation
    }

    func start() {
        queue.async { [self] in
            guard !isFinished else { return }
            startConnection()
        }
    }

    func cancel() {
        queue.async { [self] in
            finish(throwing: CancellationError())
        }
    }

    private func startConnection() {
        let connection = NWConnection(
            host: NWEndpoint.Host(settings.proxyHost),
            port: NWEndpoint.Port(rawValue: UInt16(settings.proxyPort))!,
            using: .tcp
        )
        self.connection = connection
        // NWConnection retains this handler; finish() clears it and breaks the
        // temporary self-retain cycle after the continuation is resumed.
        connection.stateUpdateHandler = { [self] state in
            switch state {
            case .ready:
                self.sendRequest()
            case let .failed(error):
                self.finish(throwing: ProxyProbeError.connectionFailed(error.localizedDescription))
            case .cancelled where !self.isFinished:
                self.finish(throwing: ProxyProbeError.connectionFailed("connection cancelled"))
            default:
                break
            }
        }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + 8) { [weak self] in
            self?.finish(throwing: ProxyProbeError.timedOut)
        }
    }

    private func sendRequest() {
        let request = "GET /proxy.pac HTTP/1.1\r\nHost: \(settings.proxyHost):\(settings.proxyPort)\r\nConnection: close\r\n\r\n"
        connection?.send(content: Data(request.utf8), completion: .contentProcessed { [weak self] error in
            guard let self else { return }
            if let error {
                self.finish(throwing: ProxyProbeError.connectionFailed(error.localizedDescription))
            } else {
                self.receiveResponse()
            }
        })
    }

    private func receiveResponse() {
        connection?.receive(minimumIncompleteLength: 1, maximumLength: 4_096) { [weak self] data, _, complete, error in
            guard let self, !self.isFinished else { return }
            if let data {
                self.response.append(data)
            }
            if self.response.count > 16_384 {
                self.finish(throwing: ProxyProbeError.responseTooLarge)
                return
            }
            if let error {
                self.finish(throwing: ProxyProbeError.connectionFailed(error.localizedDescription))
                return
            }
            if self.validateIfComplete(streamEnded: complete) {
                return
            }
            self.receiveResponse()
        }
    }

    @discardableResult
    private func validateIfComplete(streamEnded: Bool) -> Bool {
        do {
            let complete = try PACResponseValidator.validate(
                response,
                streamEnded: streamEnded,
                expectedEndpoint: "\(settings.proxyHost):\(settings.proxyPort)"
            )
            if complete {
                finish()
            }
            return complete
        } catch let error as PACResponseValidationError {
            switch error {
            case .invalidResponse:
                finish(throwing: ProxyProbeError.invalidResponse)
            case let .unexpectedStatus(status):
                finish(throwing: ProxyProbeError.unexpectedStatus(status))
            case .endpointMismatch:
                finish(throwing: ProxyProbeError.endpointMismatch)
            }
            return true
        } catch {
            finish(throwing: error)
            return true
        }
    }

    private func finish(throwing error: Error? = nil) {
        guard !isFinished else { return }
        isFinished = true
        connection?.stateUpdateHandler = nil
        connection?.cancel()
        connection = nil
        if let error {
            continuation.resume(throwing: error)
        } else {
            continuation.resume()
        }
    }
}
