struct MockMOOSStateService: MOOSStateProviding {
    func homeSnapshot() async -> HomeSnapshot {
        HomeSnapshot(
            host: Host(id: "studio-host", displayName: "Studio Host"),
            personalSystem: PersonalSystem(
                id: "personal",
                displayName: "Personal MOOS",
                state: .running
            ),
            connectionState: .connected,
            widgets: [
                ShellWidget(
                    id: "luna",
                    kind: .agent,
                    title: "Luna",
                    value: "Ready",
                    detail: "Agent · goal idle",
                    symbolName: "moon.stars",
                    progress: nil
                ),
                ShellWidget(
                    id: "sol",
                    kind: .agent,
                    title: "Sol",
                    value: "Working",
                    detail: "Agent · shell concept",
                    symbolName: "sun.max",
                    progress: nil
                ),
                ShellWidget(
                    id: "cpu",
                    kind: .metric,
                    title: "CPU",
                    value: "34%",
                    detail: "2 virtual cores",
                    symbolName: "cpu",
                    progress: 0.34
                ),
                ShellWidget(
                    id: "ram",
                    kind: .metric,
                    title: "RAM",
                    value: "1.2 GB",
                    detail: "of 2 GB",
                    symbolName: "memorychip",
                    progress: 0.60
                ),
                ShellWidget(
                    id: "disk",
                    kind: .metric,
                    title: "Disk",
                    value: "6 GB",
                    detail: "of 60 GB",
                    symbolName: "internaldrive",
                    progress: 0.10
                ),
                ShellWidget(
                    id: "goal-runtime",
                    kind: .goal,
                    title: "Goal runtime",
                    value: "18m",
                    detail: "Current mock session",
                    symbolName: "scope",
                    progress: nil
                ),
            ],
            applications: [
                ShellApp(
                    id: "blender",
                    name: "Blender",
                    symbolName: "cube.transparent",
                    destination: .blender
                ),
                ShellApp(
                    id: "discord",
                    name: "Discord",
                    symbolName: "bubble.left.and.bubble.right",
                    destination: .discord
                ),
                ShellApp(
                    id: "terminal",
                    name: "Terminal",
                    symbolName: "terminal",
                    destination: .terminal
                ),
                ShellApp(
                    id: "files",
                    name: "Files",
                    symbolName: "folder",
                    destination: .files
                ),
                ShellApp(
                    id: "settings",
                    name: "Settings",
                    symbolName: "gearshape",
                    destination: .settings
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
            ]
        )
    }
}
