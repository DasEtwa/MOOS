import SwiftUI

@main
struct MOOSApp: App {
    private let preferencesStore: UserDefaultsLocalPreferencesStore
    private let initialPreferences: LocalShellPreferences

    init() {
        let store = UserDefaultsLocalPreferencesStore()
        preferencesStore = store
        initialPreferences = (try? store.load()) ?? .defaultValue
    }

    var body: some Scene {
        WindowGroup {
            MOOSHomeView(
                viewModel: HomeViewModel(
                    service: MockMOOSStateService(preferences: initialPreferences)
                ),
                settingsViewModel: SettingsViewModel(
                    store: preferencesStore
                )
            )
        }
    }
}
