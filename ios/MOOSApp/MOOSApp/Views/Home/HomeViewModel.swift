import Combine

@MainActor
final class HomeViewModel: ObservableObject {
    @Published private(set) var snapshot: HomeSnapshot

    private let service: any MOOSStateProviding

    init(
        service: any MOOSStateProviding,
        initialSnapshot: HomeSnapshot = .placeholder
    ) {
        self.service = service
        snapshot = initialSnapshot
    }

    func start() async {
        for await nextSnapshot in service.snapshots() {
            guard !Task.isCancelled else {
                return
            }
            snapshot = nextSnapshot
        }
    }

    static func preview(_ scenario: MockConnectionScenario = .connected) -> HomeViewModel {
        HomeViewModel(service: MockMOOSStateService(scenario: scenario))
    }
}
