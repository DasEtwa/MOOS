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

The service refuses non-Tailscale listen addresses and addresses that are not
assigned to `tailscale0`; its systemd sandbox also restricts network access to
that interface and the Tailscale IPv4 `100.64.0.0/10` and IPv6
`fd7a:115c:a1e0::/48` ranges. Supporting public WAN or ordinary LAN listeners
would require a separately reviewed encrypted transport and is outside Gateway
v1.

## Install and pair an iPhone

Install Tailscale on the Host and iPhone, sign both into the intended tailnet,
and use a tailnet ACL to allow the iPhone to reach only the Gateway TCP port.
Then refresh the local control plane (this installs the `moosd` Gateway peer
restriction) and install the Gateway on the Host:

```sh
sudo ./scripts/setup-control-plane.sh --source-root "$PWD"
sudo ./scripts/setup-gateway.sh \
  --tailscale-address "$(tailscale ip -4)" \
  --port 7411 \
  --source-root "$PWD"
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
