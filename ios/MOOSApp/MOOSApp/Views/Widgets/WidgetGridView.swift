import SwiftUI

struct WidgetGridView: View {
    let widgets: [ShellWidget]

    private let columns = [
        GridItem(.adaptive(minimum: 150, maximum: 240), spacing: 12),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionLabel(title: "WIDGETS")

            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(widgets) { widget in
                    WidgetCard(widget: widget)
                }
            }
        }
    }
}

private struct WidgetCard: View {
    let widget: ShellWidget

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Image(systemName: widget.symbolName)
                    .foregroundStyle(MOOSTheme.accent)
                Text(widget.title.uppercased())
                    .font(.caption.bold().monospaced())
                    .foregroundStyle(MOOSTheme.secondaryText)
                Spacer()
                if widget.kind == .agent {
                    Circle()
                        .fill(MOOSTheme.accent)
                        .frame(width: 7, height: 7)
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                Text(widget.value)
                    .font(.system(size: 22, weight: .semibold, design: .rounded))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Text(widget.detail)
                    .font(.caption)
                    .foregroundStyle(MOOSTheme.secondaryText)
                    .lineLimit(1)
            }

            if let progress = widget.progress {
                ProgressView(value: progress)
                    .tint(MOOSTheme.accent)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 112, alignment: .topLeading)
        .padding(15)
        .background(MOOSTheme.panel, in: RoundedRectangle(cornerRadius: 18))
        .overlay {
            RoundedRectangle(cornerRadius: 18)
                .stroke(MOOSTheme.panelBorder, lineWidth: 1)
        }
    }
}
