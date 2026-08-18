import Foundation

protocol HostConfigurationStoring: Sendable {
    func load() throws -> HostConfiguration?
    func save(_ configuration: HostConfiguration?) throws
}

final class UserDefaultsHostConfigurationStore: HostConfigurationStoring, @unchecked Sendable {
    private let defaults: UserDefaults
    private let key: String
    private let lock = NSLock()

    init(
        defaults: UserDefaults = .standard,
        key: String = "moos.host.configuration.v1"
    ) {
        self.defaults = defaults
        self.key = key
    }

    func load() throws -> HostConfiguration? {
        lock.lock()
        defer { lock.unlock() }
        guard let data = defaults.data(forKey: key) else {
            return nil
        }
        let decoded = try JSONDecoder().decode(HostConfiguration.self, from: data)
        return try HostConfiguration(
            address: decoded.address,
            port: decoded.port,
            deviceID: decoded.deviceID
        )
    }

    func save(_ configuration: HostConfiguration?) throws {
        lock.lock()
        defer { lock.unlock() }
        guard let configuration else {
            defaults.removeObject(forKey: key)
            return
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        defaults.set(try encoder.encode(configuration), forKey: key)
    }
}
