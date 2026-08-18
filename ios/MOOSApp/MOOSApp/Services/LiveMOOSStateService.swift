import Foundation

actor LiveMOOSStateService: MOOSStateProviding {
    private let store: any HostConfigurationStoring
    private let credentialStore: any DeviceCredentialStoring
    private let connector: any MOOSConnectionConnecting
    private let pollIntervalNanoseconds: UInt64
    private let gatewayClientNonce: Data?
    private var snapshot: HomeSnapshot
    private var continuation: AsyncStream<HomeSnapshot>.Continuation?
    private var connection: (any MOOSConnection)?
    private var monitorTask: Task<Void, Never>?
    private var generation = 0
    private var hasStarted = false

    init(
        store: any HostConfigurationStoring,
        credentialStore: any DeviceCredentialStoring,
        connector: any MOOSConnectionConnecting,
        pollIntervalNanoseconds: UInt64 = 5_000_000_000,
        gatewayClientNonce: Data? = nil
    ) {
        self.store = store
        self.credentialStore = credentialStore
        self.connector = connector
        self.pollIntervalNanoseconds = pollIntervalNanoseconds
        self.gatewayClientNonce = gatewayClientNonce
        if let configuredHost = try? store.load() {
            snapshot = .connecting(to: configuredHost)
        } else {
            snapshot = .noHost
        }
    }

    nonisolated func snapshots() -> AsyncStream<HomeSnapshot> {
        AsyncStream { continuation in
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

    private func attach(_ continuation: AsyncStream<HomeSnapshot>.Continuation) {
        self.continuation?.finish()
        self.continuation = continuation
        continuation.yield(snapshot)
        guard !hasStarted,
              let configuration = configuration(from: snapshot.host) else {
            return
        }
        hasStarted = true
        Task {
            await self.beginConnection(to: configuration)
        }
    }

    private func beginConnection(to configuration: HostConfiguration) async {
        hasStarted = true
        generation += 1
        let attempt = generation
        monitorTask?.cancel()
        monitorTask = nil
        if let connection {
            await connection.close()
        }
        connection = nil
        snapshot = .connecting(to: configuration)
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
            self.connection = connection
            snapshot = .connected(
                to: configuration,
                personalSystems: [response.personalSystem]
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
            snapshot = .disconnected(
                from: configuration,
                message: Self.userFacingMessage(for: error)
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
                snapshot = .connected(
                    to: configuration,
                    personalSystems: [response.personalSystem]
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
                snapshot = .disconnected(
                    from: configuration,
                    personalSystems: snapshot.personalSystems,
                    message: Self.userFacingMessage(for: error)
                )
                emit()
                return
            }
        }
    }

    private func emit() {
        continuation?.yield(snapshot)
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
            deviceID: deviceID
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
