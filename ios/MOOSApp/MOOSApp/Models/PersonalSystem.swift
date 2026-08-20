enum PersonalSystemState: String, Codable, Equatable, Sendable {
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

struct PersonalSystem: Identifiable, Codable, Equatable, Sendable {
    let id: String
    let displayName: String
    let state: PersonalSystemState
    let capabilities: Set<InstanceCapability>

    init(
        id: String,
        displayName: String,
        state: PersonalSystemState,
        capabilities: Set<InstanceCapability> = []
    ) {
        self.id = id
        self.displayName = displayName
        self.state = state
        self.capabilities = capabilities
    }
}

enum InstanceCapability: String, CaseIterable, Codable, Hashable, Sendable {
    case console
    case files
    case plugins
    case backups
    case players
    case desktop
    case gallery
    case renderQueue
    case session
    case repository
    case logs
}
