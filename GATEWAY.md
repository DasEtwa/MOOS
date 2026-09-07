# MOOS Gateway v1

The MOOS Gateway is the authenticated remote entry point for Protocol v1. It
does not replace `moosd`, expose the guest, or add remote operations. It accepts
connections only on an explicitly configured Tailscale address and forwards
authorized Protocol-v1 frames to the existing local control plane.

```text
iPhone → Tailscale → moos-gateway → local moosd → Personal MOOS
```

Tailscale provides encrypted transport and tailnet routing. MOOS device
authentication remains mandatory: tailnet membership or a source IP is never a
MOOS identity. Gateway v1 intentionally does not add a second TLS layer. The
transport routing and host identity are therefore supplied by Tailscale, and
the service verifies that its configured address is really assigned to
`tailscale0`. The Gateway additionally proves possession of the paired device
key to the client. Restrict the gateway port to the intended source devices
with a tailnet ACL as an additional admission and denial-of-service boundary.

## Framing and limits

Gateway authentication uses the same bounded UTF-8 NDJSON framing as Protocol
v1. Each authentication frame is one JSON object followed by `\n` and is
limited to 16 KiB. Unknown fields, malformed JSON, invalid types, truncated
frames, and oversized frames fail the authentication session.

## Authentication handshake

The Gateway sends a fresh 32-byte base64url nonce immediately after accepting a
connection:

```json
{"gatewayVersion":1,"type":"challenge","nonce":"..."}
```

The client creates its own fresh 32-byte nonce and sends:

```json
{
  "gatewayVersion": 1,
  "type": "authenticate",
  "deviceId": "019...",
  "clientNonce": "...",
  "proof": "..."
}
```

`proof` is HMAC-SHA256 with the paired 32-byte device key over these exact UTF-8
bytes, including the final newline:

```text
MOOS-GATEWAY-AUTH-V1\n
<server nonce>\n
<client nonce>\n
<device ID>\n
```

On success the Gateway returns the effective operation grants:

```json
{
  "gatewayVersion": 1,
  "type": "authenticated",
  "deviceId": "019...",
  "permissions": ["status"],
  "serverProof": "..."
}
```

`serverProof` is HMAC-SHA256 with the same paired key and transcript fields,
but the distinct context `MOOS-GATEWAY-SERVER-V1\n`. The iOS app validates it
before sending Protocol v1 or publishing Connected. This provides mutual
application authentication without adding a second encrypted transport layer.

All authentication failures return the same generic error and close the
connection. Nonces and proofs are never credentials and are not reusable.
Device keys are generated independently for every device, shown once by the
administrator tool, stored in the iOS Keychain, and individually revocable or
rotatable.

The pairing code is a possession credential until it is imported. It can be
copied: importing the same code on another client clones that device identity,
and revocation or rotation then affects every copy. Transfer it privately,
delete any copy after import, and create one device entry per physical device.
Hardware-bound asymmetric enrollment is intentionally deferred to a later
protocol version.

Unauthenticated admission is bounded globally and per source IP, with no more
than four concurrent sessions from one tailnet source and a rolling attempt
limit. The ten-second authentication deadline applies to the entire frame, not
to each individual read.

## Authorization and forwarding

After authentication, the stream carries unmodified Protocol-v1 control
frames. The Gateway parses every request before forwarding it and checks the
requested operation against the authenticated device's grants. Version 1 of
the remote Gateway grants only `status`; lifecycle and terminal operations are
not remotely exposed yet. A denied operation returns the Protocol-v1 error code
`forbidden` without revealing host details.

The Gateway never accepts QEMU arguments, host paths, commands, socket paths,
or credentials from a client. `moosd` remains local and independently limits
the `moos-gateway` Unix peer to `status`, so a compromised Gateway process
cannot use its local socket membership for lifecycle or terminal operations.

## Host state

Authorized devices are stored in a root-managed, atomically replaced JSON file.
Administrator mutations are serialized with a root-owned lock so concurrent
commands cannot overwrite a revocation or rotation. The Gateway has read-only
access. Each entry contains a stable device ID,
display name, random device key, operation grants, timestamps, and a revocation
flag. Secrets must never be committed, copied into images, or written to logs.
The service refuses unsafe file ownership, modes, symlinks, hard links, or
unexpected process supplementary groups. A root-owned non-secret UID record
lets `moosd` retain the status-only peer boundary during NSS/account failure.

The production Gateway and `moos-gateway-device` are synchronous Rust
binaries. Rust owns bounded framing, HMAC authentication, store validation,
authorization, admission limits, and forwarding; there is no Python execution
path in the installed service. `rust-toolchain.toml` pins Rust 1.97.1 and
`Cargo.lock` fixes dependency resolution. The canonical release artifacts are
built as an unprivileged user with `scripts/build-gateway.sh`; the root setup
script never runs Cargo or downloads code. It accepts normal Cargo hardlinks
and checkout filesystem modes only as untrusted input, copies the artifacts to
root-owned mode-0755 staging files, and executes them solely as the locked
`moos-gateway` identity inside a transient systemd sandbox. The validator uses
a non-secret empty device store, a private PID namespace, and cannot access the
real device store or `moosd` socket. The reviewed service unit is pinned by its
SHA-256 digest before systemd activation.

