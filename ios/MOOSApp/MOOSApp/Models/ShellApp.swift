enum ShellDestination: String, Hashable, Sendable {
    case terminal
    case files
    case apps
    case settings
}

struct ShellApp: Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let symbolName: String
    let destination: ShellDestination
}
