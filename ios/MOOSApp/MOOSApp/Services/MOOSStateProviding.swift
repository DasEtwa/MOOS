protocol MOOSStateProviding: Sendable {
    func snapshots() -> AsyncStream<HomeSnapshot>
}
