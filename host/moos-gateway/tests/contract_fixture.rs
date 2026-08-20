use std::fs;
use std::path::PathBuf;

use moos_gateway::auth::{
    AuthorizedDevice, DeviceKey, decode_base64url, make_authenticate_frame,
    make_authenticated_frame, parse_pairing_code, validate_permissions,
};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Fixture {
    authentication_proof: String,
    client_nonce: String,
    device_id: String,
    device_key: String,
    gateway_version: u64,
    pairing_code: String,
    server_nonce: String,
    server_proof: String,
}

#[test]
fn shared_swift_rust_contract_fixture_matches() -> Result<(), String> {
    let fixture_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/gateway-v1-contract.json");
    let fixture: Fixture =
        serde_json::from_slice(&fs::read(fixture_path).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
    assert_eq!(fixture.gateway_version, 1);
    let device = AuthorizedDevice {
        device_id: fixture.device_id.clone(),
        name: "Fixture iPhone".to_owned(),
        key: DeviceKey::from_bytes(
            decode_base64url::<32>(&fixture.device_key).map_err(|error| error.to_string())?,
        ),
        permissions: validate_permissions(["status"])?,
        revoked: false,
        created_at: "2026-08-20T12:00:00+00:00".to_owned(),
        updated_at: "2026-08-20T12:00:00+00:00".to_owned(),
    };
    let server_nonce =
        decode_base64url::<32>(&fixture.server_nonce).map_err(|error| error.to_string())?;
    let client_nonce =
        decode_base64url::<32>(&fixture.client_nonce).map_err(|error| error.to_string())?;
    let authenticate = make_authenticate_frame(&device, &server_nonce, &client_nonce)
        .map_err(|error| error.to_string())?;
    assert_eq!(authenticate["proof"], fixture.authentication_proof);
    let authenticated = make_authenticated_frame(&device, &server_nonce, &client_nonce)
        .map_err(|error| error.to_string())?;
    assert_eq!(authenticated["serverProof"], fixture.server_proof);
    let (device_id, key) = parse_pairing_code(&fixture.pairing_code)?;
    assert_eq!(device_id, fixture.device_id);
    assert!(key.constant_time_eq(&device.key));
    Ok(())
}
