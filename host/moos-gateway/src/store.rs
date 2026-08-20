use std::collections::BTreeMap;
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{
    DirBuilderExt as _, MetadataExt as _, OpenOptionsExt as _, PermissionsExt as _,
};
use std::path::{Path, PathBuf};

use nix::unistd::{Gid, Uid, fchown, geteuid};
use serde::{Deserialize, Serialize};
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;
use uuid::Uuid;
use zeroize::Zeroize as _;

use crate::auth::{
    AuthorizedDevice, DEVICE_KEY_BYTES, DeviceKey, decode_base64url, encode_base64url,
    validate_device_id, validate_device_name, validate_permissions,
};

const SCHEMA_VERSION: u64 = 1;
const MAX_DEVICE_STORE_BYTES: u64 = 1024 * 1024;

pub type AuthorizedDevices = BTreeMap<String, AuthorizedDevice>;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct StoreDocument {
    schema_version: u64,
    devices: Vec<DeviceDocument>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct DeviceDocument {
    id: String,
    name: String,
    key: String,
    permissions: Vec<String>,
    revoked: bool,
    created_at: String,
    updated_at: String,
}

#[derive(Clone, Debug)]
pub struct DeviceStore {
    path: PathBuf,
    expected_owner: Option<(u32, u32)>,
}

impl DeviceStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            expected_owner: None,
        }
    }

    pub fn with_expected_owner(path: impl Into<PathBuf>, uid: u32, gid: u32) -> Self {
        Self {
            path: path.into(),
            expected_owner: Some((uid, gid)),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self) -> Result<AuthorizedDevices, String> {
        self.validate_parent()?;
        let file = match OpenOptions::new()
            .read(true)
            .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_CLOEXEC)
            .open(&self.path)
        {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Ok(BTreeMap::new());
            }
            Err(_) => return Err("device store cannot be opened safely".to_owned()),
        };
        let metadata = file
            .metadata()
            .map_err(|_| "device store metadata is unavailable".to_owned())?;
        validate_regular_file(&metadata, 0o640, "device store")?;
        self.validate_owner(&metadata, "device store")?;
        if metadata.len() > MAX_DEVICE_STORE_BYTES {
            return Err("device store is too large".to_owned());
        }
        let mut encoded = Vec::new();
        file.take(MAX_DEVICE_STORE_BYTES + 1)
            .read_to_end(&mut encoded)
            .map_err(|_| "device store cannot be read".to_owned())?;
        if encoded.len() as u64 > MAX_DEVICE_STORE_BYTES {
            encoded.zeroize();
            return Err("device store is too large".to_owned());
        }
        let parsed = serde_json::from_slice(&encoded);
        encoded.zeroize();
        let document: StoreDocument = parsed.map_err(|_| "invalid device store".to_owned())?;
        if document.schema_version != SCHEMA_VERSION {
            return Err("unsupported device store schema".to_owned());
        }
        let mut devices = BTreeMap::new();
        for mut item in document.devices {
            let device_id =
                validate_device_id(&item.id).map_err(|_| "invalid device ID".to_owned())?;
            let key = decode_base64url::<DEVICE_KEY_BYTES>(&item.key)
                .map(DeviceKey::from_bytes)
                .map_err(|_| "invalid device key".to_owned())?;
            item.key.zeroize();
            let name = validate_device_name(&item.name)?;
            let permissions = validate_permissions(&item.permissions)?;
            if item.created_at.is_empty() || item.updated_at.is_empty() {
                return Err("invalid device timestamp".to_owned());
            }
            let device = AuthorizedDevice {
                device_id: device_id.clone(),
                name,
                key,
                permissions,
                revoked: item.revoked,
                created_at: item.created_at,
                updated_at: item.updated_at,
            };
            if devices.insert(device_id, device).is_some() {
                return Err("duplicate device ID".to_owned());
            }
        }
        Ok(devices)
    }

    fn validate_parent(&self) -> Result<(), String> {
        let Some((uid, gid)) = self.expected_owner else {
            return Ok(());
        };
        let metadata = fs::symlink_metadata(
            self.path
                .parent()
                .ok_or_else(|| "device store directory is unavailable".to_owned())?,
        )
        .map_err(|_| "device store directory is unavailable".to_owned())?;
        if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
            return Err("device store directory must be a regular directory".to_owned());
        }
        if metadata.mode() & 0o7777 != 0o750 {
            return Err("device store directory must have mode 0750".to_owned());
        }
        if metadata.uid() != uid || metadata.gid() != gid {
            return Err("device store directory has an unexpected owner".to_owned());
        }
        Ok(())
    }

    fn validate_owner(&self, metadata: &fs::Metadata, label: &str) -> Result<(), String> {
        if let Some((uid, gid)) = self.expected_owner
            && (metadata.uid() != uid || metadata.gid() != gid)
        {
            return Err(format!("{label} has an unexpected owner"));
        }
        Ok(())
    }
}

