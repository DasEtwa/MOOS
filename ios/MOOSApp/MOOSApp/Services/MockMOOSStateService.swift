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
            applications: [
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
                    id: "apps",
                    name: "Apps",
                    symbolName: "square.grid.2x2",
                    destination: .apps
                ),
                ShellApp(
                    id: "settings",
                    name: "Settings",
                    symbolName: "gearshape",
                    destination: .settings
                ),
            ]
        )
    }
}
