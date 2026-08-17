import Foundation

enum ShellLayoutDensity: String, CaseIterable, Codable, Sendable {
    case comfortable
    case compact
}

enum ShellThemePreference: String, CaseIterable, Codable, Sendable {
    case dark
}

struct LocalShellPreferences: Codable, Equatable, Sendable {
    var layoutDensity: ShellLayoutDensity
    var theme: ShellThemePreference
    var animationsEnabled: Bool
    var appArrangement: [String]

    static let defaultValue = LocalShellPreferences(
        layoutDensity: .comfortable,
        theme: .dark,
        animationsEnabled: true,
        appArrangement: ["blender", "discord", "terminal", "files", "settings"]
    )
}

protocol LocalPreferencesStoring: Sendable {
    func load() throws -> LocalShellPreferences
    func save(_ preferences: LocalShellPreferences) throws
}

final class UserDefaultsLocalPreferencesStore: LocalPreferencesStoring, @unchecked Sendable {
    private let defaults: UserDefaults
    private let key: String
    private let lock = NSLock()

    init(defaults: UserDefaults = .standard, key: String = "moos.shell.preferences") {
        self.defaults = defaults
        self.key = key
    }

    func load() throws -> LocalShellPreferences {
        lock.lock()
        defer { lock.unlock() }

        guard let data = defaults.data(forKey: key) else {
            return .defaultValue
        }
        return try JSONDecoder().decode(LocalShellPreferences.self, from: data)
    }

    func save(_ preferences: LocalShellPreferences) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(preferences)

        lock.lock()
        defer { lock.unlock() }
        defaults.set(data, forKey: key)
    }
}
