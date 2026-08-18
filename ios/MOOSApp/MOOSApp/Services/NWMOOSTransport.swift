import Foundation
import Network

protocol MOOSConnection: Sendable {
    func request(_ frame: Data, maximumResponseBytes: Int) async throws -> Data
    func close() async
}

protocol MOOSGatewayConnection: MOOSConnection {
    func sendFrame(_ frame: Data) async throws
    func receiveFrame(maximumFrameBytes: Int) async throws -> Data
}

extension MOOSGatewayConnection {
    func request(_ frame: Data, maximumResponseBytes: Int) async throws -> Data {
        try await sendFrame(frame)
        return try await receiveFrame(maximumFrameBytes: maximumResponseBytes)
    }
}

protocol MOOSConnectionConnecting: Sendable {
    func connect(to host: HostConfiguration) async throws -> any MOOSGatewayConnection
}

enum MOOSTransportError: Error, LocalizedError {
    case connectionFailed
    case responseTooLarge
    case truncatedResponse

    var errorDescription: String? {
        switch self {
        case .connectionFailed:
            return "The MOOS Host could not be reached."
        case .responseTooLarge:
            return "The host response exceeded the Protocol v1 frame limit."
        case .truncatedResponse:
            return "The host closed the connection during its response."
        }
    }
}

struct NWMOOSConnectionConnector: MOOSConnectionConnecting {
    func connect(to host: HostConfiguration) async throws -> any MOOSGatewayConnection {
        guard let port = NWEndpoint.Port(rawValue: host.port) else {
            throw MOOSTransportError.connectionFailed
        }
        let connection = NetworkMOOSConnection(
            connection: NWConnection(
                host: NWEndpoint.Host(host.address),
                port: port,
                using: .tcp
            )
        )
        try await connection.start()
        return connection
    }
}

actor NetworkMOOSConnection: MOOSGatewayConnection {
    private static let timeoutNanoseconds: UInt64 = 10_000_000_000
    private let connection: NWConnection
    private let queue = DispatchQueue(label: "dev.moos.protocol-v1")
    private var receiveBuffer = Data()

    init(connection: NWConnection) {
        self.connection = connection
    }

    func start() async throws {
        try await withCheckedThrowingContinuation {
            (continuation: CheckedContinuation<Void, Error>) in
            let completion = LockedOneShotContinuation(continuation)
            let connection = self.connection
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    completion.resume(returning: ())
                case .failed, .cancelled:
                    completion.resume(throwing: MOOSTransportError.connectionFailed)
                default:
                    break
                }
            }
            connection.start(queue: queue)
            queue.asyncAfter(deadline: .now() + 10) {
                if completion.resume(throwing: MOOSTransportError.connectionFailed) {
                    connection.cancel()
                }
            }
        }
    }

    func sendFrame(_ frame: Data) async throws {
        let timeout = Task { [connection] in
            try? await Task.sleep(nanoseconds: Self.timeoutNanoseconds)
            guard !Task.isCancelled else {
                return
            }
            connection.cancel()
        }
        defer { timeout.cancel() }
        try await send(frame)
    }

    func receiveFrame(maximumFrameBytes: Int) async throws -> Data {
        let timeout = Task { [connection] in
            try? await Task.sleep(nanoseconds: Self.timeoutNanoseconds)
            guard !Task.isCancelled else {
                return
            }
            connection.cancel()
        }
        defer { timeout.cancel() }

        while true {
            if let newline = receiveBuffer.firstIndex(of: 0x0A) {
                let response = Data(receiveBuffer[..<newline])
                receiveBuffer.removeSubrange(...newline)
                guard response.count <= maximumFrameBytes else {
                    throw MOOSTransportError.responseTooLarge
                }
                return response
            }
            guard receiveBuffer.count <= maximumFrameBytes else {
                throw MOOSTransportError.responseTooLarge
            }

            let (data, isComplete) = try await receive()
            if let data, !data.isEmpty {
                receiveBuffer.append(data)
            }
            if isComplete {
                if receiveBuffer.contains(0x0A) {
                    continue
                }
                throw MOOSTransportError.truncatedResponse
            }
        }
    }

    func close() async {
        connection.stateUpdateHandler = nil
        connection.cancel()
    }

    private func send(_ data: Data) async throws {
        try await withCheckedThrowingContinuation { continuation in
            connection.send(content: data, completion: .contentProcessed { error in
                if error == nil {
                    continuation.resume(returning: ())
                } else {
                    continuation.resume(throwing: MOOSTransportError.connectionFailed)
                }
            })
        }
    }

    private func receive() async throws -> (Data?, Bool) {
        try await withCheckedThrowingContinuation { continuation in
            connection.receive(
                minimumIncompleteLength: 1,
                maximumLength: 4_096
            ) { data, _, isComplete, error in
                if error == nil {
                    continuation.resume(returning: (data, isComplete))
                } else {
                    continuation.resume(throwing: MOOSTransportError.connectionFailed)
                }
            }
        }
    }
}

private final class LockedOneShotContinuation<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Value, Error>?

    init(_ continuation: CheckedContinuation<Value, Error>) {
        self.continuation = continuation
    }

    @discardableResult
    func resume(returning value: Value) -> Bool {
        guard let continuation = take() else {
            return false
        }
        continuation.resume(returning: value)
        return true
    }

    @discardableResult
    func resume(throwing error: Error) -> Bool {
        guard let continuation = take() else {
            return false
        }
        continuation.resume(throwing: error)
        return true
    }

    private func take() -> CheckedContinuation<Value, Error>? {
        lock.lock()
        defer { lock.unlock() }
        let current = continuation
        continuation = nil
        return current
    }
}
