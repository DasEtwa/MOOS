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

## State lifetimes

- Local preferences use a small `UserDefaults` store for layout density, theme,
  animations, and app ordering.
- Remote metadata has a versioned, atomically written JSON cache. App icon
  metadata includes a fallback symbol plus an optional content hash so icon
  bytes can be cached independently later.
- Live state is modeled separately for metrics, sessions, latency, connection,
  and synchronized uptime. Services publish snapshots with `AsyncStream` rather
  than requiring view polling.

The connected app and the reconnecting/offline previews still use mock data.
Offline snapshots deliberately retain cached widgets and apps. The Protocol v1
request type is transport-independent; there is still no network transport,
pairing, credential, or live backend client in this project.

## Build verification

`.github/workflows/ios.yml` selects Xcode 16.4 on a GitHub-hosted `macos-15`
runner, builds for the iPhone 16 / iOS 18.5 simulator, and then runs the unit
tests without rebuilding. Both commands disable code signing. The workflow uses
no Apple certificates, provisioning profiles, signing secrets, package manager,
or third-party dependency bootstrap.

The equivalent project and scheme are:

```text
project: ios/MOOSApp/MOOSApp.xcodeproj
scheme:  MOOSApp
```

The workflow must be observed after the branch is pushed or opened as a pull
request; local Linux validation cannot execute Xcode or an iOS simulator.
