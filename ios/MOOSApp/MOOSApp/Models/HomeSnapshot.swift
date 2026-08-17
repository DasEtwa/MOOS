import Foundation

struct HomeSnapshot: Equatable, Sendable {
    let host: Host
    let personalSystem: PersonalSystem
    let connectionState: ConnectionState
    let widgets: [ShellWidget]
    let applications: [ShellApp]
    let sessions: [AppSession]
    let latencyMilliseconds: Int?
    let uptime: SynchronizedUptime?
    let lastSynchronizedAt: Date?
    let isShowingCachedMetadata: Bool

    static let placeholder = HomeSnapshot(
        host: Host(id: "unconfigured", displayName: "No host configured"),
        personalSystem: PersonalSystem(
            id: "personal",
            displayName: "Personal MOOS",
            state: .unknown
        ),
        connectionState: .connecting,
        widgets: [],
        applications: [],
        sessions: [],
        latencyMilliseconds: nil,
        uptime: nil,
        lastSynchronizedAt: nil,
        isShowingCachedMetadata: false
    )
}
