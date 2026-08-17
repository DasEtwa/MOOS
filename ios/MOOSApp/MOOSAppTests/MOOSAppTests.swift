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
            "terminal", "files", "apps", "settings",
        ])
    }
}
