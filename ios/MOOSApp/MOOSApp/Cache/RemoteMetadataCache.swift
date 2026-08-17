import Foundation

struct CachedRemoteMetadata: Codable, Equatable, Sendable {
    static let currentSchemaVersion = 1

    let schemaVersion: Int
    let host: Host
    let personalSystemID: String
    let personalSystemName: String
    let applications: [ShellApp]
    let widgets: [WidgetMetadata]
    let updatedAt: Date

    init(
        schemaVersion: Int = CachedRemoteMetadata.currentSchemaVersion,
        host: Host,
        personalSystemID: String,
        personalSystemName: String,
        applications: [ShellApp],
        widgets: [WidgetMetadata],
        updatedAt: Date
    ) {
        self.schemaVersion = schemaVersion
        self.host = host
        self.personalSystemID = personalSystemID
        self.personalSystemName = personalSystemName
        self.applications = applications
        self.widgets = widgets
        self.updatedAt = updatedAt
    }
}

protocol RemoteMetadataCaching: Sendable {
    func load() throws -> CachedRemoteMetadata?
    func save(_ metadata: CachedRemoteMetadata) throws
}

final class DiskRemoteMetadataCache: RemoteMetadataCaching, @unchecked Sendable {
    private let fileURL: URL
    private let lock = NSLock()

    init(fileURL: URL) {
        self.fileURL = fileURL
    }

    static func defaultFileURL(fileManager: FileManager = .default) throws -> URL {
        let cacheRoot = try fileManager.url(
            for: .cachesDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        return cacheRoot
            .appendingPathComponent("MOOS", isDirectory: true)
            .appendingPathComponent("remote-metadata-v1.json", isDirectory: false)
    }

    func load() throws -> CachedRemoteMetadata? {
        lock.lock()
        defer { lock.unlock() }

        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return nil
        }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let metadata = try decoder.decode(
            CachedRemoteMetadata.self,
            from: Data(contentsOf: fileURL)
        )
        guard metadata.schemaVersion == CachedRemoteMetadata.currentSchemaVersion else {
            return nil
        }
        return metadata
    }

    func save(_ metadata: CachedRemoteMetadata) throws {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(metadata)

        lock.lock()
        defer { lock.unlock() }

        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try data.write(to: fileURL, options: .atomic)
    }
}
