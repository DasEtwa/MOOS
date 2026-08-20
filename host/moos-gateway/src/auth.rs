use std::collections::BTreeSet;
use std::error::Error;
use std::fmt::{self, Display, Formatter};

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use hmac::{Hmac, KeyInit as _, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::Sha256;
use subtle::ConstantTimeEq;
use uuid::Uuid;
use zeroize::{Zeroize, ZeroizeOnDrop};

pub const GATEWAY_VERSION: u64 = 1;
pub const NONCE_BYTES: usize = 32;
pub const DEVICE_KEY_BYTES: usize = 32;
const AUTH_CONTEXT: &[u8] = b"MOOS-GATEWAY-AUTH-V1\n";
const SERVER_AUTH_CONTEXT: &[u8] = b"MOOS-GATEWAY-SERVER-V1\n";
const PAIRING_PREFIX: &str = "moos-pair-v1";

#[derive(Clone, Zeroize, ZeroizeOnDrop)]
pub struct DeviceKey([u8; DEVICE_KEY_BYTES]);

impl DeviceKey {
    pub fn generate() -> Result<Self, GatewayAuthError> {
        let mut bytes = [0_u8; DEVICE_KEY_BYTES];
        getrandom::fill(&mut bytes).map_err(|_| GatewayAuthError)?;
        Ok(Self(bytes))
    }

    pub fn from_bytes(bytes: [u8; DEVICE_KEY_BYTES]) -> Self {
        Self(bytes)
    }

    pub fn as_bytes(&self) -> &[u8; DEVICE_KEY_BYTES] {
        &self.0
    }

    pub fn constant_time_eq(&self, other: &Self) -> bool {
        bool::from(self.0.ct_eq(&other.0))
    }
}

impl fmt::Debug for DeviceKey {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("DeviceKey([REDACTED])")
    }
}

#[derive(Clone, Debug)]
pub struct AuthorizedDevice {
    pub device_id: String,
    pub name: String,
    pub key: DeviceKey,
    pub permissions: BTreeSet<String>,
    pub revoked: bool,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct GatewayAuthError;

impl Display for GatewayAuthError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        formatter.write_str("authentication failed")
    }
}

impl Error for GatewayAuthError {}

#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct AuthenticateFrame {
    gateway_version: u64,
    #[serde(rename = "type")]
    frame_type: String,
    device_id: String,
    client_nonce: String,
    proof: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ChallengeFrame<'a> {
    gateway_version: u64,
    #[serde(rename = "type")]
    frame_type: &'a str,
    nonce: String,
}

pub fn validate_device_id(value: &str) -> Result<String, GatewayAuthError> {
    let parsed = Uuid::parse_str(value).map_err(|_| GatewayAuthError)?;
    let canonical = parsed.hyphenated().to_string();
    if canonical != value {
        return Err(GatewayAuthError);
    }
    Ok(canonical)
}

pub fn validate_device_name(value: &str) -> Result<String, String> {
    let name = value.trim();
    let characters = name.chars().count();
    if characters == 0 || characters > 80 || name.chars().any(char::is_control) {
        return Err("device name must contain 1 to 80 printable characters".to_owned());
    }
    Ok(name.to_owned())
}

pub fn validate_permissions<I, S>(permissions: I) -> Result<BTreeSet<String>, String>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    let result: BTreeSet<String> = permissions
        .into_iter()
        .map(|item| item.as_ref().to_owned())
        .collect();
    if result.is_empty() {
        return Err("at least one text permission is required".to_owned());
    }
    if result.iter().any(|permission| permission != "status") {
        return Err("unsupported remote permission".to_owned());
    }
    Ok(result)
}

pub fn encode_base64url(value: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(value)
}

pub fn decode_base64url<const N: usize>(value: &str) -> Result<[u8; N], GatewayAuthError> {
    if value.is_empty() || value.contains('=') {
        return Err(GatewayAuthError);
    }
    let mut decoded = URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| GatewayAuthError)?;
    if decoded.len() != N || encode_base64url(&decoded) != value {
        decoded.zeroize();
        return Err(GatewayAuthError);
    }
    let mut output = [0_u8; N];
    output.copy_from_slice(&decoded);
    decoded.zeroize();
    Ok(output)
}

fn transcript(
    context: &[u8],
    server_nonce: &[u8; NONCE_BYTES],
    client_nonce: &[u8; NONCE_BYTES],
    device_id: &str,
) -> Result<Vec<u8>, GatewayAuthError> {
    validate_device_id(device_id)?;
    let mut output = Vec::with_capacity(180);
    output.extend_from_slice(context);
    output.extend_from_slice(encode_base64url(server_nonce).as_bytes());
    output.push(b'\n');
    output.extend_from_slice(encode_base64url(client_nonce).as_bytes());
    output.push(b'\n');
    output.extend_from_slice(device_id.as_bytes());
    output.push(b'\n');
    Ok(output)
}

