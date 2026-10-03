//! Local Native installer contract. No network, shell or client-supplied paths.
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const MIB: u64 = 1024 * 1024;
pub const MIN_TARGET: u64 = 192 * MIB;
pub const DATA_UUID: &str = "4d4f4f53-0000-4000-8000-000000000005";
const BIOS: &str = "21686148-6449-6e6f-744e-656564454649";
const EFI: &str = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b";
const LINUX: &str = "0fc63daf-8483-4772-8e79-3d69d8477de4";
const ROLES: [&str; 5] = ["BIOS_GRUB", "BOOT", "SYSTEM_A", "SYSTEM_B", "DATA"];

fn require(condition: bool, message: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(message.into())
    }
}
pub fn hash(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}
fn text(path: impl AsRef<Path>, limit: u64) -> Result<String> {
    let mut bytes = Vec::new();
    File::open(path)?.take(limit + 1).read_to_end(&mut bytes)?;
    require(bytes.len() as u64 <= limit, "oversized metadata")?;
    Ok(String::from_utf8(bytes)?)
}
fn number(path: impl AsRef<Path>) -> Result<u64> {
    Ok(text(path, 64)?.trim().parse()?)
}
fn property(path: &Path) -> String {
    text(path, 256)
        .unwrap_or_default()
        .trim()
        .chars()
        .filter(|c| c.is_ascii_graphic() || *c == ' ')
        .take(128)
        .collect()
}

fn bounded_entries(path: &Path, limit: usize, message: &str) -> Result<Vec<PathBuf>> {
    let mut entries = Vec::new();
    for entry in fs::read_dir(path)? {
        require(entries.len() < limit, message)?;
        entries.push(entry?.path());
    }
    Ok(entries)
}
pub fn random_id() -> Result<String> {
    let mut bytes = [0_u8; 16];
    // getrandom uses the Linux getrandom syscall: no pre-CRNG urandom fallback.
    getrandom::fill(&mut bytes)?;
    bytes[6] = (bytes[6] & 15) | 0x40;
    bytes[8] = (bytes[8] & 63) | 0x80;
    Ok(uuid::Uuid::from_bytes(bytes).to_string())
}
pub fn valid_id(value: &str) -> bool {
    uuid::Uuid::parse_str(value).is_ok_and(|id| {
        id.get_version_num() == 4
            && id.get_variant() == uuid::Variant::RFC4122
            && id.to_string() == value
    })
}
fn pinned_device(path: &Path, write: bool) -> Result<File> {
    let before = fs::symlink_metadata(path)?;
    require(
        before.file_type().is_block_device(),
        "not a guest block device",
    )?;
    let file = OpenOptions::new()
        .read(true)
        .write(write)
        .custom_flags(0x20000)
        .open(path)?;
    require(
        file.metadata()?.rdev() == before.rdev(),
        "device changed during open",
    )?;
    Ok(file)
}
fn alias(file: &File) -> String {
    format!("/proc/{}/fd/{}", std::process::id(), file.as_raw_fd())
}
fn device_number(file: &File) -> Result<String> {
    let device = file.metadata()?.rdev();
    let major = ((device >> 8) & 0xfff) | ((device >> 32) & 0xfffff000);
    let minor = (device & 0xff) | ((device >> 12) & 0xffffff00);
    Ok(format!("{major}:{minor}"))
}
fn require_unmounted(number: &str) -> Result<()> {
    require(
        !text("/proc/self/mountinfo", 65536)?
            .lines()
            .any(|s| s.split_whitespace().nth(2) == Some(number)),
        "target or partition is mounted",
    )
}
fn cache_flush(file: &File) -> Result<()> {
    require_unmounted(&device_number(file)?)?;
    // Guest-only BLKFLSBUF synchronizes then invalidates cache; never format/discard.
    tool("/sbin/blockdev", &["--flushbufs", &alias(file)], b"")?;
    Ok(())
}
fn range_hash(file: &File, offset: u64, size: u64) -> Result<String> {
    let mut reader = file.try_clone()?;
    reader.seek(SeekFrom::Start(offset))?;
    let mut digest = Sha256::new();
    let mut remaining = size;
    let mut buffer = [0_u8; 65536];
    while remaining > 0 {
        let count = remaining.min(buffer.len() as u64) as usize;
        reader.read_exact(&mut buffer[..count])?;
        digest.update(&buffer[..count]);
        remaining -= count as u64;
    }
    Ok(hex(&digest.finalize()))
}

/// Fixed tools only, direct argument arrays, bounded output/time, no shell.
struct ToolChild(std::process::Child);
impl std::ops::Deref for ToolChild {
    type Target = std::process::Child;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}
