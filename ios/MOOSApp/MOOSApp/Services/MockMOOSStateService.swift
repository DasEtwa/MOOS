import Foundation

enum MockConnectionScenario: Equatable, Sendable {
    case connected
    case reconnecting
    case offline
}

struct MockMOOSStateService: MOOSStateProviding {
    private let scenario: MockConnectionScenario
    private let now: Date
    private let preferences: LocalShellPreferences

    init(
        scenario: MockConnectionScenario = .connected,
        now: Date = .now,
        preferences: LocalShellPreferences = .defaultValue
    ) {
        self.scenario = scenario
        self.now = now
        self.preferences = preferences
    }

    func snapshots() -> AsyncStream<HomeSnapshot> {
        let snapshot = HomeSnapshotComposer.compose(
            metadata: Self.metadata(updatedAt: now.addingTimeInterval(-75)),
            liveState: liveState,
            preferences: preferences,
            isShowingCachedMetadata: scenario != .connected
        )

        return AsyncStream { continuation in
            continuation.yield(snapshot)
            continuation.finish()
        }
    }

    private var liveState: LiveMOOSState {
        let connectionState: ConnectionState
        let latency: Int?
        switch scenario {
        case .connected:
            connectionState = .connected
            latency = 18
        case .reconnecting:
            connectionState = .reconnecting
            latency = nil
        case .offline:
            connectionState = .offline
            latency = nil
        }

        let synchronizedAt = now.addingTimeInterval(-75)
        return LiveMOOSState(
            connectionState: connectionState,
            personalSystemState: .running,
            widgets: [
                "luna": LiveWidgetValue(
                    value: "Ready",
                    detail: "Agent · goal idle",
                    progress: nil
                ),
                "sol": LiveWidgetValue(
                    value: "Working",
                    detail: "Agent · shell concept",
                    progress: nil
                ),
                "cpu": LiveWidgetValue(
                    value: "34%",
                    detail: "2 virtual cores",
                    progress: 0.34
                ),
                "ram": LiveWidgetValue(
                    value: "1.2 GB",
                    detail: "of 2 GB",
                    progress: 0.60
                ),
                "disk": LiveWidgetValue(
                    value: "6 GB",
                    detail: "of 60 GB",
                    progress: 0.10
                ),
                "goal-runtime": LiveWidgetValue(
                    value: "18m",
                    detail: "Current mock session",
                    progress: nil
                ),
            ],
            sessions: [
                AppSession(
                    id: "terminal-main",
                    applicationID: "terminal",
                    displayName: "Terminal",
                    symbolName: "terminal",
                    state: .active
                ),
                AppSession(
                    id: "files-main",
                    applicationID: "files",
                    displayName: "Files",
                    symbolName: "folder",
                    state: .background
                ),
            ],
            latencyMilliseconds: latency,
            uptime: SynchronizedUptime(
                secondsAtSynchronization: 18_000,
                synchronizedAt: synchronizedAt
            ),
            synchronizedAt: synchronizedAt
        )
    }

    static func metadata(updatedAt: Date) -> CachedRemoteMetadata {
        CachedRemoteMetadata(
            host: Host(id: "studio-host", displayName: "Studio Host"),
            personalSystemID: "personal",
            personalSystemName: "Personal MOOS",
            applications: [
                ShellApp(
                    id: "blender",
                    name: "Blender",
                    icon: AppIconMetadata(
                        fallbackSymbolName: "cube.transparent",
                        contentHash: "mock-icon-blender-v1"
                    ),
                    destination: .blender
                ),
                ShellApp(
                    id: "discord",
                    name: "Discord",
                    icon: AppIconMetadata(
                        fallbackSymbolName: "bubble.left.and.bubble.right",
                        contentHash: "mock-icon-discord-v1"
                    ),
                    destination: .discord
                ),
                ShellApp(
                    id: "terminal",
                    name: "Terminal",
                    icon: AppIconMetadata(
                        fallbackSymbolName: "terminal",
                        contentHash: nil
                    ),
                    destination: .terminal
                ),
                ShellApp(
                    id: "files",
                    name: "Files",
                    icon: AppIconMetadata(
                        fallbackSymbolName: "folder",
                        contentHash: nil
                    ),
                    destination: .files
                ),
                ShellApp(
                    id: "settings",
                    name: "Settings",
                    icon: AppIconMetadata(
                        fallbackSymbolName: "gearshape",
                        contentHash: nil
                    ),
                    destination: .settings
                ),
            ],
            widgets: [
                WidgetMetadata(
                    id: "luna",
                    kind: .agent,
                    title: "Luna",
                    symbolName: "moon.stars"
                ),
                WidgetMetadata(
                    id: "sol",
                    kind: .agent,
                    title: "Sol",
                    symbolName: "sun.max"
                ),
                WidgetMetadata(
                    id: "cpu",
                    kind: .metric,
                    title: "CPU",
                    symbolName: "cpu"
                ),
                WidgetMetadata(
                    id: "ram",
                    kind: .metric,
                    title: "RAM",
                    symbolName: "memorychip"
                ),
                WidgetMetadata(
                    id: "disk",
                    kind: .metric,
                    title: "Disk",
                    symbolName: "internaldrive"
                ),
                WidgetMetadata(
                    id: "goal-runtime",
                    kind: .goal,
                    title: "Goal runtime",
                    symbolName: "scope"
                ),
            ],
            updatedAt: updatedAt
        )
    }
}
