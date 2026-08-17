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
