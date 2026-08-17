import Foundation
import SwiftUI

struct RadialMenuItem: Identifiable, Equatable {
    let id: String
    let title: String
    let symbolName: String
    let destination: ShellDestination
}

extension Array where Element == RadialMenuItem {
    static var shellDefaults: [RadialMenuItem] {
        [
            RadialMenuItem(
                id: "settings",
                title: "Settings",
                symbolName: "gearshape",
                destination: .settings
            ),
            RadialMenuItem(
                id: "app-store",
                title: "App Store",
                symbolName: "shippingbox",
                destination: .appStore
            ),
            RadialMenuItem(
                id: "files",
                title: "Files",
                symbolName: "folder",
                destination: .files
            ),
            RadialMenuItem(
                id: "terminal",
                title: "Terminal",
                symbolName: "terminal",
                destination: .terminal
            ),
            RadialMenuItem(
                id: "instances",
                title: "Instances",
                symbolName: "server.rack",
                destination: .instances
            ),
            RadialMenuItem(
                id: "apps",
                title: "Apps",
                symbolName: "square.grid.2x2",
                destination: .apps
            ),
        ]
    }
}

struct RadialMenuView: View {
    let items: [RadialMenuItem]
    let onSelect: (RadialMenuItem) -> Void
    let onDismiss: () -> Void

    var body: some View {
        GeometryReader { geometry in
            let center = CGPoint(x: geometry.size.width / 2, y: geometry.size.height / 2)
            let radius = min(geometry.size.width * 0.34, 142)

            ZStack {
                Color.black.opacity(0.82)
                    .ignoresSafeArea()
                    .onTapGesture(perform: onDismiss)

                Circle()
                    .stroke(MOOSTheme.accent.opacity(0.22), lineWidth: 1)
                    .frame(width: radius * 2.15, height: radius * 2.15)
                    .position(center)

                ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                    let angle = -Double.pi / 2
                        + (Double(index) / Double(max(items.count, 1))) * Double.pi * 2

                    Button {
                        onSelect(item)
                    } label: {
                        VStack(spacing: 5) {
                            Image(systemName: item.symbolName)
                                .font(.system(size: 19, weight: .medium))
                            Text(item.title)
                                .font(.caption2.weight(.semibold))
                                .lineLimit(1)
                        }
                        .foregroundStyle(.white)
                        .frame(width: 72, height: 60)
                        .background(Color.black, in: RoundedRectangle(cornerRadius: 17))
                        .overlay {
                            RoundedRectangle(cornerRadius: 17)
                                .stroke(MOOSTheme.accent.opacity(0.55), lineWidth: 1)
                        }
                    }
                    .buttonStyle(.plain)
                    .position(
                        x: center.x + CGFloat(cos(angle)) * radius,
                        y: center.y + CGFloat(sin(angle)) * radius
                    )
                }

                Button(action: onDismiss) {
                    VStack(spacing: 3) {
                        Image(systemName: "circle.grid.2x2.fill")
                            .font(.title2)
                        Text("MOOS")
                            .font(.caption2.bold().monospaced())
                    }
                    .foregroundStyle(.black)
                    .frame(width: 76, height: 76)
                    .background(MOOSTheme.accent, in: Circle())
                }
                .buttonStyle(.plain)
                .position(center)
                .accessibilityLabel("Close MOOS radial menu")
            }
        }
    }
}