fn proof(
    context: &[u8],
    key: &DeviceKey,
    server_nonce: &[u8; NONCE_BYTES],
    client_nonce: &[u8; NONCE_BYTES],
    device_id: &str,
) -> Result<[u8; 32], GatewayAuthError> {
    let payload = transcript(context, server_nonce, client_nonce, device_id)?;
    let mut mac = Hmac::<Sha256>::new_from_slice(key.as_bytes()).map_err(|_| GatewayAuthError)?;
    mac.update(&payload);
    Ok(mac.finalize().into_bytes().into())
}

pub fn make_authenticate_frame(
    device: &AuthorizedDevice,
    server_nonce: &[u8; NONCE_BYTES],
    client_nonce: &[u8; NONCE_BYTES],
) -> Result<Value, GatewayAuthError> {
    let client_proof = proof(
        AUTH_CONTEXT,
        &device.key,
        server_nonce,
        client_nonce,
        &device.device_id,
    )?;
    Ok(json!({
        "gatewayVersion": GATEWAY_VERSION,
        "type": "authenticate",
        "deviceId": device.device_id,
        "clientNonce": encode_base64url(client_nonce),
        "proof": encode_base64url(&client_proof),
    }))
}

pub fn make_challenge() -> Result<(Value, [u8; NONCE_BYTES]), GatewayAuthError> {
    let mut nonce = [0_u8; NONCE_BYTES];
    getrandom::fill(&mut nonce).map_err(|_| GatewayAuthError)?;
    let frame = serde_json::to_value(ChallengeFrame {
        gateway_version: GATEWAY_VERSION,
        frame_type: "challenge",
        nonce: encode_base64url(&nonce),
    })
    .map_err(|_| GatewayAuthError)?;
    Ok((frame, nonce))
}

pub fn verify_authenticate_frame(
    frame: &Value,
    server_nonce: &[u8; NONCE_BYTES],
    devices: &std::collections::BTreeMap<String, AuthorizedDevice>,
) -> Result<(AuthorizedDevice, [u8; NONCE_BYTES]), GatewayAuthError> {
    let authentication: AuthenticateFrame =
        serde_json::from_value(frame.clone()).map_err(|_| GatewayAuthError)?;
    if authentication.gateway_version != GATEWAY_VERSION
        || authentication.frame_type != "authenticate"
    {
        return Err(GatewayAuthError);
    }
    let device_id = validate_device_id(&authentication.device_id)?;
    let client_nonce = decode_base64url::<NONCE_BYTES>(&authentication.client_nonce)?;
    let supplied_proof = decode_base64url::<32>(&authentication.proof)?;

    let zero_key = DeviceKey::from_bytes([0_u8; DEVICE_KEY_BYTES]);
    let verification_key = devices
        .get(&device_id)
        .map_or(&zero_key, |device| &device.key);
    let expected = proof(
        AUTH_CONTEXT,
        verification_key,
        server_nonce,
        &client_nonce,
        &device_id,
    )?;
    let matches = bool::from(expected.ct_eq(&supplied_proof));
    let device = devices.get(&device_id).ok_or(GatewayAuthError)?;
    if device.revoked || !matches {
        return Err(GatewayAuthError);
    }
    Ok((device.clone(), client_nonce))
}

pub fn make_authenticated_frame(
    device: &AuthorizedDevice,
    server_nonce: &[u8; NONCE_BYTES],
    client_nonce: &[u8; NONCE_BYTES],
) -> Result<Value, GatewayAuthError> {
    let server_proof = proof(
        SERVER_AUTH_CONTEXT,
        &device.key,
        server_nonce,
        client_nonce,
        &device.device_id,
    )?;
    Ok(json!({
        "gatewayVersion": GATEWAY_VERSION,
        "type": "authenticated",
        "deviceId": device.device_id,
        "permissions": device.permissions,
        "serverProof": encode_base64url(&server_proof),
    }))
}

pub fn make_authentication_error() -> Value {
    json!({
        "gatewayVersion": GATEWAY_VERSION,
        "type": "error",
        "code": "authentication_failed",
        "message": "Device authentication failed",
    })
}

pub fn pairing_code(device: &AuthorizedDevice) -> String {
    format!(
        "{PAIRING_PREFIX}:{}:{}",
        device.device_id,
        encode_base64url(device.key.as_bytes())
    )
}

pub fn parse_pairing_code(value: &str) -> Result<(String, DeviceKey), String> {
    let parts: Vec<&str> = value.trim().split(':').collect();
    if parts.len() != 3 || parts[0] != PAIRING_PREFIX {
        return Err("invalid pairing code".to_owned());
    }
    let device_id = validate_device_id(parts[1]).map_err(|_| "invalid pairing code")?;
    let key = decode_base64url::<DEVICE_KEY_BYTES>(parts[2])
        .map(DeviceKey::from_bytes)
        .map_err(|_| "invalid pairing code")?;
    Ok((device_id, key))
}

