use std::collections::{BTreeMap, VecDeque};
use std::io::{self, Read, Write};
use std::net::{IpAddr, Shutdown, TcpListener, TcpStream};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::auth::{
    AuthorizedDevice, make_authenticated_frame, make_authentication_error, make_challenge,
    verify_authenticate_frame,
};
use crate::framing::{FrameDecoder, FrameResult, encode_frame};
use crate::platform_linux::{bind_tailscale_listener, io_error};
use crate::protocol::{make_control_error, parse_control_request, parse_control_response};
use crate::store::DeviceStore;

const READ_BYTES: usize = 4096;
pub const AUTH_TIMEOUT: Duration = Duration::from_secs(10);
pub const IDLE_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_CLIENTS: usize = 32;
const MAX_CLIENTS_PER_SOURCE: usize = 4;
const MAX_AUTH_ATTEMPTS_PER_SOURCE: usize = 20;
const MAX_AUTH_ATTEMPTS_GLOBAL: usize = 120;
const AUTH_RATE_WINDOW: Duration = Duration::from_secs(60);

trait TimedStream: Read + Write {
    fn set_read_deadline(&self, timeout: Option<Duration>) -> io::Result<()>;
}

impl TimedStream for TcpStream {
    fn set_read_deadline(&self, timeout: Option<Duration>) -> io::Result<()> {
        self.set_read_timeout(timeout)
    }
}

impl TimedStream for UnixStream {
    fn set_read_deadline(&self, timeout: Option<Duration>) -> io::Result<()> {
        self.set_read_timeout(timeout)
    }
}

struct FrameReader {
    decoder: FrameDecoder,
    pending: VecDeque<FrameResult>,
}

impl FrameReader {
    fn new() -> Self {
        Self {
            decoder: FrameDecoder::new(),
            pending: VecDeque::new(),
        }
    }

    fn receive<S: TimedStream>(
        &mut self,
        stream: &mut S,
        deadline: Option<Instant>,
    ) -> Result<Value, String> {
        loop {
            if let Some(result) = self.pending.pop_front() {
                return match result {
                    FrameResult::Frame(value) => Ok(value),
                    FrameResult::Error(_) => Err("invalid frame".to_owned()),
                };
            }
            let timeout = if let Some(deadline) = deadline {
                let remaining = deadline
                    .checked_duration_since(Instant::now())
                    .ok_or_else(|| "frame deadline expired".to_owned())?;
                Some(remaining.max(Duration::from_millis(1)))
            } else {
                None
            };
            stream
                .set_read_deadline(timeout)
                .map_err(|_| "frame timeout setup failed".to_owned())?;
            let mut chunk = [0_u8; READ_BYTES];
            let count = stream
                .read(&mut chunk)
                .map_err(|_| "frame read failed".to_owned())?;
            if count == 0 {
                return if self.decoder.finish().is_some() {
                    Err("truncated frame".to_owned())
                } else {
                    Err("connection closed".to_owned())
                };
            }
            self.pending.extend(self.decoder.feed(&chunk[..count]));
        }
    }

    fn has_pending(&self) -> bool {
        !self.pending.is_empty()
    }
}

fn send_frame<S: Write>(stream: &mut S, value: &Value) -> Result<(), String> {
    let encoded = encode_frame(value)?;
    stream
        .write_all(&encoded)
        .map_err(|_| "frame send failed".to_owned())
}

type BackendConnector = Arc<dyn Fn() -> io::Result<UnixStream> + Send + Sync>;

struct GatewaySession {
    devices: DeviceStore,
    backend_connector: BackendConnector,
    auth_timeout: Duration,
}

impl GatewaySession {
    fn new(devices: DeviceStore, moosd_socket: impl Into<PathBuf>) -> Self {
        let socket = moosd_socket.into();
        Self::with_connector(devices, move || UnixStream::connect(&socket))
    }

    fn with_connector<F>(devices: DeviceStore, connector: F) -> Self
    where
        F: Fn() -> io::Result<UnixStream> + Send + Sync + 'static,
    {
        Self {
            devices,
            backend_connector: Arc::new(connector),
            auth_timeout: AUTH_TIMEOUT,
        }
    }

    #[cfg(test)]
    fn with_auth_timeout(mut self, timeout: Duration) -> Self {
        self.auth_timeout = timeout;
        self
    }

