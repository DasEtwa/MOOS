# MOOS iOS shell

`MOOSApp` is an experimental native SwiftUI client. It renders the shell on the
iPhone and consumes only client-facing concepts such as a Host, Personal MOOS,
and connection state.

The production app starts with no configured Host. Add Host accepts only a
literal Tailscale IPv4 or IPv6 address and persists that address, port, and
non-secret device ID in `UserDefaults`; the paired device key
is stored separately in the iOS Keychain. The app opens one Network.framework
TCP connection, completes the MOOS Gateway v1 HMAC challenge, and then sends the
bounded Protocol v1 `status` request. Both client and Gateway prove possession
of the paired key over fresh nonces. The UI becomes Connected only after the
mutual authentication and strictly validated Protocol-v1 response, then renders
the returned Personal runtime state.
Preview and test fixtures remain in `MockMOOSStateService`; production does not
instantiate that service or fall back to its data.

The repository's `moosd` still exposes only its local Unix socket. The separate
`moos-gateway` binds to an explicit Tailscale address, authenticates the concrete
MOOS device, checks its status grant, and forwards the unchanged Protocol-v1
frame. Tailscale provides encrypted transport but membership alone is not MOOS
application authentication.

Open `MOOSApp.xcodeproj` in Xcode and run the `MOOSApp` scheme on an iPhone
simulator. The deployment target is iOS 17.0 and the project has no third-party
dependencies. The committed `AppIcon` asset catalog uses the same MOOS artwork
that is published with the SideStore source.

With no saved Host, Home shows only the MOOS title, `No host connected.`, and
Add Host. A saved Host moves through Connecting, Connected, and Disconnected
states. Retry, Edit Host, and Remove Host remain available after failure.

## State lifetimes

- Local preferences use a small `UserDefaults` store for layout density, theme,
  animations, and app ordering.
- Remote metadata has a versioned, atomically written JSON cache. App icon
  metadata includes a fallback symbol plus an optional content hash so icon
  bytes can be cached independently later.
- Live state is modeled separately for metrics, sessions, latency, connection,
  and synchronized uptime. Services publish snapshots with `AsyncStream` rather
  than requiring view polling.

Legacy shell previews may still use mock widgets, apps, sessions, latency, and
uptime, but none of those values appear in the normal app experience. QR
pairing, biometric gating, terminal streaming, application streaming, and
lifecycle controls remain outside this slice.

## Build verification

`.github/workflows/ios.yml` selects Xcode 16.4 on a GitHub-hosted `macos-15`
runner, builds for the iPhone 16 / iOS 18.5 simulator, and then runs the unit
tests without rebuilding. A separate job builds the Release app against the
physical-device `iphoneos` SDK with an arm64-only executable, verifies its
Mach-O platform is `IOS` rather than `IOSSIMULATOR`, and packages
`Payload/MOOSApp.app` as the `MOOS.ipa` workflow artifact. All build commands
disable code signing. The workflow uses no Apple certificates, provisioning
profiles, signing secrets, package manager, or third-party dependency bootstrap.
The resulting IPA is a standard bundle artifact for inspection and later
signing; it cannot be installed on an iPhone until it is signed and provisioned.

An explicit `ios-v*` tag invokes the separate guarded release workflow. It
reuses the same simulator tests and physical-device packaging path, verifies
the tag against Xcode metadata, publishes the unsigned app as the stable
`MOOS.ipa` GitHub Release asset, and deploys SideStore update metadata to GitHub
Pages. Signing and installation remain entirely external in SideStore. See
[`distribution/ios/README.md`](../../distribution/ios/README.md) for the
one-time Pages setting and exact release procedure.

The equivalent project and scheme are:

```text
project: ios/MOOSApp/MOOSApp.xcodeproj
scheme:  MOOSApp
```

The workflow must be observed after the branch is pushed or opened as a pull
request; local Linux validation cannot execute Xcode or an iOS simulator.
