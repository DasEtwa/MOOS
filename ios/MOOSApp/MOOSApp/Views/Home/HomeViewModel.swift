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

    func load() async {
        snapshot = await service.homeSnapshot()
    }

    static var preview: HomeViewModel {
        HomeViewModel(service: MockMOOSStateService())
    }
}
