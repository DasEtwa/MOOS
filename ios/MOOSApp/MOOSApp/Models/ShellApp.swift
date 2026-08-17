enum ShellDestination: String, Codable, Hashable, Sendable {
    case blender
    case discord
    case terminal
    case files
    case apps
    case appStore
    case instances
    case settings
}

struct AppIconMetadata: Codable, Equatable, Sendable {
    let fallbackSymbolName: String
    let contentHash: String?
}

struct ShellApp: Identifiable, Codable, Equatable, Sendable {
    let id: String
    let name: String
    let icon: AppIconMetadata
    let destination: ShellDestination

    var symbolName: String {
        icon.fallbackSymbolName
    }
}
