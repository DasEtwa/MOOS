import SwiftUI

struct AppGridView: View {
    let applications: [ShellApp]

    private let columns = [
        GridItem(.adaptive(minimum: 76, maximum: 96), spacing: 18),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SectionLabel(title: "APPS")

            LazyVGrid(columns: columns, alignment: .leading, spacing: 20) {
                ForEach(applications) { application in
                    NavigationLink(value: application.destination) {
                        VStack(spacing: 9) {
                            Image(systemName: application.symbolName)
                                .font(.system(size: 25, weight: .medium))
                                .foregroundStyle(MOOSTheme.accent)
                                .frame(width: 58, height: 58)
                                .background(
                                    LinearGradient(
                                        colors: [MOOSTheme.accent.opacity(0.16), MOOSTheme.panel],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    ),
                                    in: RoundedRectangle(cornerRadius: 16)
                                )
                                .overlay {
                                    RoundedRectangle(cornerRadius: 16)
                                        .stroke(MOOSTheme.panelBorder, lineWidth: 1)
                                }
                            Text(application.name)
                                .font(.caption)
                                .foregroundStyle(.white.opacity(0.86))
                                .lineLimit(1)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}
