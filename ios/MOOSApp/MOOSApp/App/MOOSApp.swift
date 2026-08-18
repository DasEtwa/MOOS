import SwiftUI

@main
struct MOOSApp: App {
    private let service: LiveMOOSStateService
    private let initialSnapshot: HomeSnapshot

    init() {
        let store = UserDefaultsHostConfigurationStore()
        service = LiveMOOSStateService(
            store: store,
            credentialStore: KeychainDeviceCredentialStore(),
            connector: NWMOOSConnectionConnector()
        )
        if let configuration = try? store.load() {
            initialSnapshot = .connecting(to: configuration)
        } else {
            initialSnapshot = .noHost
        }
    }

    var body: some Scene {
        WindowGroup {
            MOOSHomeView(
                viewModel: HomeViewModel(
                    service: service,
                    initialSnapshot: initialSnapshot
                )
            )
        }
    }
}
