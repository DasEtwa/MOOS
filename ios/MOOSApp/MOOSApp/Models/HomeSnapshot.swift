import Foundation

struct HomeSnapshot: Equatable, Sendable {
    let host: Host?
    let personalSystems: [PersonalSystem]
    let connectionState: ConnectionState
    let widgets: [ShellWidget]
    let applications: [ShellApp]
    let sessions: [AppSession]
    let latencyMilliseconds: Int?
    let uptime: SynchronizedUptime?
    let lastSynchronizedAt: Date?
    let isShowingCachedMetadata: Bool
    let failureMessage: String?

    static let noHost = HomeSnapshot(
        host: nil,
        personalSystems: [],
        connectionState: .noHost,
        widgets: [],
        applications: [],
        sessions: [],
        latencyMilliseconds: nil,
        uptime: nil,
        lastSynchronizedAt: nil,
        isShowingCachedMetadata: false,
        failureMessage: nil
    )

    static let placeholder = noHost

    static func connecting(to configuration: HostConfiguration) -> HomeSnapshot {
        remote(configuration: configuration, state: .connecting)
    }

    static func reconnecting(
        to configuration: HostConfiguration,
        personalSystems: [PersonalSystem]
    ) -> HomeSnapshot {
        remote(
            configuration: configuration,
            state: .reconnecting,
            personalSystems: personalSystems
        )
    }

    static func connected(
        to configuration: HostConfiguration,
        personalSystems: [PersonalSystem],
        synchronizedAt: Date = .now
    ) -> HomeSnapshot {
        remote(
            configuration: configuration,
            state: .connected,
            personalSystems: personalSystems,
            synchronizedAt: synchronizedAt
        )
    }

    static func disconnected(
        from configuration: HostConfiguration,
        personalSystems: [PersonalSystem] = [],
        message: String
    ) -> HomeSnapshot {
        remote(
            configuration: configuration,
            state: .disconnected,
            personalSystems: personalSystems,
            failureMessage: message
        )
    }

    func replacingHost(_ host: Host) -> HomeSnapshot {
        HomeSnapshot(
            host: host,
            personalSystems: personalSystems,
            connectionState: connectionState,
            widgets: widgets,
            applications: applications,
            sessions: sessions,
            latencyMilliseconds: latencyMilliseconds,
            uptime: uptime,
            lastSynchronizedAt: lastSynchronizedAt,
            isShowingCachedMetadata: isShowingCachedMetadata,
            failureMessage: failureMessage
        )
    }

    func replacingPersonalSystem(_ replacement: PersonalSystem) -> HomeSnapshot {
        HomeSnapshot(
            host: host,
            personalSystems: personalSystems.map {
                $0.id == replacement.id ? replacement : $0
            },
            connectionState: connectionState,
            widgets: widgets,
            applications: applications,
            sessions: sessions,
            latencyMilliseconds: latencyMilliseconds,
            uptime: uptime,
            lastSynchronizedAt: lastSynchronizedAt,
            isShowingCachedMetadata: isShowingCachedMetadata,
            failureMessage: failureMessage
        )
    }

    private static func remote(
        configuration: HostConfiguration,
        state: ConnectionState,
        personalSystems: [PersonalSystem] = [],
        synchronizedAt: Date? = nil,
        failureMessage: String? = nil
    ) -> HomeSnapshot {
        HomeSnapshot(
            host: configuration.host,
            personalSystems: personalSystems,
            connectionState: state,
            widgets: [],
            applications: [],
            sessions: [],
            latencyMilliseconds: nil,
            uptime: nil,
            lastSynchronizedAt: synchronizedAt,
            isShowingCachedMetadata: false,
            failureMessage: failureMessage
        )
    }
}