#[derive(Clone, Debug)]
pub struct DeviceAdminStore {
    store: DeviceStore,
}

impl DeviceAdminStore {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            store: DeviceStore::new(path),
        }
    }

    pub fn load(&self) -> Result<AuthorizedDevices, String> {
        self.store.load()
    }

    pub fn add<I, S>(&self, name: &str, permissions: I) -> Result<AuthorizedDevice, String>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let name = validate_device_name(name)?;
        let permissions = validate_permissions(permissions)?;
        self.mutate(move |devices| {
            let now = utc_now()?;
            let mut uuid_bytes = [0_u8; 16];
            getrandom::fill(&mut uuid_bytes).map_err(|_| "device ID generation failed")?;
            uuid_bytes[6] = (uuid_bytes[6] & 0x0f) | 0x40;
            uuid_bytes[8] = (uuid_bytes[8] & 0x3f) | 0x80;
            let device = AuthorizedDevice {
                device_id: Uuid::from_bytes(uuid_bytes).hyphenated().to_string(),
                name,
                key: DeviceKey::generate().map_err(|error| error.to_string())?,
                permissions,
                revoked: false,
                created_at: now.clone(),
                updated_at: now,
            };
            devices.insert(device.device_id.clone(), device.clone());
            Ok(device)
        })
    }

    pub fn revoke(&self, device_id: &str) -> Result<AuthorizedDevice, String> {
        self.replace(device_id, |device| {
            device.revoked = true;
            Ok(())
        })
    }

    pub fn rotate(&self, device_id: &str) -> Result<AuthorizedDevice, String> {
        self.replace(device_id, |device| {
            device.key = DeviceKey::generate()?;
            device.revoked = false;
            Ok(())
        })
    }

    pub fn set_permissions<I, S>(
        &self,
        device_id: &str,
        permissions: I,
    ) -> Result<AuthorizedDevice, String>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let permissions = validate_permissions(permissions)?;
        self.replace(device_id, move |device| {
            device.permissions = permissions;
            Ok(())
        })
    }

    fn replace<F>(&self, device_id: &str, update: F) -> Result<AuthorizedDevice, String>
    where
        F: FnOnce(&mut AuthorizedDevice) -> Result<(), crate::auth::GatewayAuthError>,
    {
        let device_id = validate_device_id(device_id).map_err(|_| "device not found")?;
        self.mutate(move |devices| {
            let device = devices
                .get_mut(&device_id)
                .ok_or_else(|| "device not found".to_owned())?;
            update(device).map_err(|error| error.to_string())?;
            device.updated_at = utc_now()?;
            Ok(device.clone())
        })
    }

    fn mutate<T, F>(&self, update: F) -> Result<T, String>
    where
        F: FnOnce(&mut AuthorizedDevices) -> Result<T, String>,
    {
        ensure_parent(self.store.path())?;
        let lock_path = self
            .store
            .path()
            .parent()
            .ok_or_else(|| "device store directory is unavailable".to_owned())?
            .join(".devices.lock");
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .mode(0o600)
            .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_CLOEXEC)
            .open(&lock_path)
            .map_err(|_| "device store lock cannot be opened safely".to_owned())?;
        let lock_metadata = lock
            .metadata()
            .map_err(|_| "device store lock metadata is unavailable".to_owned())?;
        validate_regular_file(&lock_metadata, 0o600, "device store lock")?;
        if lock_metadata.uid() != geteuid().as_raw() {
            return Err("device store lock has an unexpected owner".to_owned());
        }
        lock.lock()
            .map_err(|_| "device store lock failed".to_owned())?;
        let result = (|| {
            let mut devices = self.store.load()?;
            let value = update(&mut devices)?;
            save_unlocked(self.store.path(), &devices)?;
            Ok(value)
        })();
        let unlock_result = lock.unlock();
        if result.is_ok() && unlock_result.is_err() {
            return Err("device store unlock failed".to_owned());
        }
        result
    }
}

