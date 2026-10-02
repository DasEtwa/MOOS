use moos_native_installer::{
    Disk, MIB, MIN_TARGET, Mode, Payload, Plan, fresh_layout, random_id, validate_layout,
};

fn target() -> Disk {
    Disk {
        kernel_name: "vdb".into(),
        model: "Virtio".into(),
        serial: "MOOS-N3-TARGET-12abc".into(),
        wwn: String::new(),
        size: 512 * MIB,
        logical_sector: 512,
        physical_sector: 512,
        removable: false,
        read_only: false,
        source: false,
        sys_device: "/sys/devices/virtio1".into(),
        device_number: "254:16".into(),
        disk_sequence: 7,
        layout_fingerprint: "blank".into(),
        partition_roles: vec![],
        eligible: true,
    }
}
fn plan() -> Result<Plan, Box<dyn std::error::Error>> {
    let disk = target();
    Ok(Plan {
        schema: 1,
        mode: Mode::Fresh,
        target_fingerprint: disk.fingerprint()?,
        layout: fresh_layout(disk.size)?,
        target: disk,
        payload: Payload {
            schema: 1,
            version: "test".into(),
            entries: vec![],
        },
        nonce: random_id()?,
        installation_id: random_id()?,
        preserved_data_hash: None,
        destructive_summary: "ALL DATA ON THIS TARGET WILL BE ERASED".into(),
    })
}
#[test]
fn layout_unique_bounded_and_data_grows() -> Result<(), Box<dyn std::error::Error>> {
    assert!(fresh_layout(MIN_TARGET - MIB).is_err());
    assert!(fresh_layout(MIN_TARGET + 1).is_err());
    let a = fresh_layout(MIN_TARGET)?;
    let b = fresh_layout(512 * MIB)?;
    validate_layout(&a, MIN_TARGET, true)?;
    validate_layout(&b, 512 * MIB, true)?;
    assert_ne!(a.id, b.id);
    for (x, y) in a.partitions.iter().zip(&b.partitions) {
        assert_ne!(x.uuid, y.uuid);
    }
    assert_eq!(b.partitions[4].size, 373 * 2048);
    assert_eq!(b.partitions[3].attrs, "GUID:63");
    let mut malformed = b.clone();
    malformed.partitions[4].name = "SYSTEM_A".into();
    assert!(validate_layout(&malformed, 512 * MIB, true).is_err());
    malformed = b.clone();
    malformed.partitions[4].uuid = "4d4f4f53-0000-4000-8000-000000000005".into();
    assert!(validate_layout(&malformed, 512 * MIB, true).is_err());
    malformed = b.clone();
    malformed.partitions[4].uuid = malformed.partitions[3].uuid.clone();
    assert!(validate_layout(&malformed, 512 * MIB, true).is_err());
    Ok(())
}
#[test]
fn confirmation_and_target_revalidation_are_bound() -> Result<(), Box<dyn std::error::Error>> {
    let p = plan()?;
    let token = p.token()?;
    p.revalidate(&p.target, &p.payload, &token)?;
    assert!(p.revalidate(&p.target, &p.payload, "ERASE yes").is_err());
    assert!(
        p.revalidate(&p.target, &p.payload, &plan()?.token()?)
            .is_err()
    );
    for change in 0..8 {
        let mut disk = p.target.clone();
        match change {
            0 => disk.serial = "MOOS-N3-TARGET-fffff".into(),
            1 => disk.model = "other".into(),
            2 => disk.size += MIB,
            3 => disk.layout_fingerprint = "foreign".into(),
            4 => disk.source = true,
            5 => disk.read_only = true,
            6 => disk.disk_sequence += 1,
            _ => disk.eligible = false,
        }
        assert!(p.revalidate(&disk, &p.payload, &token).is_err());
    }
    let mut payload = p.payload.clone();
    payload.version = "changed".into();
    assert!(p.revalidate(&p.target, &payload, &token).is_err());
    let mut changed = p.clone();
    changed.mode = Mode::PreserveData;
    assert!(
        changed
            .revalidate(&changed.target, &changed.payload, &token)
            .is_err()
    );
    Ok(())
}
#[test]
fn payload_corruption_and_manifest_paths_are_rejected() -> Result<(), Box<dyn std::error::Error>> {
    let dir = std::env::temp_dir().join(format!("moos-n3-payload-{}", random_id()?));
    std::fs::create_dir(&dir)?;
    std::fs::write(
        dir.join("manifest.json"),
        r#"{"schema":1,"version":"test","entries":[]}"#,
    )?;
    assert!(Payload::verify(&dir).is_err());
    std::fs::write(
        dir.join("manifest.json"),
        r#"{"schema":1,"version":"test","entries":[],"command":"mkfs"}"#,
    )?;
    assert!(Payload::verify(&dir).is_err());
    let mut entries = vec![];
    for (role, name, size) in [
        ("BOOT", "native-boot.vfat", 16 * MIB),
        ("SYSTEM_A", "rootfs.ext2", 60 * MIB),
        ("BIOS_BOOT", "boot.img", 512),
        ("BIOS_CORE", "grub.img", 1024),
        ("KERNEL", "bzImage", 1024),
        ("UEFI", "bootx64.efi", 1024),
    ] {
        let bytes = vec![0u8; size as usize];
        std::fs::write(dir.join(name), &bytes)?;
        entries.push(moos_native_installer::PayloadEntry {
            role: role.into(),
            file: name.into(),
            size,
            sha256: moos_native_installer::hash(&bytes),
        });
    }
    let payload = Payload {
        schema: 1,
        version: "test".into(),
        entries,
    };
    std::fs::write(dir.join("manifest.json"), serde_json::to_vec(&payload)?)?;
    assert_eq!(Payload::verify(&dir)?, payload);
    for change in 0..3 {
        let mut bad = payload.clone();
        match change {
            0 => bad.entries[0].file = "../rootfs.ext2".into(),
            1 => bad.entries[5].sha256 = "0".repeat(64),
            _ => {
                bad.entries.pop();
            }
        }
        std::fs::write(dir.join("manifest.json"), serde_json::to_vec(&bad)?)?;
        assert!(Payload::verify(&dir).is_err());
    }
    std::fs::write(dir.join("manifest.json"), serde_json::to_vec(&payload)?)?;
    std::fs::write(dir.join("bootx64.efi"), b"corrupted payload")?;
    assert!(Payload::verify(&dir).is_err());
    for entry in &payload.entries {
        std::fs::remove_file(dir.join(&entry.file))?;
    }
    std::fs::remove_file(dir.join("manifest.json"))?;
    std::fs::remove_dir(dir)?;
    Ok(())
}

