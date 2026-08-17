enum MOOSProtocolV1 {
    static let version = 1

    enum Operation: String, CaseIterable, Codable, Sendable {
        case status
        case personalStatus = "personal.status"
        case personalStart = "personal.start"
        case personalStop = "personal.stop"
        case personalTerminalOpen = "personal.terminal.open"
    }

    struct ControlRequest: Codable, Equatable, Sendable {
        let protocolVersion: Int
        let operation: Operation

        init(operation: Operation) {
            protocolVersion = MOOSProtocolV1.version
            self.operation = operation
        }
    }
}