fn ensure_parent(path: &Path) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "device store directory is unavailable".to_owned())?;
    if !parent.exists() {
        let mut builder = DirBuilder::new();
        builder.recursive(true).mode(0o750);
        builder
            .create(parent)
            .map_err(|_| "device store directory cannot be created".to_owned())?;
    }
    let metadata = fs::symlink_metadata(parent)
        .map_err(|_| "device store directory is unavailable".to_owned())?;
    if !metadata.file_type().is_dir() || metadata.file_type().is_symlink() {
        return Err("device store directory must be a regular directory".to_owned());
    }
    Ok(())
}

fn validate_regular_file(metadata: &fs::Metadata, mode: u32, label: &str) -> Result<(), String> {
    if !metadata.file_type().is_file() {
        return Err(format!("{label} must be a regular file"));
    }
    if metadata.nlink() != 1 {
        return Err(format!("{label} must not have hard links"));
    }
    if metadata.mode() & 0o7777 != mode {
        return Err(format!("{label} must have mode {mode:04o}"));
    }
    Ok(())
}

fn utc_now() -> Result<String, String> {
    let mut timestamp = OffsetDateTime::now_utc()
        .replace_nanosecond(0)
        .map_err(|_| "timestamp generation failed".to_owned())?
        .format(&Rfc3339)
        .map_err(|_| "timestamp generation failed".to_owned())?;
    if timestamp.ends_with('Z') {
        timestamp.pop();
        timestamp.push_str("+00:00");
    }
    Ok(timestamp)
}

