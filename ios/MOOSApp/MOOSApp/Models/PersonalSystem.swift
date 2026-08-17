enum PersonalSystemState: String, Equatable, Sendable {
    case starting
    case running
    case stopping
    case stopped
    case failed
    case unknown

    var label: String {
        switch self {
        case .starting:
            return "Starting"
        case .running:
            return "Running"
        case .stopping:
            return "Stopping"
        case .stopped:
            return "Stopped"
        case .failed:
            return "Failed"
        case .unknown:
            return "Unknown"
        }
    }
}

struct PersonalSystem: Identifiable, Equatable, Sendable {
    let id: String
    let displayName: String
    let state: PersonalSystemState
}
