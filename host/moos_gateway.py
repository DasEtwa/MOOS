"""Authenticated Tailscale-only gateway for the local MOOS control plane."""

from __future__ import annotations

import hmac
import fcntl
import grp
import ipaddress
import logging
import os
import socket
import struct
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from moos_gateway_auth import (
    AuthorizedDevice,
    DeviceStore,
    GatewayAuthError,
    make_authenticated_frame,
    make_authentication_error,
    make_challenge,
    verify_authenticate_frame,
)
from moos_protocol import (
    ByteTransport,
    FrameDecoder,
    FrameResult,
    ProtocolError,
    encode_frame,
    make_control_error,
    parse_control_request,
    parse_control_response,
)


READ_BYTES = 4096
AUTH_TIMEOUT_SECONDS = 10.0
IDLE_TIMEOUT_SECONDS = 20.0
MAX_CLIENTS = 32
MAX_CLIENTS_PER_SOURCE = 4
MAX_AUTH_ATTEMPTS_PER_SOURCE = 20
MAX_AUTH_ATTEMPTS_GLOBAL = 120
AUTH_RATE_WINDOW_SECONDS = 60.0
TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_IPV6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
LOG = logging.getLogger(__name__)


class GatewayError(RuntimeError):
    pass


class AdmissionLimiter:
    """Bound concurrent sessions and authentication churn per tailnet source."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.active: dict[str, int] = {}
        self.global_attempts: deque[float] = deque()
        self.source_attempts: dict[str, deque[float]] = {}

    def admit(self, source: str) -> bool:
        now = self.clock()
        threshold = now - AUTH_RATE_WINDOW_SECONDS
        with self.lock:
            while self.global_attempts and self.global_attempts[0] <= threshold:
                self.global_attempts.popleft()
            attempts = self.source_attempts.setdefault(source, deque())
            while attempts and attempts[0] <= threshold:
                attempts.popleft()
            if (
                self.active.get(source, 0) >= MAX_CLIENTS_PER_SOURCE
                or len(attempts) >= MAX_AUTH_ATTEMPTS_PER_SOURCE
                or len(self.global_attempts) >= MAX_AUTH_ATTEMPTS_GLOBAL
            ):
                return False
            attempts.append(now)
            self.global_attempts.append(now)
            self.active[source] = self.active.get(source, 0) + 1
            return True

    def release(self, source: str) -> None:
        with self.lock:
            count = self.active.get(source, 0)
            if count <= 1:
                self.active.pop(source, None)
            else:
                self.active[source] = count - 1


def validate_process_groups(
    *,
    primary_gid: int | None = None,
    control_gid: int | None = None,
    supplementary_gids: set[int] | None = None,
) -> None:
    """Reject a network service identity with unexpected host groups."""

    primary_gid = os.getgid() if primary_gid is None else primary_gid
    control_gid = (
        grp.getgrnam("moos-control").gr_gid if control_gid is None else control_gid
    )
    groups = set(os.getgroups()) if supplementary_gids is None else supplementary_gids
    if control_gid not in groups or not groups <= {primary_gid, control_gid}:
        raise ValueError("gateway process has unexpected supplementary groups")


def validate_tailscale_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("listen address must be a literal Tailscale IP") from None
    if address not in TAILSCALE_IPV4 and address not in TAILSCALE_IPV6:
        raise ValueError("listen address is outside the Tailscale ranges")
    return str(address)


def tailscale_interface_addresses(interface: str = "tailscale0") -> frozenset[str]:
    """Return addresses actually assigned to the named Tailscale interface."""

    try:
        socket.if_nametoindex(interface)
    except OSError:
        raise ValueError(f"required network interface is unavailable: {interface}") from None

    addresses: set[str] = set()
    request = struct.pack("256s", interface.encode("ascii")[:15])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as query:
        try:
            response = fcntl.ioctl(query.fileno(), 0x8915, request)  # SIOCGIFADDR
        except OSError:
            pass
        else:
            addresses.add(socket.inet_ntoa(response[20:24]))

    try:
        ipv6_lines = Path("/proc/net/if_inet6").read_text(encoding="ascii").splitlines()
    except OSError:
        ipv6_lines = []
    for line in ipv6_lines:
        fields = line.split()
        if len(fields) == 6 and fields[5] == interface:
            try:
                addresses.add(str(ipaddress.IPv6Address(int(fields[0], 16))))
            except ValueError:
                continue
    return frozenset(addresses)


def validate_tailscale_interface_address(
    value: str,
    *,
    assigned_addresses: frozenset[str] | set[str] | None = None,
    interface: str = "tailscale0",
) -> str:
    """Require a Tailscale-range address that is assigned to tailscale0."""

    address = validate_tailscale_address(value)
    assigned = (
        tailscale_interface_addresses(interface)
        if assigned_addresses is None
        else assigned_addresses
    )
    normalized = {str(ipaddress.ip_address(item)) for item in assigned}
    if address not in normalized:
        raise ValueError(f"listen address is not assigned to {interface}")
    return address


def _send(connection: ByteTransport, frame: dict) -> bool:
    try:
        connection.sendall(encode_frame(frame))
        return True
    except (OSError, ValueError):
        return False


def _receive_frame(
    connection: socket.socket,
    decoder: FrameDecoder,
    pending: deque[FrameResult],
    *,
    deadline: float | None = None,
) -> dict:
    while True:
        while pending:
            result = pending.popleft()
            if result.error is not None or result.frame is None:
                raise GatewayError("invalid frame")
            return result.frame
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("authentication deadline expired")
            connection.settimeout(remaining)
        data = connection.recv(READ_BYTES)
        if not data:
            final = decoder.finish()
            if final is not None:
                raise GatewayError("truncated frame")
            raise GatewayError("connection closed")
        pending.extend(decoder.feed(data))


def _connect_backend(socket_path: Path) -> socket.socket:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(socket_path))
    except BaseException:
        connection.close()
        raise
    return connection


class GatewaySession:
    def __init__(
        self,
        devices: DeviceStore,
        backend_connector: Callable[[], socket.socket],
        *,
        auth_timeout: float = AUTH_TIMEOUT_SECONDS,
    ) -> None:
        self.devices = devices
        self.backend_connector = backend_connector
        self.auth_timeout = auth_timeout

    def handle(self, connection: socket.socket) -> None:
        with connection:
            try:
                device, server_nonce, client_nonce = self._authenticate(connection)
            except (GatewayAuthError, GatewayError, OSError, ValueError):
                _send(connection, make_authentication_error())
                return

            connection.settimeout(IDLE_TIMEOUT_SECONDS)
            LOG.info("authenticated MOOS device %s", device.device_id)
            try:
                self._forward_control(connection, device, server_nonce, client_nonce)
            except (GatewayError, OSError, ValueError):
                return

    def _authenticate(
        self, connection: socket.socket
    ) -> tuple[AuthorizedDevice, bytes, bytes]:
        deadline = time.monotonic() + self.auth_timeout
        challenge_frame, server_nonce = make_challenge()
        if not _send(connection, challenge_frame):
            raise GatewayError("challenge send failed")
        decoder = FrameDecoder()
        pending: deque[FrameResult] = deque()
        frame = _receive_frame(connection, decoder, pending, deadline=deadline)
        if pending:
            raise GatewayAuthError("authentication failed")
        device, client_nonce = verify_authenticate_frame(
            frame, server_nonce, self.devices.load()
        )
        return device, server_nonce, client_nonce

    def _forward_control(
        self,
        remote: socket.socket,
        authenticated_device: AuthorizedDevice,
        server_nonce: bytes,
        client_nonce: bytes,
    ) -> None:
        remote_decoder = FrameDecoder()
        remote_pending: deque[FrameResult] = deque()
        backend: socket.socket | None = None
        backend_decoder = FrameDecoder()
        backend_pending: deque[FrameResult] = deque()
        try:
            if not _send(
                remote,
                make_authenticated_frame(
                    authenticated_device, server_nonce, client_nonce
                ),
            ):
                return
            while True:
                try:
                    frame = _receive_frame(remote, remote_decoder, remote_pending)
                except socket.timeout:
                    return
                except GatewayError:
                    _send(remote, make_control_error("invalid_request", "Invalid request"))
                    return

                try:
                    request = parse_control_request(frame)
                except ProtocolError as error:
                    if not _send(remote, make_control_error(error.code, error.message)):
                        return
                    continue

                current_device = self.devices.load().get(authenticated_device.device_id)
                if (
                    current_device is None
                    or current_device.revoked
                    or not hmac.compare_digest(
                        current_device.key, authenticated_device.key
                    )
                ):
                    _send(remote, make_control_error("forbidden", "Device access revoked"))
                    return
                if request.operation not in current_device.permissions:
                    if not _send(
                        remote,
                        make_control_error("forbidden", "Operation is not permitted"),
                    ):
                        return
                    continue

                if backend is None:
                    try:
                        backend = self.backend_connector()
                        backend.settimeout(IDLE_TIMEOUT_SECONDS)
                    except OSError:
                        _send(
                            remote,
                            make_control_error("runtime_error", "MOOS is unavailable"),
                        )
                        return
                try:
                    backend.sendall(encode_frame(frame))
                    response = _receive_frame(
                        backend, backend_decoder, backend_pending
                    )
                    parse_control_response(
                        response, expected_operation=request.operation
                    )
                except (OSError, GatewayError, ProtocolError, ValueError):
                    _send(
                        remote,
                        make_control_error("runtime_error", "MOOS is unavailable"),
                    )
                    return
                if not _send(remote, response):
                    return
        finally:
            if backend is not None:
                backend.close()


def serve(
    listen_address: str,
    port: int,
    devices: DeviceStore,
    moosd_socket: Path,
    *,
    listener: socket.socket | None = None,
) -> None:
    """Serve authenticated Protocol v1 only on a validated Tailscale address."""

    owns_listener = listener is None
    if listener is None:
        listen_address = validate_tailscale_interface_address(listen_address)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("port must be from 1 to 65535")
        family = socket.AF_INET6 if ":" in listen_address else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((listen_address, port))
        listener.listen(16)

    session = GatewaySession(
        devices,
        lambda: _connect_backend(moosd_socket),
    )
    clients = threading.BoundedSemaphore(MAX_CLIENTS)
    admissions = AdmissionLimiter()

    def handle_client(connection: socket.socket, source: str) -> None:
        try:
            session.handle(connection)
        finally:
            admissions.release(source)
            clients.release()

    try:
        while True:
            connection, peer = listener.accept()
            source = str(peer[0]) if isinstance(peer, tuple) and peer else "unknown"
            if not clients.acquire(blocking=False):
                connection.close()
                continue
            if not admissions.admit(source):
                connection.close()
                clients.release()
                continue
            worker = threading.Thread(
                target=handle_client,
                args=(connection, source),
                daemon=True,
                name="moos-gateway-client",
            )
            try:
                worker.start()
            except RuntimeError:
                connection.close()
                admissions.release(source)
                clients.release()
    finally:
        if owns_listener:
            listener.close()