fn save_unlocked(path: &Path, devices: &AuthorizedDevices) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "device store directory is unavailable".to_owned())?;
    let parent_metadata = fs::metadata(parent)
        .map_err(|_| "device store directory metadata is unavailable".to_owned())?;
    let mut documents = Vec::with_capacity(devices.len());
    for device in devices.values() {
        documents.push(DeviceDocument {
            id: device.device_id.clone(),
            name: device.name.clone(),
            key: encode_base64url(device.key.as_bytes()),
            permissions: device.permissions.iter().cloned().collect(),
            revoked: device.revoked,
            created_at: device.created_at.clone(),
            updated_at: device.updated_at.clone(),
        });
    }
    let mut encoded = serde_json::to_vec_pretty(&StoreDocument {
        schema_version: SCHEMA_VERSION,
        devices: documents,
    })
    .map_err(|_| "device store serialization failed".to_owned())?;
    encoded.push(b'\n');
    if encoded.len() as u64 > MAX_DEVICE_STORE_BYTES {
        encoded.zeroize();
        return Err("device store is too large".to_owned());
    }

    let mut random = [0_u8; 8];
    getrandom::fill(&mut random).map_err(|_| "temporary file generation failed".to_owned())?;
    let suffix = random
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let temporary = parent.join(format!(".devices-{suffix}.json"));
    let write_result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(nix::libc::O_NOFOLLOW | nix::libc::O_CLOEXEC)
            .open(&temporary)
            .map_err(|_| "temporary device store cannot be created safely".to_owned())?;
        fchown(
            &file,
            Some(Uid::from_raw(parent_metadata.uid())),
            Some(Gid::from_raw(parent_metadata.gid())),
        )
        .map_err(|_| "temporary device store ownership failed".to_owned())?;
        file.set_permissions(fs::Permissions::from_mode(0o640))
            .map_err(|_| "temporary device store mode failed".to_owned())?;
        file.write_all(&encoded)
            .map_err(|_| "device store write failed".to_owned())?;
        file.sync_all()
            .map_err(|_| "device store sync failed".to_owned())?;
        fs::rename(&temporary, path)
            .map_err(|_| "device store atomic replacement failed".to_owned())?;
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(|_| "device store directory sync failed".to_owned())?;
        Ok(())
    })();
    encoded.zeroize();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    write_result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    fn temporary_store(label: &str) -> Result<(PathBuf, DeviceAdminStore), String> {
        let mut random = [0_u8; 8];
        getrandom::fill(&mut random).map_err(|_| "random failure")?;
        let suffix = random
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let directory = std::env::temp_dir().join(format!("moos-{label}-{suffix}"));
        fs::create_dir(&directory).map_err(|error| error.to_string())?;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o750))
            .map_err(|error| error.to_string())?;
        let path = directory.join("devices.json");
        Ok((directory, DeviceAdminStore::new(path)))
    }

    #[test]
    fn legacy_schema_round_trips_and_mutates() -> Result<(), String> {
        let (directory, admin) = temporary_store("store")?;
        let device = admin.add("Test iPhone", ["status"])?;
        let loaded = admin.load()?;
        assert!(loaded.contains_key(&device.device_id));
        assert_eq!(
            fs::metadata(admin.store.path())
                .map_err(|error| error.to_string())?
                .mode()
                & 0o7777,
            0o640
        );
        let revoked = admin.revoke(&device.device_id)?;
        assert!(revoked.revoked);
        let rotated = admin.rotate(&device.device_id)?;
        assert!(!rotated.revoked);
        assert!(!rotated.key.constant_time_eq(&device.key));
        fs::remove_dir_all(directory).map_err(|error| error.to_string())?;
        Ok(())
    }

    #[test]
    fn existing_python_store_load_is_read_only_and_compatible() -> Result<(), String> {
        let (directory, admin) = temporary_store("legacy")?;
        let legacy = concat!(
            "{\n",
            "  \"devices\": [\n",
            "    {\n",
            "      \"createdAt\": \"2026-08-18T12:00:00+00:00\",\n",
            "      \"id\": \"00000000-0000-4000-8000-000000000001\",\n",
            "      \"key\": \"BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc\",\n",
            "      \"name\": \"My iPhone\",\n",
            "      \"permissions\": [\"status\"],\n",
            "      \"revoked\": false,\n",
            "      \"updatedAt\": \"2026-08-18T12:00:00+00:00\"\n",
            "    }\n",
            "  ],\n",
            "  \"schemaVersion\": 1\n",
            "}\n"
        );
        fs::write(admin.store.path(), legacy).map_err(|error| error.to_string())?;
        fs::set_permissions(admin.store.path(), fs::Permissions::from_mode(0o640))
            .map_err(|error| error.to_string())?;
        let before = fs::read(admin.store.path()).map_err(|error| error.to_string())?;
        let devices = admin.load()?;
        let after = fs::read(admin.store.path()).map_err(|error| error.to_string())?;
        assert_eq!(before, after);
        let device = devices
            .get("00000000-0000-4000-8000-000000000001")
            .ok_or_else(|| "legacy device was not loaded".to_owned())?;
        assert_eq!(device.name, "My iPhone");
        assert!(device.permissions.contains("status"));
        assert_eq!(
            encode_base64url(device.key.as_bytes()),
            "BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc"
        );
        fs::remove_dir_all(directory).map_err(|error| error.to_string())?;
        Ok(())
    }

    #[test]
    fn symlink_and_hardlink_stores_are_rejected() -> Result<(), String> {
        let (directory, admin) = temporary_store("links")?;
        let target = directory.join("target");
        fs::write(&target, b"{}\n").map_err(|error| error.to_string())?;
        fs::set_permissions(&target, fs::Permissions::from_mode(0o640))
            .map_err(|error| error.to_string())?;
        symlink(&target, admin.store.path()).map_err(|error| error.to_string())?;
        assert!(admin.load().is_err());
        fs::remove_file(admin.store.path()).map_err(|error| error.to_string())?;
        fs::hard_link(&target, admin.store.path()).map_err(|error| error.to_string())?;
        assert!(admin.load().is_err());
        fs::remove_dir_all(directory).map_err(|error| error.to_string())?;
        Ok(())
    }

    #[test]
    fn unsafe_mode_and_oversized_store_are_rejected() -> Result<(), String> {
        let (directory, admin) = temporary_store("bounds")?;
        admin.add("Test iPhone", ["status"])?;
        fs::set_permissions(admin.store.path(), fs::Permissions::from_mode(0o660))
            .map_err(|error| error.to_string())?;
        assert!(admin.load().is_err());
        fs::set_permissions(admin.store.path(), fs::Permissions::from_mode(0o640))
            .map_err(|error| error.to_string())?;
        fs::write(
            admin.store.path(),
            vec![b'x'; MAX_DEVICE_STORE_BYTES as usize + 1],
        )
        .map_err(|error| error.to_string())?;
        assert!(admin.load().is_err());
        fs::remove_dir_all(directory).map_err(|error| error.to_string())?;
        Ok(())
    }
}