    fn handle(&self, mut remote: TcpStream) {
        let authenticated = self.authenticate(&mut remote);
        let Ok((device, server_nonce, client_nonce)) = authenticated else {
            let _ = send_frame(&mut remote, &make_authentication_error());
            let _ = remote.shutdown(Shutdown::Both);
            return;
        };
        let frame = match make_authenticated_frame(&device, &server_nonce, &client_nonce) {
            Ok(frame) => frame,
            Err(_) => return,
        };
        if send_frame(&mut remote, &frame).is_err() {
            return;
        }
        eprintln!("authenticated MOOS device {}", device.device_id);
        self.forward(remote, device);
    }

    fn authenticate(
        &self,
        remote: &mut TcpStream,
    ) -> Result<(AuthorizedDevice, [u8; 32], [u8; 32]), String> {
        let deadline = Instant::now() + self.auth_timeout;
        let (challenge, server_nonce) = make_challenge().map_err(|error| error.to_string())?;
        send_frame(remote, &challenge)?;
        let mut reader = FrameReader::new();
        let frame = reader.receive(remote, Some(deadline))?;
        if reader.has_pending() {
            return Err("authentication pipelining is not allowed".to_owned());
        }
        let devices = self.devices.load()?;
        let (device, client_nonce) = verify_authenticate_frame(&frame, &server_nonce, &devices)
            .map_err(|error| error.to_string())?;
        Ok((device, server_nonce, client_nonce))
    }

    fn forward(&self, mut remote: TcpStream, authenticated: AuthorizedDevice) {
        let mut remote_reader = FrameReader::new();
        let mut backend: Option<UnixStream> = None;
        let mut backend_reader = FrameReader::new();
        loop {
            let frame = match remote_reader
                .receive(&mut remote, Some(Instant::now() + IDLE_TIMEOUT))
            {
                Ok(frame) => frame,
                Err(_) => {
                    let _ =
                        send_control_error_to(&mut remote, "invalid_request", "Invalid request");
                    return;
                }
            };
            let request = match parse_control_request(&frame) {
                Ok(request) => request,
                Err(error) => {
                    if send_control_error_to(&mut remote, error.code, error.message).is_err() {
                        return;
                    }
                    continue;
                }
            };
            let devices = match self.devices.load() {
                Ok(devices) => devices,
                Err(_) => {
                    let _ =
                        send_control_error_to(&mut remote, "runtime_error", "MOOS is unavailable");
                    return;
                }
            };
            let current = devices.get(&authenticated.device_id);
            let current = match current {
                Some(device)
                    if !device.revoked && device.key.constant_time_eq(&authenticated.key) =>
                {
                    device
                }
                _ => {
                    eprintln!(
                        "revoked MOOS device {}; closing session",
                        authenticated.device_id
                    );
                    let _ =
                        send_control_error_to(&mut remote, "forbidden", "Device access revoked");
                    return;
                }
            };
            if !request.operation.is_remotely_authorizable()
                || !current.permissions.contains(request.operation.as_str())
            {
                if send_control_error_to(&mut remote, "forbidden", "Operation is not permitted")
                    .is_err()
                {
                    return;
                }
                continue;
            }
            if backend.is_none() {
                backend = match (self.backend_connector)() {
                    Ok(stream) => Some(stream),
                    Err(_) => {
                        let _ = send_control_error_to(
                            &mut remote,
                            "runtime_error",
                            "MOOS is unavailable",
                        );
                        return;
                    }
                };
            }
            let Some(backend_stream) = backend.as_mut() else {
                return;
            };
            if send_frame(backend_stream, &frame).is_err() {
                let _ = send_control_error_to(&mut remote, "runtime_error", "MOOS is unavailable");
                return;
            }
            let response = match backend_reader
                .receive(backend_stream, Some(Instant::now() + IDLE_TIMEOUT))
            {
                Ok(response) => response,
                Err(_) => {
                    let _ =
                        send_control_error_to(&mut remote, "runtime_error", "MOOS is unavailable");
                    return;
                }
            };
            if parse_control_response(&response, Some(request.operation)).is_err()
                || send_frame(&mut remote, &response).is_err()
            {
                let _ = send_control_error_to(&mut remote, "runtime_error", "MOOS is unavailable");
                return;
            }
        }
    }
}

