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

struct EmptyRemoteMetadataCache: RemoteMetadataCaching, Sendable {
    func load() throws -> CachedRemoteMetadata? {
        nil
    }

    func save(_ metadata: CachedRemoteMetadata) throws {}
}

enum DiskRemoteMetadataCacheError: Error, Equatable, Sendable {
    case fileTooLarge(actualBytes: Int, maximumBytes: Int)
}

final class DiskRemoteMetadataCache: RemoteMetadataCaching, @unchecked Sendable {
    static let maximumFileSizeBytes = 1_048_576

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

        let data = try readBoundedData()
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let metadata = try decoder.decode(
            CachedRemoteMetadata.self,
            from: data
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
        guard data.count <= Self.maximumFileSizeBytes else {
            throw DiskRemoteMetadataCacheError.fileTooLarge(
                actualBytes: data.count,
                maximumBytes: Self.maximumFileSizeBytes
            )
        }

        lock.lock()
        defer { lock.unlock() }

        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try data.write(to: fileURL, options: .atomic)
    }

    private func readBoundedData() throws -> Data {
        let resourceValues = try fileURL.resourceValues(forKeys: [.fileSizeKey])
        if let fileSize = resourceValues.fileSize,
           fileSize > Self.maximumFileSizeBytes {
            throw DiskRemoteMetadataCacheError.fileTooLarge(
                actualBytes: fileSize,
                maximumBytes: Self.maximumFileSizeBytes
            )
        }

        let handle = try FileHandle(forReadingFrom: fileURL)
        defer { try? handle.close() }

        var data = Data()
        while data.count <= Self.maximumFileSizeBytes {
            let remaining = Self.maximumFileSizeBytes + 1 - data.count
            let chunk = try handle.read(upToCount: min(64 * 1024, remaining)) ?? Data()
            if chunk.isEmpty {
                return data
            }
            data.append(chunk)
        }

        throw DiskRemoteMetadataCacheError.fileTooLarge(
            actualBytes: data.count,
            maximumBytes: Self.maximumFileSizeBytes
        )
    }
}
