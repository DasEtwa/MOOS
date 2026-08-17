enum ConnectionState: String, CaseIterable, Codable, Equatable, Sendable {
    case connected
    case connecting
    case reconnecting
    case offline

    var label: String {
        switch self {
        case .connected:
            return "Connected"
        case .connecting:
            return "Connecting"
        case .reconnecting:
            return "Reconnecting"
        case .offline:
            return "Offline"
        }
    }

    var isReachable: Bool {
        self == .connected
    }
}
