import SwiftUI

struct ShellPlaceholderView: View {
    let title: String
    let symbolName: String
    let message: String

    var body: some View {
        ZStack {
            MOOSTheme.background.ignoresSafeArea()

            VStack(spacing: 18) {
                Image(systemName: symbolName)
                    .font(.system(size: 42, weight: .light))
                    .foregroundStyle(MOOSTheme.accent)
                Text(title)
                    .font(.title2.bold())
                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(MOOSTheme.secondaryText)
                    .multilineTextAlignment(.center)
            }
            .padding(32)
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}
