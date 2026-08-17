import Foundation
import XCTest
@testable import MOOSApp

final class MOOSAppTests: XCTestCase {
    func testConnectionStateLabelsAreClientFacing() {
        XCTAssertEqual(ConnectionState.connected.label, "Connected")
        XCTAssertEqual(ConnectionState.reconnecting.label, "Reconnecting")
        XCTAssertFalse(ConnectionState.offline.isReachable)
    }

    func testMockSnapshotUsesStablePersonalIdentity() async {
        let snapshot = await firstSnapshot(from: MockMOOSStateService())

        XCTAssertEqual(snapshot.personalSystem.id, "personal")
        XCTAssertEqual(snapshot.personalSystem.state, .running)
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
}
