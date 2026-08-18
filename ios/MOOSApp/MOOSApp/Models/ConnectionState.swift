enum ConnectionState: String, CaseIterable, Codable, Equatable, Sendable {
    case noHost
    case connected
    case connecting
    case reconnecting
    case disconnected
    case offline

    var label: String {
        switch self {
        case .noHost:
            return "No Host"
        case .connected:
            return "Connected"
        case .connecting:
            return "Connecting"
        case .reconnecting:
            return "Reconnecting"
        case .disconnected:
            return "Disconnected"
        case .offline:
            return "Offline"
        }
    }

    var isReachable: Bool {
        self == .connected
    }
}
