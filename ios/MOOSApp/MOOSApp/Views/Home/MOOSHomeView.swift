import SwiftUI

struct MOOSHomeView: View {
    @StateObject private var viewModel: HomeViewModel
    @State private var isHostEditorPresented = false

    init(viewModel: @autoclosure @escaping () -> HomeViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel())
    }

    var body: some View {
        NavigationStack {
            ZStack {
                MOOSTheme.background.ignoresSafeArea()

                VStack(alignment: .leading, spacing: 0) {
                    Text("MOOS")
                        .font(.system(size: 30, weight: .black, design: .rounded))
                        .foregroundStyle(MOOSTheme.accent)
                        .padding(.horizontal, 24)
                        .padding(.top, 24)

                    switch viewModel.snapshot.connectionState {
                    case .noHost:
                        NoHostView(addHost: showHostEditor)
                    default:
                        ConfiguredHostView(
                            snapshot: viewModel.snapshot,
                            retry: { Task { await viewModel.retry() } },
                            edit: showHostEditor,
                            remove: { Task { await viewModel.removeHost() } }
                        )
                    }
                }
            }
            .toolbar(.hidden, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
        .task {
            await viewModel.start()
        }
        .sheet(isPresented: $isHostEditorPresented) {
            AddHostView(viewModel: viewModel)
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
    }

    private func showHostEditor() {
        viewModel.prepareHostEditor()
        isHostEditorPresented = true
    }
}

private struct NoHostView: View {
    let addHost: () -> Void

    var body: some View {
        VStack(spacing: 20) {
            Spacer()
            Image(systemName: "network.slash")
                .font(.system(size: 34, weight: .light))
                .foregroundStyle(MOOSTheme.secondaryText)
            Text("No host connected.")
                .font(.headline)
                .foregroundStyle(.white.opacity(0.86))
            Button("Add Host", action: addHost)
                .buttonStyle(MOOSPrimaryButtonStyle())
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(24)
    }
}

private struct ConfiguredHostView: View {
    let snapshot: HomeSnapshot
    let retry: () -> Void
    let edit: () -> Void
    let remove: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                hostHeader

                switch snapshot.connectionState {
                case .connecting, .reconnecting:
                    HStack(spacing: 12) {
                        ProgressView()
                            .tint(MOOSTheme.accent)
                        Text("Connecting to MOOS Host…")
                            .foregroundStyle(MOOSTheme.secondaryText)
                    }
                    .padding(.vertical, 12)
                case .disconnected, .offline:
                    failureCard
                case .connected:
                    instanceList
                case .noHost:
                    EmptyView()
                }

                HStack(spacing: 18) {
                    Button("Edit Host", action: edit)
                    Button("Remove Host", role: .destructive, action: remove)
                }
                .font(.subheadline.weight(.semibold))
            }
            .padding(.horizontal, 24)
            .padding(.top, 28)
            .padding(.bottom, 32)
        }
    }

    private var hostHeader: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 5) {
                Text(snapshot.host?.displayName ?? "MOOS Host")
                    .font(.title3.weight(.semibold))
                if let address = snapshot.host?.address,
                   let port = snapshot.host?.port {
                    Text("\(address):\(port)")
                        .font(.caption.monospaced())
                        .foregroundStyle(MOOSTheme.secondaryText)
                }
            }
            Spacer()
            HStack(spacing: 7) {
                Circle()
                    .fill(snapshot.connectionState.isReachable ? MOOSTheme.accent : .orange)
                    .frame(width: 8, height: 8)
                Text(snapshot.connectionState.label)
                    .font(.caption.monospaced())
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .background(MOOSTheme.panel, in: Capsule())
        }
    }

    private var failureCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Connection failed")
                .font(.headline)
            Text(snapshot.failureMessage ?? "The MOOS Host is unavailable.")
                .font(.subheadline)
                .foregroundStyle(MOOSTheme.secondaryText)
            Button("Retry", action: retry)
                .buttonStyle(MOOSPrimaryButtonStyle())
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.09), in: RoundedRectangle(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18)
                .stroke(Color.orange.opacity(0.28), lineWidth: 1)
        }
    }

    private var instanceList: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Instances")
                .font(.caption.weight(.semibold))
                .textCase(.uppercase)
                .tracking(1.2)
                .foregroundStyle(MOOSTheme.secondaryText)
            ForEach(snapshot.personalSystems) { system in
                HStack(spacing: 14) {
                    Image(systemName: "server.rack")
                        .foregroundStyle(MOOSTheme.accent)
                        .frame(width: 36, height: 36)
                        .background(MOOSTheme.accent.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(system.displayName)
                            .font(.headline)
                        Text(system.state.label)
                            .font(.caption.monospaced())
                            .foregroundStyle(MOOSTheme.secondaryText)
                    }
                    Spacer()
                }
                .padding(16)
                .background(MOOSTheme.panel, in: RoundedRectangle(cornerRadius: 18))
                .overlay {
                    RoundedRectangle(cornerRadius: 18)
                        .stroke(MOOSTheme.panelBorder, lineWidth: 1)
                }
            }
        }
    }
}

private struct AddHostView: View {
    @ObservedObject var viewModel: HomeViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Tailscale IP address", text: $viewModel.hostAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("Port", text: $viewModel.hostPort)
                        .keyboardType(.numberPad)
                    SecureField("Pairing code", text: $viewModel.pairingCode)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } footer: {
                    Text("Use the Tailscale address and pairing code shown once by your MOOS Host. Leave the pairing code empty when only editing the address or port.")
                }

                if let message = viewModel.hostValidationMessage {
                    Section {
                        Text(message)
                            .foregroundStyle(.orange)
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(MOOSTheme.background)
            .navigationTitle(snapshotTitle)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        Task {
                            if await viewModel.saveHost() {
                                dismiss()
                            }
                        }
                    }
                    .fontWeight(.semibold)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private var snapshotTitle: String {
        viewModel.snapshot.host == nil ? "Add Host" : "Edit Host"
    }
}

private struct MOOSPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.subheadline.weight(.bold))
            .foregroundStyle(.black)
            .padding(.horizontal, 18)
            .frame(minHeight: 42)
            .background(
                MOOSTheme.accent.opacity(configuration.isPressed ? 0.72 : 1),
                in: RoundedRectangle(cornerRadius: 12)
            )
    }
}

#Preview("No host") {
    MOOSHomeView(
        viewModel: HomeViewModel(
            service: MockMOOSStateService(),
            initialSnapshot: .noHost
        )
    )
}
