enum AppSessionState: Equatable, Sendable {
    case active
    case background
}

struct AppSession: Identifiable, Equatable, Sendable {
    let id: String
    let applicationID: String
    let displayName: String
    let symbolName: String
    let state: AppSessionState
}
