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
            deviceID: decoded.deviceID,
            displayName: decoded.displayName
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

protocol InstanceNameStoring: Sendable {
    func loadName(hostID: String, instanceID: String) throws -> String?
    func saveName(_ name: String, hostID: String, instanceID: String) throws
}

final class UserDefaultsInstanceNameStore: InstanceNameStoring, @unchecked Sendable {
    private let defaults: UserDefaults
    private let key: String
    private let lock = NSLock()

    init(
        defaults: UserDefaults = .standard,
        key: String = "moos.instance.names.v1"
    ) {
        self.defaults = defaults
        self.key = key
    }

    func loadName(hostID: String, instanceID: String) throws -> String? {
        lock.lock()
        defer { lock.unlock() }
        return try loadNames()[storageKey(hostID: hostID, instanceID: instanceID)]
    }

    func saveName(_ name: String, hostID: String, instanceID: String) throws {
        lock.lock()
        defer { lock.unlock() }
        var names = try loadNames()
        names[storageKey(hostID: hostID, instanceID: instanceID)] = name
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        defaults.set(try encoder.encode(names), forKey: key)
    }

    private func loadNames() throws -> [String: String] {
        guard let data = defaults.data(forKey: key) else {
            return [:]
        }
        return try JSONDecoder().decode([String: String].self, from: data)
    }

    private func storageKey(hostID: String, instanceID: String) -> String {
        "\(hostID):\(instanceID)"
    }
}
