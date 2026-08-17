protocol RemoteMetadataProviding: Sendable {
    func metadata() async throws -> CachedRemoteMetadata
}

protocol LiveMOOSStateProviding: Sendable {
    func states() -> AsyncThrowingStream<LiveMOOSState, Error>
}
