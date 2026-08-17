# MOOS iOS shell

`MOOSApp` is an experimental native SwiftUI client. It renders the shell on the
iPhone and consumes only client-facing concepts such as a Host, Personal MOOS,
connection state, apps, widgets, and sessions.

The project intentionally has no live transport, authentication, pairing,
Tailscale integration, remote application streaming, or host-runtime knowledge.
Its initial data comes from `MockMOOSStateService` through a service protocol so
the presentation can stay intact when an authenticated Protocol v1 client is
added after M7/M8.

Open `MOOSApp.xcodeproj` in Xcode and run the `MOOSApp` scheme on an iPhone
simulator. The deployment target is iOS 17.0 and the project has no third-party
dependencies.

The current Home is entirely local: it renders mock Luna/Sol and resource
widgets, an app grid, connection/time/session indicators, and the experimental
MOOS menus. Tap the bottom-left MOOS button for non-functional system controls;
long-press it for the replaceable radial navigation prototype. Running-app
context actions are also placeholders and do not issue backend commands.