impl std::ops::DerefMut for ToolChild {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.0
    }
}
impl Drop for ToolChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn tool(program: &str, args: &[&str], input: &[u8]) -> Result<String> {
    let output = Path::new("/run/moos-installer/tool-output");
    let stream = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(output)?;
    let operation = (|| {
        let mut child = ToolChild(
            Command::new(program)
                .args(args)
                .env_clear()
                .env("PATH", "/sbin:/bin:/usr/sbin:/usr/bin")
                .env("LC_ALL", "C")
                .stdin(Stdio::piped())
                .stdout(stream.try_clone()?)
                .stderr(stream.try_clone()?)
                .spawn()?,
        );
        if let Some(mut stdin) = child.stdin.take() {
            stdin.write_all(input)?;
        }
        let start = Instant::now();
        loop {
            if stream.metadata()?.len() > 512 * 1024 || start.elapsed() > Duration::from_secs(120) {
                child.kill()?;
                child.wait()?;
                return Err("tool time/output bound exceeded".into());
            }
            if let Some(status) = child.try_wait()? {
                require(
                    status.success(),
                    &format!(
                        "tool failed: {program}: {}",
                        text(output, 512 * 1024)?
                            .chars()
                            .take(700)
                            .collect::<String>()
                    ),
                )?;
                return text(output, 512 * 1024);
            }
            std::thread::sleep(Duration::from_millis(25));
        }
    })();
    fs::remove_file(output)?;
    operation
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Part {
    pub name: String,
    pub start: u64,
    pub size: u64,
    #[serde(rename = "type")]
    pub kind: String,
    pub uuid: String,
    #[serde(default)]
    pub attrs: String,
    #[serde(default)]
    pub node: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Layout {
    pub id: String,
    pub partitions: Vec<Part>,
}
/// libfdisk verifies CRCs; additionally require both standard GPT copies to
/// describe the same disk. This bounded read-only check writes no GPT bytes.
pub fn validate_gpt_copies(file: &File, bytes: u64) -> Result<()> {
    require(
        bytes >= MIN_TARGET && bytes.is_multiple_of(512),
        "invalid GPT size",
    )?;
    let sectors = bytes / 512;
    let mut reader = file.try_clone()?;
    let mut primary = [0_u8; 512];
    let mut backup = [0_u8; 512];
    reader.seek(SeekFrom::Start(512))?;
    reader.read_exact(&mut primary)?;
    reader.seek(SeekFrom::Start(bytes - 512))?;
    reader.read_exact(&mut backup)?;
    let u64_at = |b: &[u8], n| -> Result<u64> { Ok(u64::from_le_bytes(b[n..n + 8].try_into()?)) };
    for (header, current, other, entries) in [
        (&primary, 1, sectors - 1, 2),
        (&backup, sectors - 1, 1, sectors - 33),
    ] {
        require(
            &header[..8] == b"EFI PART"
                && header[8..12] == [0, 0, 1, 0]
                && header[12..16] == [92, 0, 0, 0]
                && u64_at(header, 24)? == current
                && u64_at(header, 32)? == other
                && u64_at(header, 72)? == entries
                && matches!(u64_at(header, 40)?, 34 | 2048)
                && u64_at(header, 48)? == sectors - 34
                && header[80..84] == [128, 0, 0, 0]
                && header[84..88] == [128, 0, 0, 0],
            "unsupported GPT geometry",
        )?;
    }
    require(
        primary[40..72] == backup[40..72] && primary[88..92] == backup[88..92],
        "GPT headers disagree",
    )?;
    let mut a = [0_u8; 16384];
    let mut b = [0_u8; 16384];
    reader.seek(SeekFrom::Start(1024))?;
    reader.read_exact(&mut a)?;
    reader.seek(SeekFrom::Start((sectors - 33) * 512))?;
    reader.read_exact(&mut b)?;
    require(a == b, "GPT primary/backup entries disagree")
}
fn layout(file: &File) -> Result<Layout> {
    let path = alias(file);
    let verification = tool("/sbin/sfdisk", &["--verify", &path], b"")?;
    require(
        verification.contains("No errors detected")
            && ![
                "warning", "caution", "invalid", "corrupt", "damaged", "mismatch",
            ]
            .iter()
            .any(|s| verification.to_ascii_lowercase().contains(s)),
        "invalid primary/backup GPT",
    )?;
    let bytes = file.try_clone()?.seek(SeekFrom::End(0))?;
    validate_gpt_copies(file, bytes)?;
    let json: serde_json::Value =
        serde_json::from_str(&tool("/sbin/sfdisk", &["--json", &path], b"")?)?;
    let table = &json["partitiontable"];
    require(
        table["label"] == "gpt" && table["sectorsize"] == 512,
        "unsupported partition table",
    )?;
    let mut value = Layout {
        id: table["id"]
            .as_str()
            .ok_or("missing disk GUID")?
            .to_ascii_lowercase(),
        partitions: serde_json::from_value(table["partitions"].clone())?,
    };
    for part in &mut value.partitions {
        part.node.clear();
        part.uuid = part.uuid.to_ascii_lowercase();
    }
    Ok(value)
}
pub fn fresh_layout(bytes: u64) -> Result<Layout> {
    require(
        (MIN_TARGET..=64 * 1024 * 1024 * 1024).contains(&bytes) && bytes.is_multiple_of(MIB),
        "target must be aligned 192 MiB..64 GiB",
    )?;
    let mut partitions = Vec::new();
    for (i, (start, size)) in [
        (1, 1),
        (2, 16),
        (18, 60),
        (78, 60),
        (138, bytes / MIB - 139),
    ]
    .iter()
    .enumerate()
    {
        partitions.push(Part {
            name: ROLES[i].into(),
            start: start * 2048,
            size: size * 2048,
            kind: [BIOS, EFI, LINUX, LINUX, LINUX][i].into(),
            uuid: random_id()?,
            attrs: if i == 3 {
                "GUID:63".into()
            } else {
                String::new()
            },
            node: String::new(),
        });
    }
    Ok(Layout {
        id: random_id()?,
        partitions,
    })
}
pub fn validate_layout(value: &Layout, bytes: u64, installed: bool) -> Result<()> {
    require(
        (MIN_TARGET..=64 * 1024 * 1024 * 1024).contains(&bytes) && bytes.is_multiple_of(MIB),
        "invalid target size",
    )?;
    require(
        value.partitions.len() == 5,
        "missing/duplicate Native roles",
    )?;
    let mut ids = HashSet::new();
    require(
        ids.insert(value.id.to_ascii_lowercase())
            && (!installed || (valid_id(&value.id) && !value.id.starts_with("4d4f4f53-"))),
        "invalid installed disk GUID",
    )?;
    for (i, part) in value.partitions.iter().enumerate() {
        let (start, size) = [
            (1, 1),
            (2, 16),
            (18, 60),
            (78, 60),
            (138, bytes / MIB - 139),
        ][i];
        require(
            part.name == ROLES[i]
                && part.start == start * 2048
                && part.size == size * 2048
                && part
                    .kind
                    .eq_ignore_ascii_case([BIOS, EFI, LINUX, LINUX, LINUX][i])
                && part.attrs == if i == 3 { "GUID:63" } else { "" },
            "incorrect Native GPT role/range/type/attributes",
        )?;
        require(
            ids.insert(part.uuid.to_ascii_lowercase())
                && (!installed
                    || (valid_id(&part.uuid.to_ascii_lowercase())
                        && !part.uuid.to_ascii_lowercase().starts_with("4d4f4f53-"))),
            "ambiguous/prototype installed partition GUID",
        )?;
    }
    Ok(())
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Disk {
    pub kernel_name: String,
    pub model: String,
    pub serial: String,
    pub wwn: String,
    pub size: u64,
    pub logical_sector: u64,
    pub physical_sector: u64,
    pub removable: bool,
    pub read_only: bool,
    pub source: bool,
    pub sys_device: String,
    pub device_number: String,
    pub disk_sequence: u64,
    pub layout_fingerprint: String,
    pub partition_roles: Vec<String>,
    pub eligible: bool,
}
impl Disk {
    fn path(&self) -> PathBuf {
        Path::new("/dev").join(&self.kernel_name)
    }
    pub fn fingerprint(&self) -> Result<String> {
        Ok(hash(&serde_json::to_vec(self)?))
    }
}
fn require_unchanged_disk(actual: &Disk, expected: &Disk) -> Result<()> {
    require(
        actual == expected,
        "disk generation or layout changed during install",
    )
}
pub fn discover(source: &str) -> Result<Vec<Disk>> {
    let mut disks = Vec::new();
    let qemu = property(Path::new("/sys/class/dmi/id/sys_vendor")) == "QEMU";
    for path in bounded_entries(
        Path::new("/sys/class/block"),
        256,
        "too many sysfs block entries",
    )? {
        if path.join("partition").exists() {
            continue;
        }
        let name = path
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or("invalid device name")?;
        if !name.bytes().all(|b| b.is_ascii_alphanumeric()) {
            continue;
        }
        let size = number(path.join("size"))?
            .checked_mul(512)
            .ok_or("disk size overflow")?;
        if size < MIB {
            continue;
        }
        let device = pinned_device(&Path::new("/dev").join(name), false)?;
        let sys_device = fs::canonicalize(&path)?.display().to_string();
        let generation = number(path.join("diskseq"))?;
        require(
            device_number(&device)? == property(&path.join("dev")),
            "disk device number changed",
        )?;
        let virtio =
            fs::canonicalize(path.join("device/driver")).is_ok_and(|p| p.ends_with("virtio_blk"));
        let serial = property(&path.join("serial"));
        let read_only = number(path.join("ro"))? != 0;
        let source_disk = name == source;
        let eligible = qemu
            && virtio
            && !source_disk
            && !read_only
            && serial
                .strip_prefix("MOOS-N3-TARGET-")
                .is_some_and(|s| s.len() == 5 && s.bytes().all(|b| b.is_ascii_hexdigit()))
            && (MIN_TARGET..=64 * 1024 * 1024 * 1024).contains(&size)
            && size.is_multiple_of(MIB);
        let mut partition_roles = Vec::new();
        for part in bounded_entries(Path::new(&sys_device), 256, "too many sysfs device entries")? {
            if part.join("partition").exists() {
                if eligible {
                    require_unmounted(&property(&part.join("dev")))?;
                }
                let uevent = text(part.join("uevent"), 2048)?;
                let role = uevent
                    .lines()
                    .find_map(|s| s.strip_prefix("PARTNAME="))
                    .unwrap_or("unnamed");
                require(role.len() <= 72, "oversized partition role")?;
                partition_roles.push(format!(
                    "{}:{}:{}:{}",
                    number(part.join("partition"))?,
                    role,
                    number(part.join("start"))?,
                    number(part.join("size"))?
                ));
            }
        }
        partition_roles.sort();
        require(partition_roles.len() <= 16, "too many partitions")?;
        if eligible {
            cache_flush(&device)?;
        }
        let mut digest = range_hash(&device, 0, MIB)?;
        digest.push_str(&range_hash(&device, size - MIB, MIB)?);
        require(
            number(path.join("diskseq"))? == generation
                && fs::canonicalize(&path)?.display().to_string() == sys_device,
            "disk generation changed during inspection",
        )?;
        disks.push(Disk {
            kernel_name: name.into(),
            model: property(&path.join("device/model")),
            serial,
            wwn: property(&path.join("device/wwid")),
            size,
            logical_sector: number(path.join("queue/logical_block_size"))?,
            physical_sector: number(path.join("queue/physical_block_size"))?,
            removable: number(path.join("removable"))? != 0,
            read_only,
            source: source_disk,
            sys_device,
            device_number: property(&path.join("dev")),
            disk_sequence: generation,
            layout_fingerprint: hash(digest.as_bytes()),
            partition_roles,
            eligible,
        });
    }
    require(disks.len() <= 16, "too many disks")?;
    require(
        disks.iter().filter(|d| d.source).count() == 1,
        "source not uniquely identified",
    )?;
    let mut serials = HashSet::new();
    for disk in disks.iter().filter(|d| d.eligible) {
        require(serials.insert(&disk.serial), "ambiguous target serial")?;
        require(
            disk.logical_sector == 512,
            "only 512-byte logical sectors supported",
        )?;
    }
    Ok(disks)
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PayloadEntry {
    pub role: String,
    pub file: String,
    pub size: u64,
    pub sha256: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Payload {
    pub schema: u32,
    pub version: String,
    pub entries: Vec<PayloadEntry>,
}

fn verify_payload_file(path: &Path, entry: &PayloadEntry, message: &str) -> Result<()> {
    let info = fs::symlink_metadata(path)?;
    require(
        info.is_file()
            && info.nlink() == 1
            && !info.file_type().is_symlink()
            && info.len() == entry.size,
        message,
    )?;
    let file = File::open(path)?;
    require(range_hash(&file, 0, entry.size)? == entry.sha256, message)
}

impl Payload {
    pub fn verify(directory: &Path) -> Result<Self> {
        let value: Self = serde_json::from_str(&text(directory.join("manifest.json"), 8192)?)?;
        require(
            value.schema == 1
                && !value.version.is_empty()
                && value.version.len() <= 64
                && value
                    .version
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b".-+".contains(&b))
                && value.entries.len() == 6,
            "invalid payload manifest",
        )?;
        for (entry, (role, name, expected)) in value.entries.iter().zip([
            ("BOOT", "native-boot.vfat", 16 * MIB),
            ("SYSTEM_A", "rootfs.ext2", 60 * MIB),
            ("BIOS_BOOT", "boot.img", 512),
            ("BIOS_CORE", "grub.img", 0),
            ("KERNEL", "bzImage", 0),
            ("UEFI", "bootx64.efi", 0),
        ]) {
            require(
                entry.role == role
                    && entry.file == name
                    && entry.size > 0
                    && entry.size <= 60 * MIB
                    && (expected == 0 || entry.size == expected),
                "invalid payload role/size/path",
            )?;
            let path = directory.join(name);
            verify_payload_file(&path, entry, "payload integrity mismatch")?;
        }
        Ok(value)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
pub enum Mode {
    Fresh,
    PreserveData,
}
#[derive(Clone, Debug, Serialize)]
pub struct Plan {
    pub schema: u32,
    pub mode: Mode,
    pub target: Disk,
    pub target_fingerprint: String,
    pub payload: Payload,
    pub layout: Layout,
    pub nonce: String,
    pub installation_id: String,
    pub preserved_data_hash: Option<String>,
    pub destructive_summary: String,
}
impl Plan {
    pub fn token(&self) -> Result<String> {
        Ok(format!(
            "{} {}",
            if self.mode == Mode::Fresh {
                "ERASE"
            } else {
                "PRESERVE"
            },
            hash(&serde_json::to_vec(self)?)[..16].to_ascii_uppercase()
        ))
    }
    pub fn revalidate(&self, disk: &Disk, payload: &Payload, confirmation: &str) -> Result<()> {
        require(
            self.schema == 1
                && disk.eligible
                && !disk.source
                && !disk.read_only
                && disk == &self.target
                && disk.fingerprint()? == self.target_fingerprint,
            "target changed/disappeared or unsafe",
        )?;
        require(payload == &self.payload, "payload changed after planning")?;
        require(
            confirmation == self.token()?,
            "confirmation belongs to another plan",
        )?;
        validate_layout(&self.layout, disk.size, true)?;
        require(
            valid_id(&self.installation_id),
            "invalid planned installation ID",
        )
    }
}

pub struct Installer {
    source: String,
    payload_dir: PathBuf,
    pub payload: Payload,
}
impl Installer {
    pub fn start() -> Result<Self> {
        fs::create_dir_all("/run/moos-installer")?;
        fs::create_dir_all("/run/moos-installer/data")?;
        fs::set_permissions("/run/moos-installer", fs::Permissions::from_mode(0o700))?;
        let cmdline = text("/proc/cmdline", 4096)?;
        let uuid = cmdline
            .split_whitespace()
            .find_map(|s| s.strip_prefix("moos.installer=PARTUUID="))
            .ok_or("no source identity")?;
        require(
            uuid == "4d4f4f53-0000-4000-9000-000000000002",
            "unexpected source identity",
        )?;
        let mut sources = Vec::new();
        for path in bounded_entries(
            Path::new("/sys/class/block"),
            256,
            "too many sysfs block entries",
        )? {
            if text(path.join("uevent"), 2048)?
                .lines()
                .any(|s| s == format!("PARTUUID={uuid}"))
            {
                let real = fs::canonicalize(&path)?;
                sources.push((
                    real.parent()
                        .and_then(|p| p.file_name())
                        .and_then(|s| s.to_str())
                        .ok_or("invalid source disk")?
                        .to_string(),
                    path,
                ));
            }
        }
        require(sources.len() == 1, "source ambiguous/missing")?;
        let (source, partition) = sources.pop().ok_or("missing source")?;
        require(
            number(Path::new("/sys/class/block").join(&source).join("ro"))? == 1,
            "installer source must be hardware readonly",
        )?;
        let dev = Path::new("/dev").join(partition.file_name().ok_or("source name")?);
        let source_file = pinned_device(&dev, false)?;
        fs::create_dir_all("/run/moos-installer/media")?;
        tool(
            "/bin/mount",
            &[
                "-t",
                "vfat",
                "-o",
                "ro,nodev,nosuid,noexec",
                &alias(&source_file),
                "/run/moos-installer/media",
            ],
            b"",
        )?;
        let payload_dir = PathBuf::from("/run/moos-installer/media/payload");
        let payload = Payload::verify(&payload_dir)?;
        discover(&source)?;
        Ok(Self {
            source,
            payload_dir,
            payload,
        })
    }
    pub fn disks(&self) -> Result<Vec<Disk>> {
        discover(&self.source)
    }
    pub fn existing_identity(&self, target: &Disk) -> Result<String> {
        require(target.eligible && !target.source, "target ineligible")?;
        let disk = pinned_device(&target.path(), false)?;
        let value = layout(&disk)?;
        validate_layout(&value, target.size, true)?;
        let parts = self.partitions(target, &value)?;
        self.state(&parts[4])
    }
    fn select(&self, serial: &str) -> Result<Disk> {
        let mut matching = self
            .disks()?
            .into_iter()
            .filter(|d| d.serial == serial)
            .collect::<Vec<_>>();
        require(matching.len() == 1, "target missing/ambiguous")?;
        let target = matching.pop().ok_or("missing target")?;
        require(
            target.eligible && !target.source,
            "target ineligible: experimental QEMU-only gate",
        )?;
        Ok(target)
    }
    fn partitions(&self, target: &Disk, value: &Layout) -> Result<Vec<File>> {
        self.guard(target)?;
        let parent = Path::new("/sys/class/block").join(&target.kernel_name);
        let mut result = Vec::new();
        for (i, expected) in value.partitions.iter().enumerate() {
            let mut found = Vec::new();
            for item in fs::read_dir(fs::canonicalize(&parent)?)? {
                let path = item?.path();
                if path.join("partition").exists()
                    && number(path.join("partition"))? == i as u64 + 1
                {
                    require(
                        fs::canonicalize(&path)?.parent() == Some(Path::new(&target.sys_device)),
                        "partition parent changed",
                    )?;
                    require(
                        number(path.join("start"))? == expected.start
                            && number(path.join("size"))? == expected.size,
                        "kernel partition mismatch",
                    )?;
                    let metadata = text(path.join("uevent"), 2048)?;
                    require(
                        metadata
                            .lines()
                            .any(|s| s == format!("PARTNAME={}", expected.name))
                            && metadata.lines().any(|s| {
                                s.eq_ignore_ascii_case(&format!("PARTUUID={}", expected.uuid))
                            }),
                        "kernel partition identity mismatch",
                    )?;
                    let file = pinned_device(
                        &Path::new("/dev").join(path.file_name().ok_or("partition name")?),
                        true,
                    )?;
                    require(
                        property(&path.join("dev")) == device_number(&file)?,
                        "partition device number changed",
                    )?;
                    found.push(file);
                }
            }
            require(found.len() == 1, "partition missing/ambiguous")?;
            result.push(found.pop().ok_or("missing partition")?);
        }
        self.guard(target)?;
        for file in &result {
            cache_flush(file)?;
        }
        Ok(result)
    }
    fn guard(&self, target: &Disk) -> Result<()> {
        require_unchanged_disk(&self.select(&target.serial)?, target)
    }
    fn snapshot_layout(&self, target: &Disk, expected: &Layout) -> Result<Disk> {
        let actual = self.select(&target.serial)?;
        let mut actual_identity = actual.clone();
        actual_identity.layout_fingerprint = target.layout_fingerprint.clone();
        actual_identity.partition_roles = target.partition_roles.clone();
        require(
            actual_identity == *target,
            "disk generation changed while recording intended layout",
        )?;
        let disk = pinned_device(&actual.path(), false)?;
        require(
            layout(&disk)? == *expected,
            "disk layout changed while recording intended layout",
        )?;
        require(
            self.select(&target.serial)? == actual,
            "disk layout changed while recording intended layout",
        )?;
        Ok(actual)
    }
    fn state(&self, device: &File) -> Result<String> {
        let dev = alias(device);
        let probe = tool("/sbin/blkid", &[&dev], b"")?;
        require(
            probe.contains("LABEL=\"MOOS_DATA\"")
                && probe.contains(&format!("UUID=\"{DATA_UUID}\""))
                && probe.contains("TYPE=\"ext4\""),
            "invalid DATA filesystem",
        )?;
        fs::create_dir_all("/run/moos-installer/data")?;
        tool(
            "/bin/mount",
            &[
                "-t",
                "ext4",
                "-o",
                "ro,noload,nodev,nosuid,noexec",
                &dev,
                "/run/moos-installer/data",
            ],
            b"",
        )?;
        let checked = (|| {
            let root = Path::new("/run/moos-installer/data");
            for path in [
                root.to_path_buf(),
                root.join("identity"),
                root.join("config"),
            ] {
                let info = fs::symlink_metadata(path)?;
                require(
                    info.is_dir()
                        && info.uid() == 0
                        && info.gid() == 0
                        && info.mode() & 0o7777 == 0o700,
                    "unsafe DATA directory",
                )?;
            }
            for (name, size) in [("state-version", 2), ("identity/installation-id", 37)] {
                let info = fs::symlink_metadata(root.join(name))?;
                require(
                    info.is_file()
                        && info.uid() == 0
                        && info.gid() == 0
                        && info.mode() & 0o7777 == 0o600
                        && info.nlink() == 1
                        && info.len() == size,
                    "unsafe DATA metadata",
                )?;
            }
            require(
                text(root.join("state-version"), 2)? == "1\n",
                "unsupported state schema",
            )?;
            let identity = text(root.join("identity/installation-id"), 37)?;
            let id = identity
                .strip_suffix('\n')
                .ok_or("invalid installation identity encoding")?;
            require(
                identity.len() == 37 && valid_id(id),
                "invalid installation identity",
            )?;
            Ok(id.to_string())
        })();
        tool("/bin/umount", &["/run/moos-installer/data"], b"")?;
        checked
    }
    fn verify_core(&self, device: &File, version: &str) -> Result<()> {
        let root = Path::new("/run/moos-installer/system");
        fs::create_dir_all(root)?;
        tool(
            "/bin/mount",
            &[
                "-t",
                "ext4",
                "-o",
                "ro,noload,nodev,nosuid,noexec",
                &alias(device),
                "/run/moos-installer/system",
            ],
            b"",
        )?;
        let checked: Result<()> = (|| {
            for name in ["etc/shadow", "etc/moos-release", "etc/moos-platform"] {
                let info = fs::symlink_metadata(root.join(name))?;
                require(
                    info.is_file() && info.nlink() == 1 && info.uid() == 0 && info.gid() == 0,
                    "unsafe Core identity metadata",
                )?;
            }
            let shadow = text(root.join("etc/shadow"), 8192)?;
            require(
                fs::symlink_metadata(root.join("etc/shadow"))?.mode() & 0o7777 == 0o600,
                "unsafe release shadow permissions",
            )?;
            let roots = shadow
                .lines()
                .filter(|s| s.starts_with("root:"))
                .collect::<Vec<_>>();
            require(
                roots.len() == 1
                    && roots[0]
                        .split(':')
                        .nth(1)
                        .is_some_and(|p| p.starts_with('!') || p.starts_with('*')),
                "installed release root is not locked",
            )?;
            require(
                text(root.join("etc/moos-release"), 4096)?
                    == format!("MOOS_NAME='MOOS'\nMOOS_VERSION='{version}'\n")
                    && text(root.join("etc/moos-platform"), 4096)?
                        == "MOOS_BACKEND='native'\nMOOS_ARCHITECTURE='x86_64'\n",
                "installed Core identity/version mismatch",
            )?;
            for directory in ["usr/bin", "bin", "usr/lib"] {
                let mut count = 0;
                for item in fs::read_dir(root.join(directory))? {
                    count += 1;
                    require(count <= 1024, "oversized Core directory")?;
                    require(
                        !item?.file_name().to_string_lossy().starts_with("python"),
                        "Python leaked into installed Core",
                    )?;
                }
            }
            for name in [
                "usr/bin/moos-native-installer",
                "sbin/grub-bios-setup",
                "etc/init.d/S99moos-test-state",
            ] {
                require(
                    fs::symlink_metadata(root.join(name))
                        .is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
                    "installer/test-only file leaked into installed Core",
                )?;
            }
            Ok(())
        })();
        tool("/bin/umount", &["/run/moos-installer/system"], b"")?;
        checked
    }
    pub fn plan(&self, serial: &str, mode: Mode) -> Result<Plan> {
        let target = self.select(serial)?;
        let payload = Payload::verify(&self.payload_dir)?;
        let disk = pinned_device(&target.path(), false)?;
        let (value, identity, preserved) = if mode == Mode::PreserveData {
            let value = layout(&disk)?;
            validate_layout(&value, target.size, true)?;
            let partitions = self.partitions(&target, &value)?;
            let identity = self.state(&partitions[4])?;
            let data_hash = range_hash(&partitions[4], 0, value.partitions[4].size * 512)?;
            (value, identity, Some(data_hash))
        } else {
            (fresh_layout(target.size)?, random_id()?, None)
        };
        Ok(Plan {
            schema: 1,
            mode,
            target_fingerprint: target.fingerprint()?,
            target,
            payload,
            layout: value,
            nonce: random_id()?,
            installation_id: identity,
            preserved_data_hash: preserved,
            destructive_summary: if mode == Mode::Fresh {
                "ALL DATA ON THIS TARGET WILL BE ERASED"
            } else {
                "REINSTALL SYSTEM; PRESERVE DATA; replace BOOT/SYSTEM_A/reserved SYSTEM_B only"
            }
            .into(),
        })
    }
    pub fn apply(&self, plan: &Plan, confirmation: &str) -> Result<()> {
        let target = self.select(&plan.target.serial)?;
        let mut guarded_target = target.clone();
        let disk = pinned_device(&target.path(), true)?;
        plan.revalidate(
            &self.select(&plan.target.serial)?,
            &Payload::verify(&self.payload_dir)?,
            confirmation,
        )?;
        // The descriptor remains pinned through writes; tools receive its parent FD alias.
        if plan.mode == Mode::PreserveData {
            let current = layout(&disk)?;
            require(current == plan.layout, "preserve GPT changed")?;
            let parts = self.partitions(&target, &current)?;
            require(
                self.state(&parts[4])? == plan.installation_id
                    && Some(range_hash(&parts[4], 0, current.partitions[4].size * 512)?)
                        == plan.preserved_data_hash,
                "preserved DATA changed",
            )?;
        }
        phase("CONFIRMED");
        if plan.mode == Mode::Fresh {
            phase("WRITING_LAYOUT");
            self.guard(&target)?;
            let mut script = format!("label: gpt\nlabel-id: {}\nunit: sectors\n", plan.layout.id);
            for part in &plan.layout.partitions {
                script.push_str(&format!(
                    "start={}, size={}, type={}, uuid={}, name=\"{}\"{}\n",
                    part.start,
                    part.size,
                    part.kind,
                    part.uuid,
                    part.name,
                    if part.attrs.is_empty() {
                        String::new()
                    } else {
                        format!(", attrs=\"{}\"", part.attrs)
                    }
                ));
            }
            tool(
                "/sbin/sfdisk",
                &[
                    "--wipe",
                    "always",
                    "--wipe-partitions",
                    "always",
                    &alias(&disk),
                ],
                script.as_bytes(),
            )?;
            disk.sync_all()?;
        }
        let observed = layout(&disk)?;
        validate_layout(&observed, target.size, true)?;
        require(
            observed.id == plan.layout.id,
            "written disk GUID differs from plan",
        )?;
        for (expected, actual) in plan.layout.partitions.iter().zip(&observed.partitions) {
            require(
                expected.uuid.eq_ignore_ascii_case(&actual.uuid),
                "written GUID mismatch",
            )?;
        }
        if plan.mode == Mode::Fresh {
            guarded_target = self.snapshot_layout(&target, &plan.layout)?;
        }
        let parts = self.partitions(&guarded_target, &observed)?;
        phase("WRITING_SYSTEM");
        self.guard(&guarded_target)?;
        copy_payload(&self.payload_dir.join("rootfs.ext2"), &parts[2], 60 * MIB)?;
        zero(&parts[3], 60 * MIB)?;
        phase("WRITING_BOOT");
        self.guard(&guarded_target)?;
        copy_payload(
            &self.payload_dir.join("native-boot.vfat"),
            &parts[1],
            16 * MIB,
        )?;
        let boot_id = plan.layout.partitions[1].uuid[..8].to_ascii_uppercase();
        tool(
            "/usr/bin/mlabel",
            &["-i", &alias(&parts[1]), "-N", &boot_id, "::"],
            b"",
        )?;
        fs::create_dir_all("/run/moos-installer/boot")?;
        tool(
            "/bin/mount",
            &[
                "-t",
                "vfat",
                "-o",
                "rw,nodev,nosuid,noexec",
                &alias(&parts[1]),
                "/run/moos-installer/boot",
            ],
            b"",
        )?;
        let boot_result: Result<()> = (|| {
            let root = Path::new("/run/moos-installer/boot");
            let config = installed_config(plan);
            for name in ["boot/grub/grub.cfg", "EFI/BOOT/grub.cfg"] {
                let path = root.join(name);
                let mut file = OpenOptions::new()
                    .write(true)
                    .truncate(true)
                    .custom_flags(0x20000)
                    .open(path)?;
                file.write_all(config.as_bytes())?;
                file.sync_all()?;
            }
            let mut marker = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(root.join(format!("moos-boot-{}", plan.layout.partitions[1].uuid)))?;
            marker.write_all(plan.layout.partitions[1].uuid.as_bytes())?;
            marker.sync_all()?;
            Ok(())
        })();
        tool("/bin/umount", &["/run/moos-installer/boot"], b"")?;
        boot_result?;
        self.guard(&guarded_target)?;
        let bios_hashes = self.embed_bios(plan, &guarded_target, &disk)?;
        guarded_target = self.snapshot_layout(&guarded_target, &plan.layout)?;
        if plan.mode == Mode::Fresh {
            phase("INITIALIZING_DATA");
            self.guard(&guarded_target)?;
            tool(
                "/sbin/mkfs.ext4",
                &[
                    "-F",
                    "-q",
                    "-L",
                    "MOOS_DATA",
                    "-U",
                    DATA_UUID,
                    "-O",
                    "^64bit",
                    "-E",
                    "root_owner=0:0,lazy_itable_init=0,lazy_journal_init=0",
                    &alias(&parts[4]),
                ],
                b"",
            )?;
            tool(
                "/bin/mount",
                &[
                    "-t",
                    "ext4",
                    "-o",
                    "rw,nodev,nosuid,noexec",
                    &alias(&parts[4]),
                    "/run/moos-installer/data",
                ],
                b"",
            )?;
            let initialized: Result<()> = (|| {
                let root = Path::new("/run/moos-installer/data");
                fs::set_permissions(root, fs::Permissions::from_mode(0o700))?;
                for name in ["identity", "config"] {
                    fs::create_dir(root.join(name))?;
                    fs::set_permissions(root.join(name), fs::Permissions::from_mode(0o700))?;
                }
                for (name, value) in [
                    ("state-version", "1\n".to_string()),
                    (
                        "identity/installation-id",
                        format!("{}\n", plan.installation_id),
                    ),
                ] {
                    let mut file = OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .mode(0o600)
                        .open(root.join(name))?;
                    file.write_all(value.as_bytes())?;
                    file.sync_all()?;
                }
                File::open(root)?.sync_all()?;
                Ok(())
            })();
            tool("/bin/umount", &["/run/moos-installer/data"], b"")?;
            initialized?;
        }
        disk.sync_all()?;
        phase("VERIFYING");
        cache_flush(&disk)?;
        for part in &parts {
            part.sync_all()?;
            cache_flush(part)?;
        }
        require(
            range_hash(&disk, 0, 512)? == bios_hashes[0]
                && range_hash(&disk, MIB, MIB)? == bios_hashes[1],
            "final BIOS bytes mismatch",
        )?;
        let final_layout = layout(&disk)?;
        validate_layout(&final_layout, target.size, true)?;
        require(final_layout == observed, "final GPT changed")?;
        require(
            range_hash(&parts[2], 0, 60 * MIB)? == plan.payload.entries[1].sha256,
            "written SYSTEM_A integrity mismatch",
        )?;
        self.verify_core(&parts[2], &plan.payload.version)?;
        require(
            range_hash(&parts[3], 0, 60 * MIB)? == zero_hash(60 * MIB),
            "SYSTEM_B not reserved empty",
        )?;
        require(
            self.state(&parts[4])? == plan.installation_id,
            "installed identity mismatch",
        )?;
        if let Some(expected) = &plan.preserved_data_hash {
            require(
                range_hash(&parts[4], 0, plan.layout.partitions[4].size * 512)? == *expected,
                "preserve operation modified DATA",
            )?;
        }
        // Verify boot contents read-only after BIOS setup and unmount.
        tool(
            "/bin/mount",
            &[
                "-t",
                "vfat",
                "-o",
                "ro,nodev,nosuid,noexec",
                &alias(&parts[1]),
                "/run/moos-installer/boot",
            ],
            b"",
        )?;
        let verified: Result<()> = (|| {
            let root = Path::new("/run/moos-installer/boot");
            let a = text(root.join("boot/grub/grub.cfg"), 4096)?;
            require(
                a == installed_config(plan)
                    && a == text(root.join("EFI/BOOT/grub.cfg"), 4096)?
                    && text(
                        root.join(format!("moos-boot-{}", plan.layout.partitions[1].uuid)),
                        36,
                    )? == plan.layout.partitions[1].uuid,
                "installed boot config mismatch",
            )?;
            for (name, entry) in [
                ("bzImage", &plan.payload.entries[4]),
                ("EFI/BOOT/bootx64.efi", &plan.payload.entries[5]),
            ] {
                let file = File::open(root.join(name))?;
                require(
                    file.metadata()?.len() == entry.size
                        && range_hash(&file, 0, entry.size)? == entry.sha256,
                    "written kernel/EFI integrity mismatch",
                )?;
            }
            Ok(())
        })();
        tool("/bin/umount", &["/run/moos-installer/boot"], b"")?;
        verified?;
        Payload::verify(&self.payload_dir)?;
        self.guard(&guarded_target)?;
        phase("COMPLETE");
        Ok(())
    }
    fn embed_bios(&self, plan: &Plan, guarded_target: &Disk, disk: &File) -> Result<[String; 2]> {
        // GRUB canonicalizes device maps. It therefore receives NO guest device:
        // embed normally on private sparse regular GPT staging, then copy only
        // protective MBR + BIOS_GRUB through the retained target descriptor.
        let directory = Path::new("/run/moos-installer/bios-stage");
        fs::create_dir(directory)?;
        let staging_path = directory.join("disk.img");
        let staging = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&staging_path)?;
        staging.set_len(plan.target.size)?;
        let mut script = format!("label: gpt\nlabel-id: {}\nunit: sectors\n", plan.layout.id);
        for part in &plan.layout.partitions {
            script.push_str(&format!(
                "start={}, size={}, type={}, uuid={}, name=\"{}\"{}\n",
                part.start,
                part.size,
                part.kind,
                part.uuid,
                part.name,
                if part.attrs.is_empty() {
                    String::new()
                } else {
                    format!(", attrs=\"{}\"", part.attrs)
                }
            ));
        }
        tool(
            "/sbin/sfdisk",
            &[staging_path.to_str().ok_or("stage path")?],
            script.as_bytes(),
        )?;
        for (index, name) in [(2, "boot.img"), (3, "grub.img")] {
            let staged_path = directory.join(name);
            fs::copy(self.payload_dir.join(name), &staged_path)?;
            let entry = &plan.payload.entries[index];
            verify_payload_file(
                &staged_path,
                entry,
                "staged BIOS payload differs from planned manifest",
            )?;
        }
        fs::write(
            directory.join("device.map"),
            format!("(hd0) {}\n", staging_path.display()),
        )?;
        tool(
            "/sbin/grub-bios-setup",
            &[
                "--directory=/run/moos-installer/bios-stage",
                "--boot-image=boot.img",
                "--core-image=grub.img",
                "--device-map=/run/moos-installer/bios-stage/device.map",
                "(hd0)",
            ],
            b"",
        )?;
        self.guard(guarded_target)?;
        let hashes = [
            range_hash(&staging, 0, 512)?,
            range_hash(&staging, MIB, MIB)?,
        ];
        for (offset, size) in [(0, 512), (MIB, MIB)] {
            let mut from = staging.try_clone()?;
            from.seek(SeekFrom::Start(offset))?;
            let mut to = disk.try_clone()?;
            to.seek(SeekFrom::Start(offset))?;
            require(
                std::io::copy(&mut from.take(size), &mut to)? == size,
                "short BIOS copy",
            )?;
            to.sync_all()?;
            require(
                range_hash(disk, offset, size)? == range_hash(&staging, offset, size)?,
                "BIOS write mismatch",
            )?;
        }
        for name in ["disk.img", "boot.img", "grub.img", "device.map"] {
            fs::remove_file(directory.join(name))?;
        }
        fs::remove_dir(directory)?;
        Ok(hashes)
    }
}
fn installed_config(plan: &Plan) -> String {
    format!(
        "serial --unit=0 --speed=115200\nterminal_input serial\nterminal_output serial\nset timeout=1\nmenuentry 'MOOS Native (release)' {{\n echo 'MOOS Native disk boot (GRUB)'\n search --no-floppy --file --set=root /moos-boot-{}\n linux /bzImage root=PARTUUID={} rootwait console=tty0 console=ttyS0,115200\n}}\n",
        plan.layout.partitions[1].uuid, plan.layout.partitions[2].uuid
    )
}
fn copy_payload(path: &Path, target: &File, size: u64) -> Result<()> {
    let mut source = File::open(path)?;
    let mut destination = target.try_clone()?;
    destination.seek(SeekFrom::Start(0))?;
    require(
        std::io::copy(&mut Read::by_ref(&mut source).take(size), &mut destination)? == size,
        "short payload/write",
    )?;
    destination.sync_all()?;
    Ok(())
}
fn zero(target: &File, size: u64) -> Result<()> {
    let mut destination = target.try_clone()?;
    destination.seek(SeekFrom::Start(0))?;
    let buffer = [0_u8; 65536];
    for _ in 0..size / 65536 {
        destination.write_all(&buffer)?;
    }
    destination.sync_all()?;
    Ok(())
}
fn zero_hash(size: u64) -> String {
    let mut digest = Sha256::new();
    for _ in 0..size / 65536 {
        digest.update([0_u8; 65536]);
    }
    hex(&digest.finalize())
}
pub fn phase(name: &str) {
    println!("INSTALL_STATE={name}");
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod security_tests {
    use super::*;

    fn private_directory(name: &str) -> Result<PathBuf> {
        let path = std::env::temp_dir().join(format!("moos-installer-{name}-{}", random_id()?));
        fs::create_dir(&path)?;
        Ok(path)
    }

    #[test]
    fn bounded_directory_enumeration_rejects_overflow() -> Result<()> {
        let directory = private_directory("entries")?;
        for index in 0..3 {
            File::create(directory.join(index.to_string()))?;
        }
        require(
            bounded_entries(&directory, 3, "overflow")?.len() == 3,
            "bounded enumeration lost entries",
        )?;
        require(
            bounded_entries(&directory, 2, "overflow").is_err(),
            "overflowing enumeration was silently truncated",
        )?;
        fs::remove_dir_all(directory)?;
        Ok(())
    }

    #[test]
    fn staged_bios_bytes_must_match_planned_digest() -> Result<()> {
        let directory = private_directory("bios")?;
        let path = directory.join("boot.img");
        fs::write(&path, b"planned")?;
        let entry = PayloadEntry {
            role: "BIOS_BOOT".into(),
            file: "boot.img".into(),
            size: 7,
            sha256: hash(b"planned"),
        };
        verify_payload_file(&path, &entry, "mismatch")?;
        fs::write(&path, b"changed")?;
        require(
            verify_payload_file(&path, &entry, "mismatch").is_err(),
            "changed BIOS bytes matched the planned manifest",
        )?;
        fs::remove_dir_all(directory)?;
        Ok(())
    }

    #[test]
    fn mutation_guard_rejects_layout_and_role_drift() -> Result<()> {
        let expected = Disk {
            kernel_name: "vdb".into(),
            model: "test".into(),
            serial: "MOOS-N3-TARGET-00001".into(),
            wwn: String::new(),
            size: 512 * MIB,
            logical_sector: 512,
            physical_sector: 512,
            removable: false,
            read_only: false,
            source: false,
            sys_device: "/sys/devices/test".into(),
            device_number: "254:16".into(),
            disk_sequence: 2,
            layout_fingerprint: "planned".into(),
            partition_roles: vec!["1:MOOS_BIOS:2048:2048".into()],
            eligible: true,
        };
        require_unchanged_disk(&expected, &expected)?;
        let mut changed = expected.clone();
        changed.layout_fingerprint = "changed".into();
        require(
            require_unchanged_disk(&changed, &expected).is_err(),
            "layout fingerprint drift passed the mutation guard",
        )?;
        changed = expected.clone();
        changed
            .partition_roles
            .push("2:MOOS_BOOT:4096:32768".into());
        require(
            require_unchanged_disk(&changed, &expected).is_err(),
            "partition role drift passed the mutation guard",
        )
    }
}
