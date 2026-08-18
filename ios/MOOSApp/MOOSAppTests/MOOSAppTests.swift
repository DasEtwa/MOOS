import Foundation
import XCTest
@testable import MOOSApp

final class MOOSAppTests: XCTestCase {
    func testConnectionStateLabelsAreClientFacing() {
        XCTAssertEqual(ConnectionState.noHost.label, "No Host")
        XCTAssertEqual(ConnectionState.connected.label, "Connected")
        XCTAssertEqual(ConnectionState.reconnecting.label, "Reconnecting")
        XCTAssertEqual(ConnectionState.disconnected.label, "Disconnected")
        XCTAssertFalse(ConnectionState.offline.isReachable)
    }

    func testMockSnapshotUsesStablePersonalIdentity() async {
        let snapshot = await firstSnapshot(from: MockMOOSStateService())

        XCTAssertEqual(snapshot.personalSystems.first?.id, "personal")
        XCTAssertEqual(snapshot.personalSystems.first?.state, .running)
        XCTAssertEqual(snapshot.connectionState, .connected)
        XCTAssertEqual(snapshot.applications.map(\.id), [
            "blender", "discord", "terminal", "files", "settings",
        ])
        XCTAssertEqual(snapshot.widgets.count, 6)
        XCTAssertEqual(snapshot.sessions.count, 2)
        XCTAssertEqual(snapshot.latencyMilliseconds, 18)
        XCTAssertFalse(snapshot.isShowingCachedMetadata)
    }

    func testOfflineSnapshotRetainsCachedDesktop() async {
        let snapshot = await firstSnapshot(
            from: MockMOOSStateService(scenario: .offline)
        )

        XCTAssertEqual(snapshot.connectionState, .offline)
        XCTAssertTrue(snapshot.isShowingCachedMetadata)
        XCTAssertFalse(snapshot.applications.isEmpty)
        XCTAssertEqual(snapshot.widgets.first { $0.id == "cpu" }?.value, "34%")
        XCTAssertNotNil(snapshot.lastSynchronizedAt)
    }

    func testSynchronizedUptimeAdvancesLocally() {
        let synchronizedAt = Date(timeIntervalSince1970: 1_000)
        let uptime = SynchronizedUptime(
            secondsAtSynchronization: 120,
            synchronizedAt: synchronizedAt
        )

        XCTAssertEqual(
            uptime.seconds(at: synchronizedAt.addingTimeInterval(42)),
            162,
            accuracy: 0.001
        )
    }

    func testRemoteMetadataCacheRoundTripIsVersionedAndAtomic() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let cache = DiskRemoteMetadataCache(
            fileURL: directory.appendingPathComponent("metadata.json")
        )
        let metadata = MockMOOSStateService.metadata(
            updatedAt: Date(timeIntervalSince1970: 1_000)
        )

        try cache.save(metadata)

