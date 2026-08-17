import Combine
import Foundation
import SwiftUI

@MainActor
final class SettingsViewModel: ObservableObject {
    @Published private(set) var preferences: LocalShellPreferences
    @Published private(set) var errorMessage: String?

    private let store: any LocalPreferencesStoring

    init(store: any LocalPreferencesStoring) {
        self.store = store
        errorMessage = nil
        do {
            preferences = try store.load()
        } catch {
            preferences = .defaultValue
            errorMessage = "Local preferences could not be loaded."
        }
    }

    func setLayoutDensity(_ density: ShellLayoutDensity) {
        preferences.layoutDensity = density
        persist()
    }

    func setAnimationsEnabled(_ enabled: Bool) {
        preferences.animationsEnabled = enabled
        persist()
    }

    func reset() {
        preferences = .defaultValue
        persist()
    }

    private func persist() {
        do {
            try store.save(preferences)
            errorMessage = nil
        } catch {
            errorMessage = "Local preferences could not be saved."
        }
    }

    static var preview: SettingsViewModel {
        let defaults = UserDefaults(suiteName: "dev.moos.shell.preview") ?? .standard
        return SettingsViewModel(
            store: UserDefaultsLocalPreferencesStore(defaults: defaults)
        )
    }
}

struct SettingsView: View {
    @ObservedObject var viewModel: SettingsViewModel

    var body: some View {
        Form {
            Section("Appearance") {
                Picker(
                    "Layout",
                    selection: Binding(
                        get: { viewModel.preferences.layoutDensity },
                        set: viewModel.setLayoutDensity
                    )
                ) {
                    Text("Comfortable").tag(ShellLayoutDensity.comfortable)
                    Text("Compact").tag(ShellLayoutDensity.compact)
                }

                LabeledContent("Theme", value: "MOOS Dark")

                Toggle(
                    "Animations",
                    isOn: Binding(
                        get: { viewModel.preferences.animationsEnabled },
                        set: viewModel.setAnimationsEnabled
                    )
                )
                .tint(MOOSTheme.accent)
            }

            Section("Home") {
                LabeledContent(
                    "App arrangement",
                    value: "\(viewModel.preferences.appArrangement.count) apps"
                )
                Text("App ordering stays on this iPhone and can be edited in a later slice.")
                    .font(.caption)
                    .foregroundStyle(MOOSTheme.secondaryText)
            }

            if let errorMessage = viewModel.errorMessage {
                Section {
                    Text(errorMessage)
                        .foregroundStyle(.orange)
                }
            }

            Section {
                Button("Reset local preferences", action: viewModel.reset)
            }
        }
        .scrollContentBackground(.hidden)
        .background(MOOSTheme.background)
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
    }
}
