import SwiftUI
import UIKit

struct MOOSHomeView: View {
    @Environment(\.scenePhase) private var scenePhase
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
                            remove: { Task { await viewModel.removeHost() } },
                            renameHost: { name in
                                await viewModel.renameHost(to: name)
                            },
                            renamePersonalSystem: { id, name in
                                await viewModel.renamePersonalSystem(id: id, to: name)
                            }
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
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background:
                Task { await viewModel.applicationDidEnterBackground() }
            case .active:
                Task { await viewModel.applicationDidBecomeActive() }
            case .inactive:
                break
            @unknown default:
                break
            }
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
    let renameHost: (String) async -> Bool
    let renamePersonalSystem: (String, String) async -> Bool

    @State private var isConnectionInfoPresented = false
    @State private var isHostRenamePresented = false
    @State private var isRemoveConfirmationPresented = false
    @State private var hostNameDraft = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                hostCard

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
            }
            .padding(.horizontal, 24)
            .padding(.top, 28)
            .padding(.bottom, 32)
        }
        .sheet(isPresented: $isConnectionInfoPresented) {
            ConnectionInfoView(snapshot: snapshot)
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
        }
        .alert("Rename Host", isPresented: $isHostRenamePresented) {
            TextField("Host name", text: $hostNameDraft)
            Button("Cancel", role: .cancel) {}
            Button("Save") {
                guard let normalized = MOOSDisplayName.normalized(hostNameDraft) else {
                    return
                }
                Task { _ = await renameHost(normalized) }
            }
        } message: {
            Text("Choose a local name for this MOOS Host.")
        }
        .confirmationDialog(
            "Remove this MOOS Host?",
            isPresented: $isRemoveConfirmationPresented,
            titleVisibility: .visible
        ) {
            Button("Remove Host", role: .destructive, action: remove)
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The saved address and device credential will be removed from this iPhone.")
        }
    }

    private var hostCard: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(alignment: .top, spacing: 14) {
                Image(systemName: "server.rack")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(MOOSTheme.accent)
                    .frame(width: 44, height: 44)
                    .background(
                        MOOSTheme.accent.opacity(0.11),
                        in: RoundedRectangle(cornerRadius: 13)
                    )

                VStack(alignment: .leading, spacing: 6) {
                    Text(snapshot.host?.displayName ?? "MOOS Host")
                        .font(.title3.weight(.semibold))
                    if let endpoint {
                        Text(verbatim: endpoint)
                            .font(.caption.monospaced())
                            .foregroundStyle(MOOSTheme.secondaryText)
                    }
                }

                Spacer(minLength: 8)

                Menu {
                    Button("Edit Host", systemImage: "pencil", action: edit)
                    Button("Connection Info", systemImage: "info.circle") {
                        isConnectionInfoPresented = true
                    }
                    Button("Rename", systemImage: "character.cursor.ibeam") {
                        hostNameDraft = snapshot.host?.displayName ?? "MOOS Host"
                        isHostRenamePresented = true
                    }
                    Button("Copy Address", systemImage: "doc.on.doc") {
                        if let endpoint {
                            UIPasteboard.general.string = endpoint
                        }
                    }
                    .disabled(endpoint == nil)
                    Divider()
                    Button("Remove Host", systemImage: "trash", role: .destructive) {
                        isRemoveConfirmationPresented = true
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.headline)
                        .foregroundStyle(.white.opacity(0.82))
                        .frame(width: 38, height: 38)
                        .background(MOOSTheme.panel, in: Circle())
                }
                .accessibilityLabel("Host actions")
            }

            Divider()
                .overlay(MOOSTheme.panelBorder)

            HStack {
                Label {
                    Text(snapshot.connectionState.label)
                        .font(.caption.monospaced())
                } icon: {
                    Circle()
                        .fill(snapshot.connectionState.isReachable ? MOOSTheme.accent : .orange)
                        .frame(width: 8, height: 8)
                }
                Spacer()
                Text("Protocol v1")
                    .font(.caption.monospaced())
                    .foregroundStyle(MOOSTheme.secondaryText)
            }
        }
        .padding(18)
        .background(MOOSTheme.panel, in: RoundedRectangle(cornerRadius: 20))
        .overlay {
            RoundedRectangle(cornerRadius: 20)
                .stroke(MOOSTheme.panelBorder, lineWidth: 1)
        }
    }

    private var endpoint: String? {
        guard let address = snapshot.host?.address,
              let port = snapshot.host?.port else {
            return nil
        }
        return "\(address):\(port)"
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
                InstanceCard(system: system) { name in
                    await renamePersonalSystem(system.id, name)
                }
            }
        }
    }
}

