import Foundation

actor LiveMOOSStateService: MOOSStateProviding {
    private let store: any HostConfigurationStoring
    private let credentialStore: any DeviceCredentialStoring
    private let instanceNameStore: any InstanceNameStoring
    private let connector: any MOOSConnectionConnecting
    private let metadataCache: any RemoteMetadataCaching
    private let preferencesStore: any LocalPreferencesStoring
    private let pollIntervalNanoseconds: UInt64
    private let gatewayClientNonce: Data?
    private var snapshot: HomeSnapshot
    private var cachedMetadata: CachedRemoteMetadata?
    private var continuations: [UInt64: AsyncStream<HomeSnapshot>.Continuation] = [:]
    private var nextSubscriberID: UInt64 = 0
    private var connection: (any MOOSConnection)?
    private var monitorTask: Task<Void, Never>?
    private var generation = 0
    private var hasStarted = false
    private var isApplicationActive = true

    init(
        store: any HostConfigurationStoring,
        credentialStore: any DeviceCredentialStoring,
        instanceNameStore: any InstanceNameStoring = UserDefaultsInstanceNameStore(),
        connector: any MOOSConnectionConnecting,
        metadataCache: any RemoteMetadataCaching = EmptyRemoteMetadataCache(),
        preferencesStore: any LocalPreferencesStoring = UserDefaultsLocalPreferencesStore(),
        pollIntervalNanoseconds: UInt64 = 5_000_000_000,
        gatewayClientNonce: Data? = nil
    ) {
        self.store = store
        self.credentialStore = credentialStore
        self.instanceNameStore = instanceNameStore
        self.connector = connector
        self.metadataCache = metadataCache
        self.preferencesStore = preferencesStore
        self.pollIntervalNanoseconds = pollIntervalNanoseconds
        self.gatewayClientNonce = gatewayClientNonce
        self.cachedMetadata = nil
        if let configuredHost = try? store.load() {
            snapshot = .connecting(to: configuredHost)
        } else {
            snapshot = .noHost
        }
    }

    nonisolated func snapshots() -> AsyncStream<HomeSnapshot> {
        AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            Task {
                await self.attach(continuation)
            }
        }
    }

    func configureHost(
        _ configuration: HostConfiguration,
        deviceKey: Data?
    ) async -> Bool {
        if let deviceKey, deviceKey.count != MOOSGatewayV1.deviceKeyBytes {
            snapshot = .disconnected(
                from: configuration,
                message: "The pairing credential is invalid."
            )
            emit()
            return false
        }
        let previousSnapshot = snapshot
        let previousDeviceID = snapshot.host?.deviceID
        let previousConfiguration: HostConfiguration?
        do {
            previousConfiguration = try store.load()
        } catch {
            snapshot = previousSnapshot
            emit()
            return false
        }
        var replacedCredential: (deviceID: String, key: Data?)?
        var configurationWasSaved = false
        do {
            if let deviceKey {
                let priorKey = try credentialStore.loadKey(
                    deviceID: configuration.deviceID
                )
                try credentialStore.saveKey(deviceKey, deviceID: configuration.deviceID)
                replacedCredential = (configuration.deviceID, priorKey)
            } else if try credentialStore.loadKey(deviceID: configuration.deviceID) == nil {
                snapshot = .disconnected(
                    from: configuration,
                    message: "The pairing credential is missing."
                )
                emit()
                return false
            }
            try store.save(configuration)
            configurationWasSaved = true
            if let previousDeviceID, previousDeviceID != configuration.deviceID {
                try credentialStore.removeKey(deviceID: previousDeviceID)
            }
        } catch {
            let rolledBack = rollbackHostChange(
                configurationWasSaved: configurationWasSaved,
                previousConfiguration: previousConfiguration,
                replacedCredential: replacedCredential
            )
            snapshot = rolledBack
                ? previousSnapshot
                : .disconnected(
                    from: configuration,
                    message: "The host settings failed and credential cleanup must be retried."
                )
            emit()
            return false
        }
        await beginConnection(to: configuration)
        return true
    }

    func retry() async {
        guard let configuration = configuration(from: snapshot.host) else {
            return
        }
        await beginConnection(to: configuration)
    }

    func removeHost() async {
        generation += 1
        monitorTask?.cancel()
        monitorTask = nil
        if let connection {
            await connection.close()
        }
        connection = nil
        cachedMetadata = nil
        do {
            if let deviceID = snapshot.host?.deviceID {
                try credentialStore.removeKey(deviceID: deviceID)
            }
            try store.save(nil)
            snapshot = .noHost
        } catch {
            if let configuration = configuration(from: snapshot.host) {
                snapshot = .disconnected(
                    from: configuration,
                    message: "The host settings or credential could not be removed. Retry removal."
                )
            }
        }
        emit()
    }

    func renameHost(to displayName: String) async -> Bool {
        guard let normalized = MOOSDisplayName.normalized(displayName),
              let configuration = configuration(from: snapshot.host) else {
            return false
        }
        do {
            let renamed = try HostConfiguration(
                address: configuration.address,
                port: configuration.port,
                deviceID: configuration.deviceID,
                displayName: normalized
            )
            try store.save(renamed)
            snapshot = snapshot.replacingHost(renamed.host)
            emit()
            return true
        } catch {
            return false
        }
    }

    func renamePersonalSystem(id: String, to displayName: String) async -> Bool {
        guard let normalized = MOOSDisplayName.normalized(displayName),
              let hostID = snapshot.host?.deviceID,
              let system = snapshot.personalSystems.first(where: { $0.id == id }) else {
            return false
        }
        do {
            try instanceNameStore.saveName(
                normalized,
                hostID: hostID,
                instanceID: id
            )
            snapshot = snapshot.replacingPersonalSystem(
                PersonalSystem(
                    id: system.id,
                    displayName: normalized,
                    state: system.state,
                    capabilities: system.capabilities
                )
            )
            emit()
            return true
        } catch {
            return false
        }
    }

    func applicationDidEnterBackground() async {
        guard isApplicationActive else {
            return
        }
        isApplicationActive = false
        generation += 1
        monitorTask?.cancel()
        monitorTask = nil
        let activeConnection = connection
        connection = nil
        if let configuration = configuration(from: snapshot.host) {
            snapshot = snapshotFor(
                configuration: configuration,
                state: .reconnecting,
                personalSystem: snapshot.personalSystems.first
            )
            emit()
        }
        if let activeConnection {
            await activeConnection.close()
        }
    }

    func applicationDidBecomeActive() async {
        guard !isApplicationActive else {
            return
        }
        isApplicationActive = true
        guard let configuration = configuration(from: snapshot.host) else {
            return
        }
        await beginConnection(to: configuration)
    }

    private func attach(_ continuation: AsyncStream<HomeSnapshot>.Continuation) {
        nextSubscriberID &+= 1
        let subscriberID = nextSubscriberID
        continuation.onTermination = { [weak self] _ in
            Task { [weak self] in
                await self?.detach(subscriberID: subscriberID)
            }
        }
        continuations[subscriberID] = continuation
        continuation.yield(snapshot)
        guard isApplicationActive,
              !hasStarted,
              let configuration = configuration(from: snapshot.host) else {
            return
        }
        hasStarted = true
        Task {
            await self.beginConnection(to: configuration)
        }
    }

    private func detach(subscriberID: UInt64) {
        continuations.removeValue(forKey: subscriberID)
    }

    private func beginConnection(to configuration: HostConfiguration) async {
        guard isApplicationActive else {
            return
        }
        hasStarted = true
        generation += 1
        let attempt = generation
        monitorTask?.cancel()
        monitorTask = nil
        if let connection {
            await connection.close()
        }
        connection = nil
        cachedMetadata = loadCachedMetadata(for: configuration)
        snapshot = snapshotFor(
            configuration: configuration,
            state: .connecting,
            personalSystem: snapshot.personalSystems.first
        )
        emit()

        var pendingConnection: (any MOOSConnection)?
        do {
            guard let key = try credentialStore.loadKey(
                deviceID: configuration.deviceID
            ), key.count == MOOSGatewayV1.deviceKeyBytes else {
                throw MOOSGatewayV1.ClientError.authenticationFailed
            }
            let connection = try await connector.connect(to: configuration)
            pendingConnection = connection
            guard attempt == generation else {
                await connection.close()
                return
            }
            try await MOOSGatewayV1.authenticate(
                connection: connection,
                credential: .init(deviceID: configuration.deviceID, key: key),
                clientNonce: gatewayClientNonce
            )
            let client = MOOSProtocolV1Client(connection: connection)
            let response = try await client.status()
            guard attempt == generation else {
                await client.close()
                return
            }
            let personalSystem = displayed(
                response.personalSystem,
                for: configuration
            )
            self.connection = connection
            snapshot = snapshotFor(
                configuration: configuration,
                state: .connected,
                personalSystem: personalSystem,
                synchronizedAt: .now
            )
            emit()
            monitorTask = Task {
                await self.monitor(client: client, configuration: configuration, attempt: attempt)
            }
        } catch {
            if let pendingConnection {
                await pendingConnection.close()
            }
            guard attempt == generation else {
                return
            }
            snapshot = snapshotFor(
                configuration: configuration,
                state: .disconnected,
                personalSystem: snapshot.personalSystems.first,
                failureMessage: Self.userFacingMessage(for: error)
            )
            emit()
        }
    }

    private func monitor(
        client: MOOSProtocolV1Client,
        configuration: HostConfiguration,
        attempt: Int
    ) async {
        while !Task.isCancelled, attempt == generation {
            do {
                try await Task.sleep(nanoseconds: pollIntervalNanoseconds)
                guard !Task.isCancelled, attempt == generation else {
                    return
                }
                let response = try await client.status()
                snapshot = snapshotFor(
                    configuration: configuration,
                    state: .connected,
                    personalSystem: displayed(
                        response.personalSystem,
                        for: configuration
                    ),
                    synchronizedAt: .now
                )
                emit()
            } catch is CancellationError {
                return
            } catch {
                guard attempt == generation else {
                    return
                }
                await client.close()
                connection = nil
                snapshot = snapshotFor(
                    configuration: configuration,
                    state: .disconnected,
                    personalSystem: snapshot.personalSystems.first,
                    failureMessage: Self.userFacingMessage(for: error)
                )
                emit()
                return
            }
        }
    }

    private func snapshotFor(
        configuration: HostConfiguration,
        state: ConnectionState,
        personalSystem: PersonalSystem? = nil,
        synchronizedAt: Date? = nil,
        failureMessage: String? = nil
    ) -> HomeSnapshot {
        guard let metadata = cachedMetadata else {
            switch state {
            case .noHost:
                return .noHost
            case .connected:
                return .connected(
                    to: configuration,
                    personalSystems: personalSystem.map { [$0] } ?? [],
                    synchronizedAt: synchronizedAt ?? .now
                )
            case .connecting:
                return .connecting(to: configuration)
            case .reconnecting:
                return .reconnecting(
                    to: configuration,
                    personalSystems: personalSystem.map { [$0] }
                        ?? snapshot.personalSystems
                )
            case .disconnected, .offline:
                return .disconnected(
                    from: configuration,
                    personalSystems: personalSystem.map { [$0] }
                        ?? snapshot.personalSystems,
                    message: failureMessage ?? "The MOOS Host is unavailable."
                )
            }
        }

        let displayedSystem = personalSystem
            ?? snapshot.personalSystems.first
            ?? PersonalSystem(
                id: metadata.personalSystemID,
                displayName: metadata.personalSystemName,
                state: .unknown
            )
        let liveState = LiveMOOSState(
            connectionState: state,
            personalSystemState: displayedSystem.state,
            widgets: [:],
            sessions: [],
            latencyMilliseconds: nil,
            uptime: snapshot.uptime,
            synchronizedAt: synchronizedAt ?? snapshot.lastSynchronizedAt
        )
        return HomeSnapshotComposer.compose(
            metadata: metadata,
            liveState: liveState,
            preferences: currentPreferences(),
            isShowingCachedMetadata: state != .connected,
            host: configuration.host,
            personalSystem: displayedSystem,
            failureMessage: failureMessage
        )
    }

    private func loadCachedMetadata(
        for configuration: HostConfiguration
    ) -> CachedRemoteMetadata? {
        do {
            guard let metadata = try metadataCache.load() else {
                return nil
            }
            let sameEndpoint = metadata.host.address == configuration.address
                && metadata.host.port == configuration.port
            guard metadata.host.id == configuration.host.id || sameEndpoint else {
                return nil
            }
            if let cachedDeviceID = metadata.host.deviceID,
               cachedDeviceID != configuration.deviceID {
                return nil
            }
            return metadata
        } catch {
            return nil
        }
    }

    private func currentPreferences() -> LocalShellPreferences {
        (try? preferencesStore.load()) ?? .defaultValue
    }

    private func emit() {
        let subscribers = continuations
        for (subscriberID, continuation) in subscribers {
            switch continuation.yield(snapshot) {
            case .enqueued, .dropped:
                break
            case .terminated:
                continuations.removeValue(forKey: subscriberID)
            @unknown default:
                continuations.removeValue(forKey: subscriberID)
            }
        }
    }

    private func rollbackHostChange(
        configurationWasSaved: Bool,
        previousConfiguration: HostConfiguration?,
        replacedCredential: (deviceID: String, key: Data?)?
    ) -> Bool {
        var succeeded = true
        if configurationWasSaved {
            do {
                try store.save(previousConfiguration)
            } catch {
                succeeded = false
            }
        }
        if let replacedCredential {
            do {
                if let priorKey = replacedCredential.key {
                    try credentialStore.saveKey(
                        priorKey,
                        deviceID: replacedCredential.deviceID
                    )
                } else {
                    try credentialStore.removeKey(
                        deviceID: replacedCredential.deviceID
                    )
                }
            } catch {
                succeeded = false
            }
        }
        return succeeded
    }

    private func configuration(from host: Host?) -> HostConfiguration? {
        guard let address = host?.address,
              let port = host?.port,
              let deviceID = host?.deviceID else {
            return nil
        }
        return try? HostConfiguration(
            address: address,
            port: port,
            deviceID: deviceID,
            displayName: host?.displayName
        )
    }

    private func displayed(
        _ system: PersonalSystem,
        for configuration: HostConfiguration
    ) -> PersonalSystem {
        let alias = try? instanceNameStore.loadName(
            hostID: configuration.deviceID,
            instanceID: system.id
        )
        return PersonalSystem(
            id: system.id,
            displayName: alias ?? system.displayName,
            state: system.state,
            capabilities: system.capabilities
        )
    }

    private static func userFacingMessage(for error: Error) -> String {
        if let localized = error as? LocalizedError,
           let description = localized.errorDescription {
            return description
        }
        return "The MOOS Host connection failed."
    }
}
