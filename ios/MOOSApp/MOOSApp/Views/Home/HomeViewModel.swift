import Combine
import Foundation

@MainActor
final class HomeViewModel: ObservableObject {
    @Published private(set) var snapshot: HomeSnapshot
    @Published var hostAddress = ""
    @Published var hostPort = ""
    @Published var pairingCode = ""
    @Published private(set) var hostValidationMessage: String?

    private let service: any MOOSStateProviding

    init(
        service: any MOOSStateProviding,
        initialSnapshot: HomeSnapshot = .noHost
    ) {
        self.service = service
        snapshot = initialSnapshot
    }

    func prepareHostEditor() {
        hostAddress = snapshot.host?.address ?? ""
        hostPort = snapshot.host?.port.map(String.init) ?? ""
        pairingCode = ""
        hostValidationMessage = nil
    }

    func saveHost() async -> Bool {
        guard let numericPort = UInt16(hostPort), numericPort > 0 else {
            hostValidationMessage = "Enter a port from 1 to 65535."
            return false
        }
        let credential: MOOSGatewayV1.PairingCredential?
        if pairingCode.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            guard snapshot.host?.deviceID != nil else {
                hostValidationMessage = "Enter the pairing code created by your MOOS Host."
                return false
            }
            credential = nil
        } else {
            do {
                credential = try MOOSGatewayV1.parsePairingCode(pairingCode)
            } catch {
                hostValidationMessage = "Enter a valid MOOS pairing code."
                return false
            }
        }
        guard let deviceID = credential?.deviceID ?? snapshot.host?.deviceID else {
            hostValidationMessage = "Enter the pairing code created by your MOOS Host."
            return false
        }
        do {
            let configuration = try HostConfiguration(
                address: hostAddress,
                port: numericPort,
                deviceID: deviceID,
                displayName: snapshot.host?.displayName
            )
            if credential != nil {
                pairingCode = ""
            }
            hostValidationMessage = nil
            let persisted = await service.configureHost(
                configuration,
                deviceKey: credential?.key
            )
            if !persisted {
                hostValidationMessage = "The host settings could not be saved."
            }
            return persisted
        } catch {
            hostValidationMessage = "Enter a valid Tailscale IP address."
            return false
        }
    }

    func retry() async {
        await service.retry()
    }

    func removeHost() async {
        await service.removeHost()
    }

    func renameHost(to displayName: String) async -> Bool {
        await service.renameHost(to: displayName)
    }

    func renamePersonalSystem(id: String, to displayName: String) async -> Bool {
        await service.renamePersonalSystem(id: id, to: displayName)
    }

    func applicationDidEnterBackground() async {
        await service.applicationDidEnterBackground()
    }

    func applicationDidBecomeActive() async {
        await service.applicationDidBecomeActive()
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