#[cfg(test)]
mod tests {
    use super::*;

    const DEVICE_ID: &str = "00000000-0000-4000-8000-000000000001";

    #[test]
    fn shared_hmac_vectors_are_stable() -> Result<(), String> {
        let key = DeviceKey::from_bytes([7; 32]);
        let server = [3; 32];
        let client = [4; 32];
        let client_proof = proof(AUTH_CONTEXT, &key, &server, &client, DEVICE_ID)
            .map_err(|error| error.to_string())?;
        let server_proof = proof(SERVER_AUTH_CONTEXT, &key, &server, &client, DEVICE_ID)
            .map_err(|error| error.to_string())?;
        let hex = |bytes: &[u8]| {
            bytes
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>()
        };
        assert_eq!(
            hex(&client_proof),
            "fadb524047e066a7546f364eab2e10cbd5203e945c9ae44aaf1346c0ee72689b"
        );
        assert_eq!(
            hex(&server_proof),
            "62674501e0aece3d3a6b34217c1eec59313614c6a68142d37c589459929f118f"
        );
        Ok(())
    }

    #[test]
    fn base64url_is_canonical_and_unpadded() {
        let encoded = encode_base64url(&[0_u8; 32]);
        assert!(!encoded.contains('='));
        assert!(decode_base64url::<32>(&encoded).is_ok());
        assert!(decode_base64url::<32>(&format!("{encoded}=")).is_err());
    }

    #[test]
    fn pairing_round_trip() -> Result<(), String> {
        let device = AuthorizedDevice {
            device_id: DEVICE_ID.to_owned(),
            name: "iPhone".to_owned(),
            key: DeviceKey::from_bytes([7; 32]),
            permissions: validate_permissions(["status"])?,
            revoked: false,
            created_at: "2026-08-20T12:00:00+00:00".to_owned(),
            updated_at: "2026-08-20T12:00:00+00:00".to_owned(),
        };
        let (id, key) = parse_pairing_code(&pairing_code(&device))?;
        assert_eq!(id, DEVICE_ID);
        assert!(key.constant_time_eq(&device.key));
        Ok(())
    }

    #[test]
    fn wrong_unknown_revoked_and_malformed_auth_are_generic() -> Result<(), String> {
        let device = AuthorizedDevice {
            device_id: DEVICE_ID.to_owned(),
            name: "iPhone".to_owned(),
            key: DeviceKey::from_bytes([7; 32]),
            permissions: validate_permissions(["status"])?,
            revoked: false,
            created_at: "2026-08-20T12:00:00+00:00".to_owned(),
            updated_at: "2026-08-20T12:00:00+00:00".to_owned(),
        };
        let server = [3; 32];
        let client = [4; 32];
        let mut devices =
            std::collections::BTreeMap::from([(device.device_id.clone(), device.clone())]);
        let wrong_device = AuthorizedDevice {
            key: DeviceKey::from_bytes([9; 32]),
            ..device.clone()
        };
        let wrong = make_authenticate_frame(&wrong_device, &server, &client)
            .map_err(|error| error.to_string())?;
        let wrong_error = verify_authenticate_frame(&wrong, &server, &devices)
            .err()
            .ok_or_else(|| "wrong key was accepted".to_owned())?;

        let unknown_device = AuthorizedDevice {
            device_id: "00000000-0000-4000-8000-000000000002".to_owned(),
            ..device.clone()
        };
        let unknown = make_authenticate_frame(&unknown_device, &server, &client)
            .map_err(|error| error.to_string())?;
        let unknown_error = verify_authenticate_frame(&unknown, &server, &devices)
            .err()
            .ok_or_else(|| "unknown device was accepted".to_owned())?;

        devices
            .get_mut(DEVICE_ID)
            .ok_or_else(|| "fixture device missing".to_owned())?
            .revoked = true;
        let valid = make_authenticate_frame(&device, &server, &client)
            .map_err(|error| error.to_string())?;
        let revoked_error = verify_authenticate_frame(&valid, &server, &devices)
            .err()
            .ok_or_else(|| "revoked device was accepted".to_owned())?;
        let mut malformed = valid;
        malformed["unexpected"] = Value::Bool(true);
        let malformed_error = verify_authenticate_frame(&malformed, &server, &devices)
            .err()
            .ok_or_else(|| "unknown authentication field was accepted".to_owned())?;

        for error in [wrong_error, unknown_error, revoked_error, malformed_error] {
            assert_eq!(error.to_string(), "authentication failed");
        }
        Ok(())
    }
}
