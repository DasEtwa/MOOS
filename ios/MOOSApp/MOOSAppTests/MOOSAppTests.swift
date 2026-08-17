import XCTest
@testable import MOOSApp

final class MOOSAppTests: XCTestCase {
    func testConnectionStateLabelsAreClientFacing() {
        XCTAssertEqual(ConnectionState.connected.label, "Connected")
        XCTAssertEqual(ConnectionState.reconnecting.label, "Reconnecting")
        XCTAssertFalse(ConnectionState.offline.isReachable)
    }

    func testMockSnapshotUsesStablePersonalIdentity() async {
        let snapshot = await MockMOOSStateService().homeSnapshot()

        XCTAssertEqual(snapshot.personalSystem.id, "personal")
        XCTAssertEqual(snapshot.personalSystem.state, .running)
        XCTAssertEqual(snapshot.connectionState, .connected)
        XCTAssertEqual(snapshot.applications.map(\.id), [
            "blender", "discord", "terminal", "files", "settings",
        ])
        XCTAssertEqual(snapshot.widgets.count, 6)
        XCTAssertEqual(snapshot.sessions.count, 2)
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
}
