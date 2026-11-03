import Darwin
import Foundation
import NetworkExtension

/// Bridges the public NEPacketTunnelFlow packet API to the file-descriptor API
/// consumed by tun2proxy. This intentionally avoids KVC access to Apple's
/// private packetFlow socket implementation.
final class PacketFlowBridge {
    let engineFileDescriptor: Int32

    private let packetFlow: NEPacketTunnelFlow
    private let flowFileDescriptor: Int32
    private let outboundQueue = DispatchQueue(label: "dev.pdalink.client.packet-flow.outbound")
    private let inboundQueue = DispatchQueue(label: "dev.pdalink.client.packet-flow.inbound")
    private let outboundQueueKey = DispatchSpecificKey<Void>()
    private let stateLock = NSLock()
    private var readSource: DispatchSourceRead?
    private var writeSource: DispatchSourceWrite?
    private var pendingOutboundFrames: [Data] = []
    private var inboundBuffer = [UInt8](repeating: 0, count: 2_048)
    private var active = false
    private var closed = false

    init(packetFlow: NEPacketTunnelFlow) throws {
        var descriptors = [Int32](repeating: -1, count: 2)
        let result = socketpair(AF_UNIX, Int32(SOCK_DGRAM.rawValue), 0, &descriptors)
        guard result == 0 else {
            throw PacketTunnelError.bridgeCreationFailed(errno)
        }
        self.packetFlow = packetFlow
        flowFileDescriptor = descriptors[0]
        engineFileDescriptor = descriptors[1]
        outboundQueue.setSpecific(key: outboundQueueKey, value: ())

        let flags = fcntl(flowFileDescriptor, F_GETFL)
        if flags >= 0 {
            _ = fcntl(flowFileDescriptor, F_SETFL, flags | O_NONBLOCK)
        }
    }

    deinit {
        stop()
    }

    func start() {
        let shouldStart = withStateLock { () -> Bool in
            guard !active, !closed else { return false }
            active = true
            return true
        }
        guard shouldStart else { return }

        let source = DispatchSource.makeReadSource(
            fileDescriptor: flowFileDescriptor,
            queue: inboundQueue
        )
        source.setEventHandler { [weak self] in
            self?.drainPacketsFromEngine()
        }
        let flowFileDescriptor = self.flowFileDescriptor
        source.setCancelHandler {
            _ = Darwin.close(flowFileDescriptor)
        }
        source.resume()
        readSource = source
        readPacketsFromFlow()
    }

    func stop() {
        let descriptors = withStateLock { () -> (Int32, Int32)? in
            guard !closed else { return nil }
            active = false
            closed = true
            return (flowFileDescriptor, engineFileDescriptor)
        }
        guard let descriptors else { return }

        let source = readSource
        source?.setEventHandler {}
        source?.cancel()
        readSource = nil
        let cancelOutbound = { [self] in
            writeSource?.cancel()
            writeSource = nil
            pendingOutboundFrames.removeAll(keepingCapacity: false)
        }
        if DispatchQueue.getSpecific(key: outboundQueueKey) != nil {
            cancelOutbound()
        } else {
            outboundQueue.sync(execute: cancelOutbound)
        }
        _ = shutdown(descriptors.0, SHUT_RDWR)
        _ = shutdown(descriptors.1, SHUT_RDWR)
        if source == nil {
            _ = Darwin.close(descriptors.0)
        }
        _ = Darwin.close(descriptors.1)
    }

    private var isActive: Bool {
        withStateLock { active && !closed }
    }

    private func readPacketsFromFlow() {
        guard isActive else { return }
        packetFlow.readPackets { [weak self] packets, protocols in
            guard let self else { return }
            self.outboundQueue.async {
                guard self.isActive else { return }
                for (packet, family) in zip(packets, protocols) {
                    if let frame = self.makeFrame(
                        packet: packet,
                        protocolFamily: family.uint32Value
                    ) {
                        self.pendingOutboundFrames.append(frame)
                    }
                }
                self.flushOutboundFrames()
            }
        }
    }

    private func makeFrame(packet: Data, protocolFamily: UInt32) -> Data? {
        guard protocolFamily == UInt32(AF_INET) || protocolFamily == UInt32(AF_INET6) else {
            return nil
        }
        var family = protocolFamily.bigEndian
        var frame = Data(capacity: packet.count + MemoryLayout<UInt32>.size)
        withUnsafeBytes(of: &family) { bytes in
            frame.append(contentsOf: bytes)
        }
        frame.append(packet)
        return frame
    }

    /// Runs only on outboundQueue. Keeping at most one packetFlow batch in
    /// memory lets the datagram socket's write readiness provide backpressure
    /// instead of dropping packets after an arbitrary retry timeout.
    private func flushOutboundFrames() {
        guard isActive else { return }
        while let frame = pendingOutboundFrames.first {
            let sent = frame.withUnsafeBytes { bytes in
                Darwin.send(flowFileDescriptor, bytes.baseAddress, bytes.count, 0)
            }
            if sent == frame.count {
                pendingOutboundFrames.removeFirst()
                continue
            }
            if sent < 0, errno == EINTR {
                continue
            }
            if sent < 0, errno == EAGAIN || errno == EWOULDBLOCK {
                waitForWriteReadiness()
                return
            }
            stop()
            return
        }
        writeSource?.cancel()
        writeSource = nil
        readPacketsFromFlow()
    }

    private func waitForWriteReadiness() {
        guard writeSource == nil, isActive else { return }
        let source = DispatchSource.makeWriteSource(
            fileDescriptor: flowFileDescriptor,
            queue: outboundQueue
        )
        source.setEventHandler { [weak self] in
            self?.flushOutboundFrames()
        }
        source.resume()
        writeSource = source
    }

    private func drainPacketsFromEngine() {
        while isActive {
            let count = inboundBuffer.withUnsafeMutableBytes { bytes in
                Darwin.recv(flowFileDescriptor, bytes.baseAddress, bytes.count, 0)
            }
            if count < 0 {
                if errno == EINTR {
                    continue
                }
                if errno == EAGAIN || errno == EWOULDBLOCK {
                    return
                }
                stop()
                return
            }
            guard count > MemoryLayout<UInt32>.size else {
                continue
            }

            let family = UInt32(inboundBuffer[0]) << 24
                | UInt32(inboundBuffer[1]) << 16
                | UInt32(inboundBuffer[2]) << 8
                | UInt32(inboundBuffer[3])
            guard family == UInt32(AF_INET) || family == UInt32(AF_INET6) else {
                continue
            }
            let packet = Data(inboundBuffer[MemoryLayout<UInt32>.size..<count])
            let accepted = packetFlow.writePackets(
                [packet],
                withProtocols: [NSNumber(value: family)]
            )
            if !accepted {
                // NEPacketTunnelFlow offers no retry callback. Tear down the
                // bridge so the provider reports failure instead of silently
                // losing traffic.
                stop()
                return
            }
        }
    }

    private func withStateLock<T>(_ operation: () throws -> T) rethrows -> T {
        stateLock.lock()
        defer { stateLock.unlock() }
        return try operation()
    }
}
