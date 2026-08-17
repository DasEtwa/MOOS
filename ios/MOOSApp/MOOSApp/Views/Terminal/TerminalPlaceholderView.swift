import SwiftUI

struct TerminalPlaceholderView: View {
    var body: some View {
        ShellPlaceholderView(
            title: "Terminal",
            symbolName: "terminal",
            message: "Remote terminal access waits for authenticated transport."
        )
    }
}
