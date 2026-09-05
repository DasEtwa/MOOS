enum HomeSnapshotComposer {
    static func compose(
        metadata: CachedRemoteMetadata,
        liveState: LiveMOOSState,
        preferences: LocalShellPreferences = .defaultValue,
        isShowingCachedMetadata: Bool,
        host: Host? = nil,
        personalSystem: PersonalSystem? = nil,
        failureMessage: String? = nil
    ) -> HomeSnapshot {
        let widgets = metadata.widgets.map { metadata in
            let liveValue = liveState.widgets[metadata.id]
            return ShellWidget(
                metadata: metadata,
                value: liveValue?.value ?? "—",
                detail: liveValue?.detail ?? "Waiting for live state",
                progress: liveValue?.progress.map { min(max($0, 0), 1) }
            )
        }

        return HomeSnapshot(
            host: host ?? metadata.host,
            personalSystems: [personalSystem ?? PersonalSystem(
                id: metadata.personalSystemID,
                displayName: metadata.personalSystemName,
                state: liveState.personalSystemState
            )],
            connectionState: liveState.connectionState,
            widgets: widgets,
            applications: arrange(
                metadata.applications,
                preferredIDs: preferences.appArrangement
            ),
            sessions: liveState.sessions,
            latencyMilliseconds: liveState.latencyMilliseconds,
            uptime: liveState.uptime,
            lastSynchronizedAt: liveState.synchronizedAt,
            isShowingCachedMetadata: isShowingCachedMetadata,
            failureMessage: failureMessage
        )
    }

    private static func arrange(
        _ applications: [ShellApp],
        preferredIDs: [String]
    ) -> [ShellApp] {
        var ranks: [String: Int] = [:]
        for id in preferredIDs where ranks[id] == nil {
            ranks[id] = ranks.count
        }

        return applications.enumerated()
            .sorted { left, right in
                let leftRank = ranks[left.element.id] ?? (ranks.count + left.offset)
                let rightRank = ranks[right.element.id] ?? (ranks.count + right.offset)
                return leftRank < rightRank
            }
            .map(\.element)
    }
}
