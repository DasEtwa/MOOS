import SwiftUI

enum PowerMenuAction: String, CaseIterable, Equatable, Identifiable {
    case lock
    case reboot
    case shutdown

    var id: String { rawValue }

    var title: String {
        switch self {
        case .lock:
            return "Lock"
        case .reboot:
            return "Reboot"
        case .shutdown:
            return "Shut down"
        }
    }

    var symbolName: String {
        switch self {
        case .lock:
            return "lock"
        case .reboot:
            return "arrow.clockwise"
        case .shutdown:
            return "power"
        }
    }
}

struct PowerMenuView: View {
    let onSelect: (PowerMenuAction) -> Void
    let onDismiss: () -> Void

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            Color.black.opacity(0.58)
                .ignoresSafeArea()
                .onTapGesture(perform: onDismiss)

            VStack(alignment: .leading, spacing: 4) {
                Text("SYSTEM")
                    .font(.caption2.bold().monospaced())
                    .tracking(1.2)
                    .foregroundStyle(MOOSTheme.secondaryText)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 5)

                ForEach(PowerMenuAction.allCases) { action in
                    Button(role: action == .shutdown ? .destructive : nil) {
                        onSelect(action)
                    } label: {
                        Label(action.title, systemImage: action.symbolName)
                            .font(.subheadline.weight(.medium))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 11)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(action == .shutdown ? .red : .white)
                }

                Text("Protocol v1 controls")
                    .font(.caption2)
                    .foregroundStyle(MOOSTheme.secondaryText)
                    .padding(.horizontal, 12)
                    .padding(.top, 5)
            }
            .frame(width: 190)
            .padding(10)
            .background(Color(white: 0.08), in: RoundedRectangle(cornerRadius: 18))
            .overlay {
                RoundedRectangle(cornerRadius: 18)
                    .stroke(MOOSTheme.panelBorder, lineWidth: 1)
            }
            .padding(.leading, 14)
            .padding(.bottom, 68)
        }
    }
}
