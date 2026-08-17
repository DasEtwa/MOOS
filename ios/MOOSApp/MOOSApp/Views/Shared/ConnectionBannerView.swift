import Foundation
import SwiftUI

struct ConnectionBannerView: View {
    let state: ConnectionState
    let lastSynchronizedAt: Date?
    let isShowingCachedMetadata: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: state == .offline ? "network.slash" : "arrow.triangle.2.circlepath")
                .foregroundStyle(.orange)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(MOOSTheme.secondaryText)

                if let lastSynchronizedAt {
                    HStack(spacing: 4) {
                        Text("Last synchronized")
                        Text(lastSynchronizedAt, style: .relative)
                    }
                    .font(.caption2.monospaced())
                    .foregroundStyle(MOOSTheme.secondaryText)
                }
            }

            Spacer(minLength: 0)
        }
        .padding(14)
        .background(Color.orange.opacity(0.09), in: RoundedRectangle(cornerRadius: 16))
        .overlay {
            RoundedRectangle(cornerRadius: 16)
                .stroke(Color.orange.opacity(0.28), lineWidth: 1)
        }
    }

    private var title: String {
        switch state {
        case .connected:
            return "Connected"
        case .connecting:
            return "Connecting to MOOS"
        case .reconnecting:
            return "Reconnecting to MOOS"
        case .offline:
            return "MOOS is offline"
        }
    }

    private var message: String {
        if isShowingCachedMetadata {
            return "Your cached desktop remains available while live state is paused."
        }
        return "The local shell is ready while it waits for remote state."
    }
}