        XCTAssertEqual(try cache.load(), metadata)
        XCTAssertEqual(metadata.schemaVersion, CachedRemoteMetadata.currentSchemaVersion)
        XCTAssertEqual(metadata.applications.first?.icon.contentHash, "mock-icon-blender-v1")
    }

    func testLocalPreferencesRoundTrip() throws {
        let suiteName = "dev.moos.shell.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = UserDefaultsLocalPreferencesStore(defaults: defaults)
        var preferences = LocalShellPreferences.defaultValue
        preferences.layoutDensity = .compact
        preferences.animationsEnabled = false

        try store.save(preferences)

        XCTAssertEqual(try store.load(), preferences)
    }

    func testLocalAppArrangementIsAppliedDuringComposition() async {
        var preferences = LocalShellPreferences.defaultValue
        preferences.appArrangement = ["settings", "terminal"]
        let snapshot = await firstSnapshot(
            from: MockMOOSStateService(preferences: preferences)
        )

        XCTAssertEqual(Array(snapshot.applications.prefix(2).map(\.id)), [
            "settings", "terminal",
        ])
    }

    func testProtocolV1RequestRemainsTransportIndependent() throws {
        let request = MOOSProtocolV1.ControlRequest(operation: .personalStatus)
        let encoded = try JSONEncoder().encode(request)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        )

        XCTAssertEqual(object["protocolVersion"] as? Int, 1)
        XCTAssertEqual(object["operation"] as? String, "personal.status")
    }

    func testNoHostInitialStateContainsNoProductionFallbackData() async throws {
        let defaults = try temporaryDefaults()
        let connector = TestConnector(result: .failure(.unreachable))
        let service = LiveMOOSStateService(
            store: UserDefaultsHostConfigurationStore(defaults: defaults),
            credentialStore: InMemoryDeviceCredentialStore(),
            connector: connector
        )

        let snapshot = await firstSnapshot(from: service)

        XCTAssertEqual(snapshot.connectionState, .noHost)
        XCTAssertNil(snapshot.host)
        XCTAssertTrue(snapshot.personalSystems.isEmpty)
        XCTAssertTrue(snapshot.widgets.isEmpty)
        XCTAssertTrue(snapshot.applications.isEmpty)
        XCTAssertEqual(connector.connectionAttempts, 0)
    }

    func testHostConfigurationPersistsAcrossStoreInstances() throws {
        let defaults = try temporaryDefaults()
        let configuration = try HostConfiguration(
            address: "100.64.0.22",
            port: 7411,
            deviceID: Self.deviceID
        )

        try UserDefaultsHostConfigurationStore(defaults: defaults).save(configuration)

        XCTAssertEqual(
            try UserDefaultsHostConfigurationStore(defaults: defaults).load(),
            configuration
        )
    }

    func testHostConfigurationRequiresLiteralTailscaleAddress() throws {
        for address in [
            "100.64.0.1",
            "100.127.255.254",
            "fd7a:115c:a1e0::1",
        ] {
            XCTAssertNoThrow(
                try HostConfiguration(
                    address: address,
                    port: 7411,
                    deviceID: Self.deviceID
                )
            )
        }
        for address in [
            "100.63.255.255",
            "100.128.0.1",
            "127.0.0.1",
            "192.168.1.10",
            "my-host.tailnet.ts.net",
            "example.com",
        ] {
            XCTAssertThrowsError(
                try HostConfiguration(
                    address: address,
                    port: 7411,
                    deviceID: Self.deviceID
                )
            )
        }
    }

    func testFailedHostPersistenceRollsBackNewCredential() async throws {
        let configuration = try HostConfiguration(
            address: "100.64.0.20",
            port: 7411,
            deviceID: Self.deviceID
        )
        let credentials = InMemoryDeviceCredentialStore()
        let service = LiveMOOSStateService(
            store: FailingHostConfigurationStore(),
            credentialStore: credentials,
            connector: TestConnector(result: .failure(.unreachable))
        )

        let persisted = await service.configureHost(
            configuration,
            deviceKey: Self.deviceKey
        )

        XCTAssertFalse(persisted)
        XCTAssertNil(try credentials.loadKey(deviceID: Self.deviceID))
    }

    func testInvalidDeviceKeyIsRejectedBeforePersistenceOrConnection() async throws {
        let defaults = try temporaryDefaults()
        let credentials = InMemoryDeviceCredentialStore()
        let connector = TestConnector(result: .failure(.unreachable))
        let service = LiveMOOSStateService(
            store: UserDefaultsHostConfigurationStore(defaults: defaults),
            credentialStore: credentials,
            connector: connector
        )
        let configuration = try HostConfiguration(
            address: "100.64.0.23",
            port: 7411,
            deviceID: Self.deviceID
        )

        let persisted = await service.configureHost(
            configuration,
            deviceKey: Data(repeating: 7, count: 31)
        )

        XCTAssertFalse(persisted)
        XCTAssertNil(try credentials.loadKey(deviceID: Self.deviceID))
        XCTAssertNil(try UserDefaultsHostConfigurationStore(defaults: defaults).load())
        XCTAssertEqual(connector.connectionAttempts, 0)
    }

    func testValidProtocolV1StatusMapsIntoConnectedHomeState() async throws {
        let defaults = try temporaryDefaults()
        let configuration = try HostConfiguration(
            address: "100.64.0.10",
            port: 7411,
            deviceID: Self.deviceID
        )
        let store = UserDefaultsHostConfigurationStore(defaults: defaults)
        try store.save(configuration)
        let credentials = InMemoryDeviceCredentialStore()
        try credentials.saveKey(Self.deviceKey, deviceID: Self.deviceID)
        let connection = TestConnection(frames: [
            .success(Self.gatewayChallenge),
            .success(Self.gatewayAuthenticated),
            .success(Self.validStatusResponse),
        ])
        let service = LiveMOOSStateService(
            store: store,
            credentialStore: credentials,
            connector: TestConnector(result: .success(connection)),
            pollIntervalNanoseconds: UInt64.max,
            gatewayClientNonce: Data(repeating: 4, count: 32)
        )

        let snapshot = await firstSnapshot(from: service) {
            $0.connectionState == .connected
        }

        XCTAssertEqual(snapshot.connectionState, .connected)
        XCTAssertEqual(snapshot.host?.address, "100.64.0.10")
        XCTAssertEqual(snapshot.host?.port, 7411)
        XCTAssertEqual(snapshot.personalSystems, [
            PersonalSystem(id: "personal", displayName: "Personal MOOS", state: .running),
        ])
        XCTAssertTrue(snapshot.widgets.isEmpty)
        XCTAssertTrue(snapshot.applications.isEmpty)
        let sentFrames = connection.sentFrames
        XCTAssertEqual(sentFrames.count, 2)
        XCTAssertTrue(String(decoding: sentFrames[0], as: UTF8.self).contains(
            "\"type\":\"authenticate\""
        ))
        XCTAssertTrue(String(decoding: sentFrames[1], as: UTF8.self).contains(
            "\"operation\":\"status\""
        ))
    }

    func testGatewayPairingCodeParsesDeviceIdentityAndKey() throws {
        let credential = try MOOSGatewayV1.parsePairingCode(Self.pairingCode)

        XCTAssertEqual(credential.deviceID, Self.deviceID)
        XCTAssertEqual(credential.key, Self.deviceKey)
    }

    func testGatewayRejectsMalformedPairingCode() {
        XCTAssertThrowsError(try MOOSGatewayV1.parsePairingCode("not-a-pairing-code"))
    }

    func testGatewayAuthenticationProofMatchesHostVector() {
        let proof = MOOSGatewayV1.authenticationProof(
            key: Self.deviceKey,
            serverNonce: Data(repeating: 3, count: 32),
            clientNonce: Data(repeating: 4, count: 32),
            deviceID: Self.deviceID
        )

        XCTAssertEqual(
            proof.map { String(format: "%02x", $0) }.joined(),
            "fadb524047e066a7546f364eab2e10cbd5203e945c9ae44aaf1346c0ee72689b"
        )
        let serverProof = MOOSGatewayV1.serverAuthenticationProof(
            key: Self.deviceKey,
            serverNonce: Data(repeating: 3, count: 32),
            clientNonce: Data(repeating: 4, count: 32),
            deviceID: Self.deviceID
        )
        XCTAssertEqual(
            serverProof.map { String(format: "%02x", $0) }.joined(),
            "62674501e0aece3d3a6b34217c1eec59313614c6a68142d37c589459929f118f"
        )
    }

    func testGatewayAuthenticationFailureNeverPublishesConnectedState() async throws {
        let defaults = try temporaryDefaults()
        let configuration = try HostConfiguration(
            address: "100.64.0.11",
            port: 7411,
            deviceID: Self.deviceID
        )
        let store = UserDefaultsHostConfigurationStore(defaults: defaults)
        try store.save(configuration)
        let credentials = InMemoryDeviceCredentialStore()
        try credentials.saveKey(Self.deviceKey, deviceID: Self.deviceID)
        let connection = TestConnection(frames: [
            .success(Self.gatewayChallenge),
            .success(
                "{\"gatewayVersion\":1,\"type\":\"error\",\"code\":\"authentication_failed\",\"message\":\"Device authentication failed\"}"
            ),
        ])
        let service = LiveMOOSStateService(
            store: store,
            credentialStore: credentials,
            connector: TestConnector(result: .success(connection)),
            gatewayClientNonce: Data(repeating: 4, count: 32)
        )

        let snapshot = await firstSnapshot(from: service) {
            $0.connectionState == .disconnected
        }

        XCTAssertEqual(snapshot.connectionState, .disconnected)
        XCTAssertTrue(snapshot.personalSystems.isEmpty)
        XCTAssertEqual(connection.sentFrames.count, 1)
    }

    func testGatewayInvalidServerProofNeverPublishesConnectedState() async throws {
        let defaults = try temporaryDefaults()
        let configuration = try HostConfiguration(
            address: "100.64.0.12",
            port: 7411,
            deviceID: Self.deviceID
        )
        let store = UserDefaultsHostConfigurationStore(defaults: defaults)
        try store.save(configuration)
        let credentials = InMemoryDeviceCredentialStore()
        try credentials.saveKey(Self.deviceKey, deviceID: Self.deviceID)
        let invalidAuthentication = Self.gatewayAuthenticated.replacingOccurrences(
            of: Self.gatewayServerProof,
            with: Self.base64URL(Data(repeating: 0, count: 32))
        )
        let service = LiveMOOSStateService(
            store: store,
            credentialStore: credentials,
            connector: TestConnector(result: .success(TestConnection(frames: [
                .success(Self.gatewayChallenge),
                .success(invalidAuthentication),
            ]))),
            gatewayClientNonce: Data(repeating: 4, count: 32)
        )

        let snapshot = await firstSnapshot(from: service) {
            $0.connectionState == .disconnected
        }

        XCTAssertEqual(snapshot.connectionState, .disconnected)
        XCTAssertTrue(snapshot.personalSystems.isEmpty)
    }

    func testProtocolV1RejectsIncompatibleVersion() {
        let response = Self.validStatusResponse.replacingOccurrences(
            of: "\"protocolVersion\":1",
            with: "\"protocolVersion\":2"
        )

        XCTAssertThrowsError(
            try MOOSProtocolV1.parseStatusResponse(Data(response.utf8))
        ) { error in
            XCTAssertEqual(error as? MOOSProtocolV1.ClientError, .unsupportedProtocol)
        }
    }

    func testProtocolV1RejectsMalformedResponse() {
        let malformed = Data(
            "{\"protocolVersion\":1,\"ok\":true,\"operation\":\"status\",\"data\":{},\"events\":[]}".utf8
        )

        XCTAssertThrowsError(try MOOSProtocolV1.parseStatusResponse(malformed)) { error in
            guard let clientError = error as? MOOSProtocolV1.ClientError,
                  case .malformedResponse = clientError else {
                return XCTFail("Expected malformed Protocol v1 response, got \(error)")
            }
        }
    }

    func testConnectionFailureProducesDisconnectedState() async throws {
        let defaults = try temporaryDefaults()
        let configuration = try HostConfiguration(
            address: "100.64.0.99",
            port: 7411,
            deviceID: Self.deviceID
        )
        let store = UserDefaultsHostConfigurationStore(defaults: defaults)
        try store.save(configuration)
        let credentials = InMemoryDeviceCredentialStore()
        try credentials.saveKey(Self.deviceKey, deviceID: Self.deviceID)
        let service = LiveMOOSStateService(
            store: store,
            credentialStore: credentials,
            connector: TestConnector(result: .failure(.unreachable))
        )

        let snapshot = await firstSnapshot(from: service) {
            $0.connectionState == .disconnected
        }

        XCTAssertEqual(snapshot.connectionState, .disconnected)
        XCTAssertFalse(snapshot.failureMessage?.isEmpty ?? true)
        XCTAssertTrue(snapshot.personalSystems.isEmpty)
    }

    func testRadialMenuKeepsNavigationMetadataSeparateFromLayout() {
        let items = [RadialMenuItem].shellDefaults

        XCTAssertEqual(items.count, 6)
        XCTAssertEqual(Set(items.map(\.id)).count, items.count)
        XCTAssertTrue(items.contains { $0.destination == .terminal })
        XCTAssertTrue(items.contains { $0.destination == .settings })
    }

    func testSystemControlsAreFiniteLocalPlaceholders() {
        XCTAssertEqual(PowerMenuAction.allCases, [.lock, .reboot, .shutdown])
        XCTAssertEqual(RunningAppAction.forceClose.title, "Force close")
    }

    private func firstSnapshot(
        from service: some MOOSStateProviding
    ) async -> HomeSnapshot {
        var iterator = service.snapshots().makeAsyncIterator()
        return await iterator.next() ?? .placeholder
    }

    private func firstSnapshot(
        from service: some MOOSStateProviding,
        matching predicate: @escaping (HomeSnapshot) -> Bool
    ) async -> HomeSnapshot {
        for await snapshot in service.snapshots() where predicate(snapshot) {
            return snapshot
        }
        return .placeholder
    }

    private func temporaryDefaults() throws -> UserDefaults {
        let suiteName = "dev.moos.host.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defaults.removePersistentDomain(forName: suiteName)
        return defaults
    }

    private static let validStatusResponse = """
    {"protocolVersion":1,"ok":true,"operation":"status","data":{"personal":{"identity":"personal","state":"running","result":"success"}},"events":[]}
    """

    private static let deviceID = "00000000-0000-4000-8000-000000000001"
    private static let deviceKey = Data(repeating: 7, count: 32)
    private static var pairingCode: String {
        "moos-pair-v1:\(deviceID):\(base64URL(deviceKey))"
    }
    private static var gatewayChallenge: String {
        """
        {"gatewayVersion":1,"type":"challenge","nonce":"\(base64URL(Data(repeating: 3, count: 32)))"}
        """
    }
    private static var gatewayAuthenticated: String {
        """
        {"gatewayVersion":1,"type":"authenticated","deviceId":"\(deviceID)","permissions":["status"],"serverProof":"\(gatewayServerProof)"}
        """
    }
    private static var gatewayServerProof: String {
        base64URL(
            MOOSGatewayV1.serverAuthenticationProof(
                key: deviceKey,
                serverNonce: Data(repeating: 3, count: 32),
                clientNonce: Data(repeating: 4, count: 32),
                deviceID: deviceID
            )
        )
    }

    private static func base64URL(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

private enum TestConnectionError: Error, LocalizedError {
    case unreachable

    var errorDescription: String? {
        "The test host is unreachable."
    }
}

private final class TestConnection: MOOSGatewayConnection, @unchecked Sendable {
    private let lock = NSLock()
    private var frames: [Result<String, TestConnectionError>]
    private var recordedFrames: [Data] = []

    init(frames: [Result<String, TestConnectionError>]) {
        self.frames = frames
    }

    var sentFrames: [Data] {
        lock.lock()
        defer { lock.unlock() }
        return recordedFrames
    }

    func sendFrame(_ frame: Data) async throws {
        lock.lock()
        recordedFrames.append(frame)
        lock.unlock()
    }

    func receiveFrame(maximumFrameBytes: Int) async throws -> Data {
        lock.lock()
        let response = frames.isEmpty ? .failure(.unreachable) : frames.removeFirst()
        lock.unlock()
        let data = Data(try response.get().utf8)
        guard data.count <= maximumFrameBytes else {
            throw TestConnectionError.unreachable
        }
        return data
    }

    func close() async {}
}

private final class TestConnector: MOOSConnectionConnecting, @unchecked Sendable {
    private let lock = NSLock()
    private let result: Result<TestConnection, TestConnectionError>
    private var attempts = 0

    init(result: Result<TestConnection, TestConnectionError>) {
        self.result = result
    }

    var connectionAttempts: Int {
        lock.lock()
        defer { lock.unlock() }
        return attempts
    }

    func connect(to host: HostConfiguration) async throws -> any MOOSGatewayConnection {
        lock.lock()
        attempts += 1
        lock.unlock()
        return try result.get()
    }
}

private final class InMemoryDeviceCredentialStore: DeviceCredentialStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var keys: [String: Data] = [:]

    func loadKey(deviceID: String) throws -> Data? {
        lock.lock()
        defer { lock.unlock() }
        return keys[deviceID]
    }

    func saveKey(_ key: Data, deviceID: String) throws {
        lock.lock()
        defer { lock.unlock() }
        keys[deviceID] = key
    }

    func removeKey(deviceID: String) throws {
        lock.lock()
        defer { lock.unlock() }
        keys.removeValue(forKey: deviceID)
    }
}

private struct FailingHostConfigurationStore: HostConfigurationStoring {
    private enum PersistenceError: Error {
        case unavailable
    }

    func load() throws -> HostConfiguration? {
        nil
    }

    func save(_ configuration: HostConfiguration?) throws {
        throw PersistenceError.unavailable
    }
}
