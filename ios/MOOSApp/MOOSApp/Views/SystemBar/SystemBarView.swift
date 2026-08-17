import Foundation
import SwiftUI

enum RunningAppAction: String, Equatable, Identifiable {
    case open
    case close
    case forceClose
    case newWindow

    var id: String { rawValue }

    var title: String {
        switch self {
        case .open:
            return "Open"
        case .close:
            return "Close"
        case .forceClose:
            return "Force close"
        case .newWindow:
            return "New window"
        }
    }
}

struct SystemBarView: View {
    let connectionState: ConnectionState
    let sessions: [AppSession]
    let onMOOSTap: () -> Void
    let onMOOSLongPress: () -> Void
    let onSessionAction: (AppSession, RunningAppAction) -> Void

    var body: some View {
        HStack(spacing: 10) {
            MOOSButton(
                onTap: onMOOSTap,
                onLongPress: onMOOSLongPress
            )

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 7) {
                    ForEach(sessions) { session in
                        RunningAppButton(
                            session: session,
                            onAction: { action in onSessionAction(session, action) }
                        )
                    }
                }
            }
            .frame(maxWidth: 110, alignment: .leading)

            Spacer(minLength: 0)

            ConnectionIndicator(state: connectionState)

            TimelineView(.periodic(from: .now, by: 60)) { context in
                Text(context.date, format: .dateTime.hour().minute())
                    .font(.caption.bold().monospacedDigit())
                    .foregroundStyle(.white.opacity(0.82))
                    .accessibilityLabel(context.date.formatted(date: .omitted, time: .shortened))
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .background(Color(white: 0.035))
        .overlay(alignment: .top) {
            Rectangle()
                .fill(MOOSTheme.panelBorder)
                .frame(height: 1)
        }
    }
}

private struct MOOSButton: View {
    let onTap: () -> Void
    let onLongPress: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "circle.grid.2x2.fill")
            Text("MOOS")
                .font(.caption.bold().monospaced())
        }
        .foregroundStyle(.black)
        .padding(.horizontal, 11)
        .frame(height: 36)
        .background(MOOSTheme.accent, in: RoundedRectangle(cornerRadius: 11))
        .contentShape(Rectangle())
        .gesture(interactionGesture)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Tap for system controls. Long press for quick navigation.")
        .accessibilityAction {
            onTap()
        }
        .accessibilityAction(named: Text("Open radial menu")) {
            onLongPress()
        }
    }

    private var interactionGesture: some Gesture {
        LongPressGesture(minimumDuration: 0.45)
            .exclusively(before: TapGesture())
            .onEnded { value in
                switch value {
                case .first(let completed):
                    if completed {
                        onLongPress()
                    }
                case .second:
                    onTap()
                }
            }
    }
}

private struct RunningAppButton: View {
    let session: AppSession
    let onAction: (RunningAppAction) -> Void

    var body: some View {
        Button {
            onAction(.open)
        } label: {
            ZStack(alignment: .bottomTrailing) {
                Image(systemName: session.symbolName)
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(.white)
                    .frame(width: 34, height: 34)
                    .background(MOOSTheme.panel, in: RoundedRectangle(cornerRadius: 9))

                Circle()
                    .fill(session.state == .active ? MOOSTheme.accent : .gray)
                    .frame(width: 6, height: 6)
                    .padding(3)
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(session.displayName)
        .contextMenu {
            Button(RunningAppAction.open.title) { onAction(.open) }
            Button(RunningAppAction.close.title) { onAction(.close) }
            Button(RunningAppAction.newWindow.title) { onAction(.newWindow) }
            Button(RunningAppAction.forceClose.title, role: .destructive) {
                onAction(.forceClose)
            }
        }
    }
}

private struct ConnectionIndicator: View {
    let state: ConnectionState

    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: state.isReachable ? "network" : "network.slash")
                .foregroundStyle(state.isReachable ? MOOSTheme.accent : .orange)
            ViewThatFits(in: .horizontal) {
                Text(state.label)
                EmptyView()
            }
        }
        .font(.caption2.monospaced())
        .foregroundStyle(MOOSTheme.secondaryText)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Connection \(state.label)")
    }
}