The on-disk format remains exactly `schemaVersion: 1`. Device fields, UUID
format, key encoding, lock filename, pairing-code prefix, HMAC transcripts,
grants, service arguments, binary paths, and state paths are unchanged. The
existing iPhone credential is therefore designed to reconnect without a new
pairing code. That compatibility is covered by shared Swift/Rust vectors and
store tests. Real iPhone authentication against the Rust service was observed
on 2026-08-20 without changing the existing Gateway-v1 wire format.

The service refuses non-Tailscale listen addresses. It binds its listener with
`SO_BINDTODEVICE` to `tailscale0` before binding the configured address, so the
same socket both validates and enforces the interface assignment without
requiring Netlink. Its unchanged systemd sandbox also restricts network access
to that interface and the Tailscale IPv4 `100.64.0.0/10` and IPv6
`fd7a:115c:a1e0::/48` ranges. Supporting public WAN or ordinary LAN listeners
would require a separately reviewed encrypted transport and is outside Gateway
v1.

## Install and pair an iPhone

Install Tailscale on the Host and iPhone, sign both into the intended tailnet,
and use a tailnet ACL to allow the iPhone to reach only the Gateway TCP port.
Build the pinned Gateway and administrator bundle as a normal user, sign the
bundle in the external release-signing environment, and authenticate it as
described in `ADMIN_RELEASES.md`. Then refresh the local control plane and
install the Gateway only from that root-owned release:

```sh
ADMIN_ROOT=/usr/lib/moos/admin-current
sudo "$ADMIN_ROOT/scripts/setup-control-plane.sh" --source-root "$ADMIN_ROOT"
sudo "$ADMIN_ROOT/scripts/setup-gateway.sh" \
  --tailscale-address "$(tailscale ip -4)" \
  --port 7411 \
  --source-root "$ADMIN_ROOT" \
  --binary-dir "$ADMIN_ROOT/target/release"
sudo moos-gateway-device add --name "My iPhone" --allow status
```

The final command prints the device ID and pairing code once. On the iPhone,
enable Tailscale, open MOOS, choose **Add Host**, enter the same Tailscale IP,
port `7411`, and the complete pairing code, then save. The client accepts only
literal addresses in Tailscale's IPv4 or IPv6 ranges so Gateway frames cannot
silently leave the encrypted Tailscale route. Accept the iOS local
network permission if prompted. `Connected` appears only after Gateway device
authentication and a valid Protocol-v1 `status` response; the Personal card
then shows the state returned by `moosd`.

Inspect the Host without exposing secrets:

```sh
sudo systemctl status moos-gateway.service
sudo journalctl -u moos-gateway.service
sudo moos-gateway-device list
```

Removing the Host in the app deletes its local settings and Keychain key, but
does not revoke the Host record. Revoke it explicitly when retiring a device:

```sh
sudo moos-gateway-device revoke DEVICE_ID
```

The Gateway reloads the device record before every Protocol-v1 operation. A
revoked or rotated key therefore cannot issue another status request on an
already authenticated TCP connection; the server returns `forbidden` and
closes that session. The iOS app polls status every five seconds while active,
so its visible state changes to Disconnected on the next poll. Tailscale cannot
override this application-level decision. When multiple records have the same
display name, use the device ID logged by `moos-gateway` to revoke the concrete
credential in use.

The installer statically accepts bounded regular ELF inputs, including Cargo's
usual hardlinks, then gives only the root-owned staging copies canonical mode
0755. Matching version, address/interface, process identity, and temporary
device-store validation happens inside the sandbox before activation. An
existing real device store is never rewritten or silently repaired. If file
replacement, systemd reload, restart, or the active-service check fails, the
installer attempts to restore the prior binaries, unit, configuration, and
documentation.
Reinstalling the previous tagged checkout is also schema-compatible; do not
copy the secret device store into Git or an ad-hoc rollback bundle.

The current stripped x86-64 release artifacts are approximately 654 KB for the
service and 560 KB for the administrator tool. They run outside the guest, so
the MOOS rootfs size and boot path are unchanged.

## Operator diagnostics

`moos setup --remote` explains optional iPhone status prerequisites;
`moos doctor` checks an installed Gateway automatically. The CLI reports
Tailscale as unavailable, disconnected, connected, unhealthy or indeterminate,
using a bounded local status probe. It does not print the raw JSON, addresses,
peer names, login URLs or health messages. Gateway service activity and
Tailscale connectivity do not establish a paired MOOS session or validate
end-to-end reachability; test authenticated status on the intended iPhone.

`moos setup --explain remote` guides installation/sign-in using Tailscale's own
flow and the existing administrator installation/pairing procedure above.
It does not configure Tailscale, change ACLs, read the credential store, invoke
pairing, or grant remote operations. Device checks remain explicitly unverified
rather than treating inaccessible credentials as an empty device list.
