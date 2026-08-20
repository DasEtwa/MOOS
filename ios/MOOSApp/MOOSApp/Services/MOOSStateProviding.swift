import Foundation

protocol MOOSStateProviding: Sendable {
    func snapshots() -> AsyncStream<HomeSnapshot>
    func configureHost(_ configuration: HostConfiguration, deviceKey: Data?) async -> Bool
    func retry() async
    func removeHost() async
    func renameHost(to displayName: String) async -> Bool
    func renamePersonalSystem(id: String, to displayName: String) async -> Bool
    func applicationDidEnterBackground() async
    func applicationDidBecomeActive() async
}