private struct InstanceCard: View {
    let system: PersonalSystem
    let rename: (String) async -> Bool

    @State private var name: String
    @State private var isEditingName = false
    @FocusState private var isNameFocused: Bool

    init(system: PersonalSystem, rename: @escaping (String) async -> Bool) {
        self.system = system
        self.rename = rename
        _name = State(initialValue: system.displayName)
    }

    var body: some View {
        HStack(spacing: 14) {
            Image(systemName: "cube.box")
                .foregroundStyle(MOOSTheme.accent)
                .frame(width: 38, height: 38)
                .background(
                    MOOSTheme.accent.opacity(0.1),
                    in: RoundedRectangle(cornerRadius: 11)
                )

            VStack(alignment: .leading, spacing: 5) {
                if isEditingName {
                    TextField("Instance name", text: $name)
                        .font(.headline)
                        .textFieldStyle(.plain)
                        .focused($isNameFocused)
                        .submitLabel(.done)
                        .onSubmit(commitName)
                } else {
                    Text(system.displayName)
                        .font(.headline)
                        .contentShape(Rectangle())
                        .onTapGesture(perform: beginEditingName)
                        .accessibilityAddTraits(.isButton)
                        .accessibilityHint("Double tap to rename")
                }

                Text(system.state.label)
                    .font(.caption.monospaced())
                    .foregroundStyle(MOOSTheme.secondaryText)
            }

            Spacer()

            Menu {
                Button("Rename", systemImage: "character.cursor.ibeam") {
                    beginEditingName()
                }
                if system.capabilities.isEmpty {
                    Divider()
                    Text("No additional actions")
                }
            } label: {
                Image(systemName: "ellipsis")
                    .foregroundStyle(.white.opacity(0.74))
                    .frame(width: 36, height: 36)
            }
            .accessibilityLabel("Instance actions")
        }
        .padding(16)
        .background(MOOSTheme.panel, in: RoundedRectangle(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18)
                .stroke(MOOSTheme.panelBorder, lineWidth: 1)
        }
        .onChange(of: system.displayName) { _, updatedName in
            guard !isEditingName else { return }
            name = updatedName
        }
        .onChange(of: isNameFocused) { _, focused in
            if !focused, isEditingName {
                commitName()
            }
        }
    }

    private func beginEditingName() {
        name = system.displayName
        isEditingName = true
        isNameFocused = true
    }

    private func commitName() {
        guard isEditingName else { return }
        isEditingName = false
        isNameFocused = false
        guard let normalized = MOOSDisplayName.normalized(name) else {
            name = system.displayName
            return
        }
        name = normalized
        Task {
            if !(await rename(normalized)) {
                name = system.displayName
            }
        }
    }
}

private struct ConnectionInfoView: View {
    let snapshot: HomeSnapshot
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                LabeledContent("Host", value: snapshot.host?.displayName ?? "MOOS Host")
                if let address = snapshot.host?.address,
                   let port = snapshot.host?.port {
                    LabeledContent("Address") {
                        Text(verbatim: "\(address):\(port)")
                            .monospaced()
                    }
                }
                LabeledContent("Status", value: snapshot.connectionState.label)
                LabeledContent("Protocol", value: "v1")
                LabeledContent("Transport", value: "Tailscale")
            }
            .scrollContentBackground(.hidden)
            .background(MOOSTheme.background)
            .navigationTitle("Connection Info")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
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