#[test]
fn gpt_copies_must_agree_even_when_libfdisk_accepts_both_crcs()
-> Result<(), Box<dyn std::error::Error>> {
    use std::io::{Read, Seek, SeekFrom, Write};
    // Test-only CRC mutation. Production delegates CRC validation/writes to libfdisk.
    fn crc(bytes: &[u8]) -> u32 {
        let mut c = !0u32;
        for byte in bytes {
            c ^= u32::from(*byte);
            for _ in 0..8 {
                c = (c >> 1) ^ if c & 1 != 0 { 0xedb88320 } else { 0 };
            }
        }
        !c
    }
    let path = std::env::temp_dir().join(format!("moos-n3-gpt-{}.img", random_id()?));
    let mut file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(&path)?;
    file.set_len(MIN_TARGET)?;
    let mut child = std::process::Command::new("/usr/sbin/sfdisk")
        .arg(&path)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()?;
    child
        .stdin
        .take()
        .ok_or("stdin missing")?
        .write_all(b"label: gpt\nstart=2048,size=2048,name=TEST\n")?;
    assert!(child.wait()?.success());
    moos_native_installer::validate_gpt_copies(&file, MIN_TARGET)?;
    let offset = MIN_TARGET - 33 * 512;
    let mut entries = [0u8; 16384];
    file.seek(SeekFrom::Start(offset))?;
    file.read_exact(&mut entries)?;
    entries[56] = b'X';
    file.seek(SeekFrom::Start(offset))?;
    file.write_all(&entries)?;
    let mut header = [0u8; 512];
    file.seek(SeekFrom::Start(MIN_TARGET - 512))?;
    file.read_exact(&mut header)?;
    header[88..92].copy_from_slice(&crc(&entries).to_le_bytes());
    header[16..20].fill(0);
    let checksum = crc(&header[..92]);
    header[16..20].copy_from_slice(&checksum.to_le_bytes());
    file.seek(SeekFrom::Start(MIN_TARGET - 512))?;
    file.write_all(&header)?;
    file.sync_all()?;
    let result = std::process::Command::new("/usr/sbin/sfdisk")
        .env("LC_ALL", "C")
        .arg("--verify")
        .arg(&path)
        .output()?;
    assert!(result.status.success());
    assert!(String::from_utf8(result.stdout)?.contains("No errors detected"));
    assert!(moos_native_installer::validate_gpt_copies(&file, MIN_TARGET).is_err());
    drop(file);
    std::fs::remove_file(path)?;
    Ok(())
}
