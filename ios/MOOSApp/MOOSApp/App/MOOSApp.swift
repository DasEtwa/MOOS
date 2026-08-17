import SwiftUI

@main
struct MOOSApp: App {
    var body: some Scene {
        WindowGroup {
            MOOSHomeView(
                viewModel: HomeViewModel(service: MockMOOSStateService())
            )
        }
    }
}
