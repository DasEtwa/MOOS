protocol MOOSStateProviding: Sendable {
    func homeSnapshot() async -> HomeSnapshot
}
