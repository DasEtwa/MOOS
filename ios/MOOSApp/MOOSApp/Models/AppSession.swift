enum AppSessionState: String, Codable, Equatable, Sendable {
    case active
    case background
}

struct AppSession: Identifiable, Codable, Equatable, Sendable {
    let id: String
    let applicationID: String
    let displayName: String
    let symbolName: String
    let state: AppSessionState
}
