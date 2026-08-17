# MOOS Protocol v1

This document defines the first client-facing MOOS protocol contract. It is
transport-neutral: the current implementation uses a local Unix stream socket,
but a future authenticated transport may carry the same frames without
exposing QEMU, systemd, filesystem, or process details.

## Transport and framing

- Encoding is UTF-8 newline-delimited JSON (NDJSON).
- Each frame is one JSON object followed by `\n`.
- A stream may fragment one frame or concatenate many frames; implementations
  must incrementally decode it.
- The encoded JSON object is limited to 16 KiB, excluding the newline.
- Invalid, non-object, truncated, or oversized frames produce structured local
  errors and must not terminate the daemon.
- When a peer half-closes its write side after an incomplete frame, the Host
  returns the structured error before closing its read side. A full disconnect
  is isolated to that client because no response channel remains.
- The current transport is local Unix `SOCK_STREAM` only. No TCP, Tailscale, or
  public listener is part of v1.

## Control requests

Every control request contains exactly:

```json
{"protocolVersion":1,"operation":"status"}
```

`protocolVersion` is exactly the JSON integer `1`. Booleans, strings, and
fractional numbers such as `true`, `"1"`, and `1.0` are not version 1.

The supported operations are:

| Operation | Meaning |
| --- | --- |
| `status` | Return Host-facing status for Personal. |
| `personal.status` | Return Personal runtime status. |
| `personal.start` | Start the fixed Personal runtime. |
| `personal.stop` | Stop the fixed Personal runtime. |
| `personal.terminal.open` | Open the Personal guest serial-console channel. |

Requests contain no instance paths, QEMU arguments, systemd unit names, host
PIDs, or arbitrary command fields. No Host `/exec` operation exists.

## Control responses

Successful responses have this shape:

```json
{
  "protocolVersion": 1,
  "ok": true,
  "operation": "personal.status",
  "data": {
    "personal": {
      "identity": "personal",
      "state": "running",
      "result": "success"
    }
  },
  "events": []
}
```

The public runtime states are `starting`, `running`, `stopping`, `stopped`,
`failed`, and `unknown`. `result` may be omitted or `null`; otherwise it is a
public outcome string such as `success` or `exit-code`. Implementation-specific
paths and PIDs are excluded. `events` is an array of event objects and is empty
for the operations currently implemented.

Terminal-open success returns only:

```json
{
  "protocolVersion": 1,
  "ok": true,
  "operation": "personal.terminal.open",
  "data": {"channel": "serial-console"},
  "events": []
}
```

## Structured errors

Control errors have this shape:

```json
{
  "protocolVersion": 1,
  "ok": false,
  "error": {"code": "not_running", "message": "Personal is not running"}
}
```

Defined control error codes are `unsupported_protocol`, `invalid_request`,
`request_too_large`, `unknown_operation`, `already_running`, `not_running`,
`terminal_busy`, and `runtime_error`. Clients must handle unknown future codes
as generic errors.

Clients validate `protocolVersion`, the boolean `ok`, and the operation-specific
response data before using it. Unknown response fields are ignored. A response
for a different operation does not satisfy the outstanding request. Complete
requests sent on one control stream receive responses in the same order.

## Terminal channel

After a successful `personal.terminal.open`, the same stream carries terminal
frames. These frames inherit protocol version from the opening request:

Client to Host:

```json
{"type":"input","data":"moos-info\n"}
{"type":"close"}
```

Host to client:

```json
{"type":"output","data":"version: MOOS 0.1.0-dev\n"}
{"type":"error","code":"invalid_frame"}
```

Terminal error codes are `invalid_frame`, `frame_too_large`, and
`terminal_unavailable`. Clients must treat unknown future codes as generic
terminal failures and ignore unknown fields on Host-to-client terminal frames.
The Host rejects unknown fields on client-to-Host terminal frames.

Terminal input is delivered only to the Personal guest console. Closing the
client channel does not stop Personal. A new client may open a new channel
while the guest remains running; only one terminal session is allowed at a
time by the current host runtime.

## Compatibility rules

- `protocolVersion` is mandatory, must be an integer, and unsupported versions
  are rejected by both Host and client.
- v1 clients must ignore unknown response fields such as future event fields.
- v1 servers reject unknown request fields rather than guessing their meaning.
- Unknown error codes are forward-compatible generic errors.
- Host implementation details never become protocol fields.
- Authentication and remote transport are deliberately outside v1 and are
  specified by later slices.
