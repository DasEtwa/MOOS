import SwiftUI

struct SectionLabel: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.caption.bold().monospaced())
            .tracking(1.3)
            .foregroundStyle(MOOSTheme.secondaryText)
    }
}