fn send_control_error_to(remote: &mut TcpStream, code: &str, message: &str) -> Result<(), String> {
    let frame = make_control_error(code, message).map_err(|error| error.to_string())?;
    send_frame(remote, &frame)
}

#[derive(Default)]
struct AdmissionState {
    active_total: usize,
    active_by_source: BTreeMap<IpAddr, usize>,
    global_attempts: VecDeque<Instant>,
    attempts_by_source: BTreeMap<IpAddr, VecDeque<Instant>>,
}

#[derive(Default)]
struct AdmissionLimiter {
    state: Mutex<AdmissionState>,
}

impl AdmissionLimiter {
    fn admit(self: &Arc<Self>, source: IpAddr) -> Option<AdmissionPermit> {
        let now = Instant::now();
        let threshold = now.checked_sub(AUTH_RATE_WINDOW).unwrap_or(now);
        let mut state = self.state.lock().ok()?;
        while state
            .global_attempts
            .front()
            .is_some_and(|instant| *instant <= threshold)
        {
            state.global_attempts.pop_front();
        }
        state.attempts_by_source.retain(|_, attempts| {
            while attempts
                .front()
                .is_some_and(|instant| *instant <= threshold)
            {
                attempts.pop_front();
            }
            !attempts.is_empty()
        });
        let source_attempts = state
            .attempts_by_source
            .get(&source)
            .map_or(0, VecDeque::len);
        let source_active = state.active_by_source.get(&source).copied().unwrap_or(0);
        if state.active_total >= MAX_CLIENTS
            || source_active >= MAX_CLIENTS_PER_SOURCE
            || source_attempts >= MAX_AUTH_ATTEMPTS_PER_SOURCE
            || state.global_attempts.len() >= MAX_AUTH_ATTEMPTS_GLOBAL
        {
            return None;
        }
        state.global_attempts.push_back(now);
        state
            .attempts_by_source
            .entry(source)
            .or_default()
            .push_back(now);
        state.active_total += 1;
        *state.active_by_source.entry(source).or_default() += 1;
        Some(AdmissionPermit {
            limiter: Arc::clone(self),
            source,
        })
    }

    fn release(&self, source: IpAddr) {
        if let Ok(mut state) = self.state.lock() {
            state.active_total = state.active_total.saturating_sub(1);
            if let Some(active) = state.active_by_source.get_mut(&source) {
                *active = active.saturating_sub(1);
                if *active == 0 {
                    state.active_by_source.remove(&source);
                }
            }
        }
    }
}

struct AdmissionPermit {
    limiter: Arc<AdmissionLimiter>,
    source: IpAddr,
}

impl Drop for AdmissionPermit {
    fn drop(&mut self) {
        self.limiter.release(self.source);
    }
}

pub fn serve(
    listen_address: IpAddr,
    port: u16,
    devices: DeviceStore,
    moosd_socket: &Path,
) -> io::Result<()> {
    let listener = bind_tailscale_listener(listen_address, port).map_err(io_error)?;
    serve_listener(listener, devices, moosd_socket)
}

