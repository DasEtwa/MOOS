import SwiftUI
import UIKit

struct MOOSHomeView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var viewModel: HomeViewModel
    @StateObject private var settingsViewModel: SettingsViewModel
    @State private var navigationPath = NavigationPath()
    @State private var isHostEditorPresented = false
    @State private var isPowerMenuVisible = false
    @State private var isRadialMenuVisible = false
    @State private var notice: ShellNotice?

    init(viewModel: @autoclosure @escaping () -> HomeViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel())
        _settingsViewModel = StateObject(
            wrappedValue: SettingsViewModel(
                store: UserDefaultsLocalPreferencesStore()
            )
        )
    }

    var body: some View {
        NavigationStack(path: $navigationPath) {
            ZStack {
                MOOSTheme.background.ignoresSafeArea()

                VStack(spacing: 0) {
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
                                density: settingsViewModel.preferences.layoutDensity,
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

                    if viewModel.snapshot.host != nil {
                        SystemBarView(
                            connectionState: viewModel.snapshot.connectionState,
                            latencyMilliseconds: viewModel.snapshot.latencyMilliseconds,
                            sessions: viewModel.snapshot.sessions,
                            onMOOSTap: showPowerMenu,
                            onMOOSLongPress: showRadialMenu,
                            onSessionAction: handleSessionAction
                        )
                    }
                }

                if isPowerMenuVisible {
                    PowerMenuView(
                        onSelect: handlePowerAction,
                        onDismiss: hideOverlays
                    )
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                    .zIndex(1)
                }

                if isRadialMenuVisible {
                    RadialMenuView(
                        items: .shellDefaults,
                        onSelect: handleRadialSelection,
                        onDismiss: hideOverlays
                    )
                    .transition(.opacity.combined(with: .scale(scale: 0.92)))
                    .zIndex(2)
                }
            }
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: ShellDestination.self) { destination in
                destinationView(for: destination)
            }
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
        .alert(item: $notice) { notice in
            Alert(
                title: Text(notice.title),
                message: Text(notice.message),
                dismissButton: .default(Text("OK"))
            )
        }
    }

    private func shellAnimation(duration: Double) -> Animation? {
        settingsViewModel.preferences.animationsEnabled && !reduceMotion
            ? .easeOut(duration: duration) : nil
    }

    private func showHostEditor() {
        viewModel.prepareHostEditor()
        isHostEditorPresented = true
    }

    private func showPowerMenu() {
        withAnimation(shellAnimation(duration: 0.18)) {
            isRadialMenuVisible = false
            isPowerMenuVisible = true
        }
    }

    private func showRadialMenu() {
        withAnimation(shellAnimation(duration: 0.2)) {
            isPowerMenuVisible = false
            isRadialMenuVisible = true
        }
    }

    private func hideOverlays() {
        withAnimation(shellAnimation(duration: 0.16)) {
            isPowerMenuVisible = false
            isRadialMenuVisible = false
        }
    }

    private func handlePowerAction(_ action: PowerMenuAction) {
        hideOverlays()
        notice = ShellNotice(
            id: "power-\(action.id)",
            title: "\(action.title) unavailable",
            message: "Protocol v1 does not expose system power controls."
        )
    }

    private func handleRadialSelection(_ item: RadialMenuItem) {
        hideOverlays()
        navigationPath.append(item.destination)
    }

    private func handleSessionAction(
        _ session: AppSession,
        _ action: RunningAppAction
    ) {
        if action == .open,
           let application = viewModel.snapshot.applications.first(
               where: { $0.id == session.applicationID }
           ) {
            navigationPath.append(application.destination)
            return
        }

        notice = ShellNotice(
            id: "session-\(session.id)-\(action.id)",
            title: "\(action.title) unavailable",
            message: "Protocol v1 does not expose session controls yet."
        )
    }

    @ViewBuilder
    private func destinationView(for destination: ShellDestination) -> some View {
        switch destination {
        case .blender:
            ShellPlaceholderView(
                title: "Blender",
                symbolName: "cube.transparent",
                message: "Linux application streaming is not part of Protocol v1."
            )
        case .discord:
            ShellPlaceholderView(
                title: "Discord",
                symbolName: "bubble.left.and.bubble.right",
                message: "This application is represented by remote metadata only."
            )
        case .terminal:
            TerminalPlaceholderView()
        case .files:
            ShellPlaceholderView(
                title: "Files",
                symbolName: "folder",
                message: "Remote file access requires an authenticated operation."
            )
        case .apps:
            ShellPlaceholderView(
                title: "Apps",
                symbolName: "square.grid.2x2",
                message: "Installed application metadata comes from the configured host."
            )
        case .appStore:
            ShellPlaceholderView(
                title: "App Store",
                symbolName: "shippingbox",
                message: "Application discovery is not part of Protocol v1."
            )
        case .instances:
            ShellPlaceholderView(
                title: "Instances",
                symbolName: "server.rack",
                message: "Instance management remains at the host status boundary."
            )
        case .settings:
            SettingsView(viewModel: settingsViewModel)
        }
    }
}

private struct ShellNotice: Identifiable {
    let id: String
    let title: String
    let message: String
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
    let density: ShellLayoutDensity
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
            VStack(alignment: .leading, spacing: density == .compact ? 12 : 22) {
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

                if snapshot.hasShellContent {
                    shellWorkspace
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, density == .compact ? 14 : 28)
            .padding(.bottom, density == .compact ? 16 : 32)
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

    @ViewBuilder
    private var shellWorkspace: some View {
        VStack(alignment: .leading, spacing: density == .compact ? 12 : 22) {
            if snapshot.isShowingCachedMetadata {
                ConnectionBannerView(
                    state: snapshot.connectionState,
                    lastSynchronizedAt: snapshot.lastSynchronizedAt,
                    isShowingCachedMetadata: true
                )
            }

            if !snapshot.widgets.isEmpty {
                WidgetGridView(widgets: snapshot.widgets)
            }

            if !snapshot.applications.isEmpty {
                AppGridView(applications: snapshot.applications)
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
