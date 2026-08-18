import Foundation

protocol MOOSStateProviding: Sendable {
    func snapshots() -> AsyncStream<HomeSnapshot>
    func configureHost(_ configuration: HostConfiguration, deviceKey: Data?) async -> Bool
    func retry() async
    func removeHost() async
}