fn serve_listener(
    listener: TcpListener,
    devices: DeviceStore,
    moosd_socket: &Path,
) -> io::Result<()> {
    let session = Arc::new(GatewaySession::new(devices, moosd_socket));
    let admission = Arc::new(AdmissionLimiter::default());
    for accepted in listener.incoming() {
        let stream = match accepted {
            Ok(stream) => stream,
            Err(error) => {
                eprintln!("gateway accept failed: {error}");
                continue;
            }
        };
        let source = match stream.peer_addr() {
            Ok(peer) => peer.ip(),
            Err(_) => continue,
        };
        let Some(permit) = admission.admit(source) else {
            continue;
        };
        if stream.set_nodelay(true).is_err() {
            continue;
        }
        let session = Arc::clone(&session);
        let spawn = thread::Builder::new()
            .name("moos-gateway-client".to_owned())
            .stack_size(512 * 1024)
            .spawn(move || {
                let _permit = permit;
                session.handle(stream);
            });
        if let Err(error) = spawn {
            eprintln!("gateway worker creation failed: {error}");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::BufRead as _;
    use std::io::BufReader;
    use std::os::unix::fs::PermissionsExt as _;
    use std::sync::atomic::{AtomicUsize, Ordering};

    use crate::auth::{decode_base64url, make_authenticate_frame};
    use crate::store::DeviceAdminStore;

    fn read_json(reader: &mut BufReader<TcpStream>) -> Result<Value, String> {
        let mut line = String::new();
        reader
            .read_line(&mut line)
            .map_err(|error| error.to_string())?;
        serde_json::from_str(&line).map_err(|error| error.to_string())
    }

    fn authenticated_connection(
        session: GatewaySession,
        device: &AuthorizedDevice,
    ) -> Result<(TcpStream, BufReader<TcpStream>, thread::JoinHandle<()>), String> {
        let listener = TcpListener::bind("127.0.0.1:0").map_err(|error| error.to_string())?;
        let address = listener.local_addr().map_err(|error| error.to_string())?;
        let worker = thread::spawn(move || {
            if let Ok((stream, _)) = listener.accept() {
                session.handle(stream);
            }
        });
        let mut client = TcpStream::connect(address).map_err(|error| error.to_string())?;
        client
            .set_read_timeout(Some(Duration::from_secs(1)))
            .map_err(|error| error.to_string())?;
        let mut reader = BufReader::new(client.try_clone().map_err(|error| error.to_string())?);
        let challenge = read_json(&mut reader)?;
        let server_nonce = decode_base64url::<32>(
            challenge
                .get("nonce")
                .and_then(Value::as_str)
                .ok_or_else(|| "challenge nonce missing".to_owned())?,
        )
        .map_err(|error| error.to_string())?;
        let authentication = make_authenticate_frame(device, &server_nonce, &[4; 32])
            .map_err(|error| error.to_string())?;
        send_frame(&mut client, &authentication)?;
        let authenticated = read_json(&mut reader)?;
        if authenticated.get("type").and_then(Value::as_str) != Some("authenticated") {
            return Err("gateway did not authenticate fixture device".to_owned());
        }
        Ok((client, reader, worker))
    }

    fn temporary_store(label: &str) -> Result<(PathBuf, DeviceAdminStore), String> {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).map_err(|_| "random failure")?;
        let suffix = random
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let directory = std::env::temp_dir().join(format!("moos-session-{label}-{suffix}"));
        std::fs::create_dir(&directory).map_err(|error| error.to_string())?;
        std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o750))
            .map_err(|error| error.to_string())?;
        let admin = DeviceAdminStore::new(directory.join("devices.json"));
        Ok((directory, admin))
    }

    #[test]
    fn admission_is_bounded_per_source() {
        let limiter = Arc::new(AdmissionLimiter::default());
        let source: IpAddr = "100.64.0.1"
            .parse()
            .unwrap_or(IpAddr::V4(std::net::Ipv4Addr::UNSPECIFIED));
        let permits: Vec<_> = (0..MAX_CLIENTS_PER_SOURCE)
            .filter_map(|_| limiter.admit(source))
            .collect();
        assert_eq!(permits.len(), MAX_CLIENTS_PER_SOURCE);
        assert!(limiter.admit(source).is_none());
        drop(permits);
        assert!(limiter.admit(source).is_some());
    }

    #[test]
    fn incomplete_frames_expire_despite_continuous_bytes() -> Result<(), String> {
        let (mut receiver, mut sender) = UnixStream::pair().map_err(|e| e.to_string())?;
        let writer = thread::spawn(move || {
            for _ in 0..40 {
                if sender.write_all(b" ").is_err() {
                    break;
                }
                thread::sleep(Duration::from_millis(5));
            }
        });
        let started = Instant::now();
        let result =
            FrameReader::new().receive(&mut receiver, Some(started + Duration::from_millis(40)));
        assert!(result.is_err());
        assert!(started.elapsed() < Duration::from_millis(180));
        drop(receiver);
        writer.join().map_err(|_| "writer panicked")?;
        Ok(())
    }

    #[test]
    fn authentication_uses_one_absolute_deadline() -> Result<(), String> {
        let listener = TcpListener::bind("127.0.0.1:0").map_err(|error| error.to_string())?;
        let address = listener.local_addr().map_err(|error| error.to_string())?;
        let session = GatewaySession::with_connector(
            DeviceStore::new(std::env::temp_dir().join("moos-missing-device-store.json")),
            || Err(io::Error::other("backend must not be reached")),
        )
        .with_auth_timeout(Duration::from_millis(40));
        let worker = thread::spawn(move || {
            if let Ok((stream, _)) = listener.accept() {
                session.handle(stream);
            }
        });
        let client = TcpStream::connect(address).map_err(|error| error.to_string())?;
        client
            .set_read_timeout(Some(Duration::from_secs(1)))
            .map_err(|error| error.to_string())?;
        let mut reader = BufReader::new(client.try_clone().map_err(|error| error.to_string())?);
        let mut challenge = String::new();
        reader
            .read_line(&mut challenge)
            .map_err(|error| error.to_string())?;
        assert!(challenge.contains("\"type\":\"challenge\""));
        thread::sleep(Duration::from_millis(70));
        let mut failure = String::new();
        reader
            .read_line(&mut failure)
            .map_err(|error| error.to_string())?;
        assert!(failure.contains("\"code\":\"authentication_failed\""));
        let _ = client.shutdown(Shutdown::Both);
        worker
            .join()
            .map_err(|_| "gateway test worker panicked".to_owned())?;
        Ok(())
    }

    #[test]
    fn forbidden_is_denied_before_backend_and_status_is_validated() -> Result<(), String> {
        let (directory, admin) = temporary_store("forward")?;
        let device = admin.add("Test iPhone", ["status"])?;
        let backend_uses = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&backend_uses);
        let session = GatewaySession::with_connector(
            DeviceStore::new(directory.join("devices.json")),
            move || {
                counter.fetch_add(1, Ordering::SeqCst);
                let (gateway, mut daemon) = UnixStream::pair()?;
                thread::spawn(move || {
                    let clone = match daemon.try_clone() {
                        Ok(clone) => clone,
                        Err(_) => return,
                    };
                    let mut reader = BufReader::new(clone);
                    let mut request = String::new();
                    if reader.read_line(&mut request).is_err() {
                        return;
                    }
                    let response = serde_json::json!({
                        "protocolVersion": 1,
                        "ok": true,
                        "operation": "status",
                        "data": {"personal": {"identity": "personal", "state": "running", "result": "success"}},
                        "events": [],
                    });
                    if let Ok(encoded) = encode_frame(&response) {
                        let _ = daemon.write_all(&encoded);
                    }
                });
                Ok(gateway)
            },
        );
        let (mut client, mut reader, worker) = authenticated_connection(session, &device)?;
        send_frame(
            &mut client,
            &serde_json::json!({"protocolVersion": 1, "operation": "personal.stop"}),
        )?;
        let denied = read_json(&mut reader)?;
        assert_eq!(denied["error"]["code"], "forbidden");
        assert_eq!(backend_uses.load(Ordering::SeqCst), 0);

        send_frame(
            &mut client,
            &serde_json::json!({"protocolVersion": 1, "operation": "status"}),
        )?;
        let response = read_json(&mut reader)?;
        assert_eq!(response["ok"], true);
        assert_eq!(backend_uses.load(Ordering::SeqCst), 1);
        let _ = client.shutdown(Shutdown::Both);
        worker
            .join()
            .map_err(|_| "gateway test worker panicked".to_owned())?;
        std::fs::remove_dir_all(directory).map_err(|error| error.to_string())?;
        Ok(())
    }

    #[test]
    fn revocation_is_checked_before_every_operation() -> Result<(), String> {
        let (directory, admin) = temporary_store("revoke")?;
        let device = admin.add("Test iPhone", ["status"])?;
        let backend_uses = Arc::new(AtomicUsize::new(0));
        let counter = Arc::clone(&backend_uses);
        let session = GatewaySession::with_connector(
            DeviceStore::new(directory.join("devices.json")),
            move || {
                counter.fetch_add(1, Ordering::SeqCst);
                Err(io::Error::other("backend must not be reached"))
            },
        );
        let (mut client, mut reader, worker) = authenticated_connection(session, &device)?;
        admin.revoke(&device.device_id)?;
        send_frame(
            &mut client,
            &serde_json::json!({"protocolVersion": 1, "operation": "status"}),
        )?;
        let denied = read_json(&mut reader)?;
        assert_eq!(denied["error"]["code"], "forbidden");
        assert_eq!(backend_uses.load(Ordering::SeqCst), 0);
        let _ = client.shutdown(Shutdown::Both);
        worker
            .join()
            .map_err(|_| "gateway test worker panicked".to_owned())?;
        std::fs::remove_dir_all(directory).map_err(|error| error.to_string())?;
        Ok(())
    }
}
