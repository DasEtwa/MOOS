import SwiftUI

struct MOOSHomeView: View {
    @StateObject private var viewModel: HomeViewModel

    init(viewModel: @autoclosure @escaping () -> HomeViewModel) {
        _viewModel = StateObject(wrappedValue: viewModel())
    }

    var body: some View {
        NavigationStack {
            ZStack {
                MOOSTheme.background.ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 28) {
                        HomeHeader(snapshot: viewModel.snapshot)
                        PersonalSystemCard(system: viewModel.snapshot.personalSystem)
                        QuickLaunchGrid(applications: viewModel.snapshot.applications)
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 18)
                }
            }
            .toolbar(.hidden, for: .navigationBar)
            .navigationDestination(for: ShellDestination.self) { destination in
                destinationView(for: destination)
            }
        }
        .preferredColorScheme(.dark)
        .task {
            await viewModel.load()
        }
    }

    @ViewBuilder
    private func destinationView(for destination: ShellDestination) -> some View {
        switch destination {
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
        case .settings:
            SettingsPlaceholderView()
        }
    }
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

private struct QuickLaunchGrid: View {
    let applications: [ShellApp]

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("QUICK LAUNCH")
                .font(.caption.bold().monospaced())
                .tracking(1.3)
                .foregroundStyle(MOOSTheme.secondaryText)

            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(applications) { application in
                    NavigationLink(value: application.destination) {
                        VStack(alignment: .leading, spacing: 20) {
                            Image(systemName: application.symbolName)
                                .font(.system(size: 24, weight: .medium))
                                .foregroundStyle(MOOSTheme.accent)
                            Text(application.name)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.white)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(16)
                        .background(MOOSTheme.panel, in: RoundedRectangle(cornerRadius: 18))
                        .overlay {
                            RoundedRectangle(cornerRadius: 18)
                                .stroke(MOOSTheme.panelBorder, lineWidth: 1)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

#Preview {
    MOOSHomeView(viewModel: .preview)
}
