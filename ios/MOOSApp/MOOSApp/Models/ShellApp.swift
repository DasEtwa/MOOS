enum ShellDestination: Hashable, Sendable {
    case blender
    case discord
    case terminal
    case files
    case apps
    case appStore
    case instances
    case settings
}

struct ShellApp: Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let symbolName: String
    let destination: ShellDestination
}
