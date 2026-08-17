import SwiftUI

struct MOOSHomeView: View {
    @StateObject private var viewModel: HomeViewModel
    @StateObject private var settingsViewModel: SettingsViewModel
    @State private var navigationPath = NavigationPath()
    @State private var isPowerMenuVisible = false
    @State private var isRadialMenuVisible = false
    @State private var notice: ShellNotice?

    init(
        viewModel: @autoclosure @escaping () -> HomeViewModel,
        settingsViewModel: @autoclosure @escaping () -> SettingsViewModel
    ) {
        _viewModel = StateObject(wrappedValue: viewModel())
        _settingsViewModel = StateObject(wrappedValue: settingsViewModel())
    }

    var body: some View {
        NavigationStack(path: $navigationPath) {
            ZStack {
                MOOSTheme.background.ignoresSafeArea()

                VStack(spacing: 0) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 26) {
                            HomeHeader(snapshot: viewModel.snapshot)
                            if viewModel.snapshot.connectionState != .connected {
                                ConnectionBannerView(
                                    state: viewModel.snapshot.connectionState,
                                    lastSynchronizedAt: viewModel.snapshot.lastSynchronizedAt,
                                    isShowingCachedMetadata: viewModel.snapshot.isShowingCachedMetadata
                                )
                            }
                            PersonalSystemCard(
                                system: viewModel.snapshot.personalSystem,
                                uptime: viewModel.snapshot.uptime
                            )
                            WidgetGridView(widgets: viewModel.snapshot.widgets)
                            AppGridView(applications: viewModel.snapshot.applications)
                        }
                        .padding(.horizontal, 20)
                        .padding(.top, 18)
                        .padding(.bottom, 24)
                    }

                    SystemBarView(
                        connectionState: viewModel.snapshot.connectionState,
                        latencyMilliseconds: viewModel.snapshot.latencyMilliseconds,
                        sessions: viewModel.snapshot.sessions,
                        onMOOSTap: showPowerMenu,
                        onMOOSLongPress: showRadialMenu,
                        onSessionAction: handleSessionAction
                    )
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
        .alert(item: $notice) { notice in
            Alert(
                title: Text(notice.title),
                message: Text(notice.message),
                dismissButton: .default(Text("OK"))
            )
        }
    }

    private func showPowerMenu() {
        withAnimation(.easeOut(duration: 0.18)) {
            isRadialMenuVisible = false
            isPowerMenuVisible = true
        }
    }

    private func showRadialMenu() {
        withAnimation(.easeOut(duration: 0.2)) {
            isPowerMenuVisible = false
            isRadialMenuVisible = true
        }
    }

    private func hideOverlays() {
        withAnimation(.easeOut(duration: 0.16)) {
            isPowerMenuVisible = false
            isRadialMenuVisible = false
        }
    }

    private func handlePowerAction(_ action: PowerMenuAction) {
        hideOverlays()
        notice = ShellNotice(
            id: "power-\(action.id)",
            title: "\(action.title) unavailable",
            message: "This mock shell does not send power or session commands."
        )
    }

    private func handleRadialSelection(_ item: RadialMenuItem) {
        hideOverlays()
        navigationPath.append(item.destination)
    }

    private func handleSessionAction(_ session: AppSession, _ action: RunningAppAction) {
        if action == .open {
            if let application = viewModel.snapshot.applications.first(
                where: { $0.id == session.applicationID }
            ) {
                navigationPath.append(application.destination)
                return
            }
        }

        notice = ShellNotice(
            id: "session-\(session.id)-\(action.id)",
            title: "\(action.title) unavailable",
            message: "\(session.displayName) is mock session metadata for now."
        )
    }

    @ViewBuilder
    private func destinationView(for destination: ShellDestination) -> some View {
        switch destination {
        case .blender:
            ShellPlaceholderView(
                title: "Blender",
                symbolName: "cube.transparent",
                message: "Linux application streaming is intentionally not implemented."
            )
        case .discord:
            ShellPlaceholderView(
                title: "Discord",
                symbolName: "bubble.left.and.bubble.right",
                message: "This application tile is a local shell prototype."
            )
        case .terminal:
            TerminalPlaceholderView()
        case .files:
            ShellPlaceholderView(
                title: "Files",
                symbolName: "folder",
                message: "Personal files will appear here in a later slice."
            )
        case .apps:
            ShellPlaceholderView(
                title: "Apps",
                symbolName: "square.grid.2x2",
                message: "Installed application metadata will appear here later."
            )
        case .appStore:
            ShellPlaceholderView(
                title: "App Store",
                symbolName: "shippingbox",
                message: "Application discovery is only a navigation concept today."
            )
        case .instances:
            ShellPlaceholderView(
                title: "Instances",
                symbolName: "server.rack",
                message: "Multi-instance management is outside the current scope."
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

private struct HomeHeader: View {
    let snapshot: HomeSnapshot

    var body: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                Text("MOOS")
                    .font(.system(size: 30, weight: .black, design: .rounded))
                    .foregroundStyle(MOOSTheme.accent)
                Text(snapshot.host.displayName.uppercased())
                    .font(.caption.monospaced())
                    .tracking(1.4)
                    .foregroundStyle(MOOSTheme.secondaryText)
            }

            Spacer()

            HStack(spacing: 7) {
                Circle()
                    .fill(snapshot.connectionState.isReachable ? MOOSTheme.accent : .orange)
                    .frame(width: 8, height: 8)
                Text(snapshot.connectionState.label)
                    .font(.caption.monospaced())
                    .foregroundStyle(.white.opacity(0.78))
            }
            .padding(.horizontal, 11)
            .padding(.vertical, 8)
            .background(MOOSTheme.panel, in: Capsule())
        }
    }
}

private struct PersonalSystemCard: View {
    let system: PersonalSystem
    let uptime: SynchronizedUptime?

    var body: some View {
        HStack(spacing: 16) {
            Image(systemName: "server.rack")
                .font(.system(size: 26, weight: .medium))
                .foregroundStyle(MOOSTheme.accent)
                .frame(width: 52, height: 52)
                .background(MOOSTheme.accent.opacity(0.1), in: RoundedRectangle(cornerRadius: 14))

            VStack(alignment: .leading, spacing: 5) {
                Text(system.displayName)
                    .font(.headline)
                Text(system.state.label)
                    .font(.caption.monospaced())
                    .foregroundStyle(MOOSTheme.secondaryText)
                if let uptime {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text("Uptime \(uptime.formatted(at: context.date))")
                            .font(.caption2.monospaced())
                            .foregroundStyle(MOOSTheme.secondaryText)
                    }
                }
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(.caption.weight(.bold))
                .foregroundStyle(MOOSTheme.secondaryText)
        }
        .padding(18)
        .background(MOOSTheme.panel, in: RoundedRectangle(cornerRadius: 20))
        .overlay {
            RoundedRectangle(cornerRadius: 20)
                .stroke(MOOSTheme.panelBorder, lineWidth: 1)
        }
    }
}

#Preview("Connected") {
    MOOSHomeView(
        viewModel: .preview(),
        settingsViewModel: .preview
    )
}

#Preview("Offline cache") {
    MOOSHomeView(
        viewModel: .preview(.offline),
        settingsViewModel: .preview
    )
}
