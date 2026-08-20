use std::fs;
use std::os::unix::fs::PermissionsExt as _;
use std::path::PathBuf;
use std::process::{Command, Stdio};

use moos_gateway::store::DeviceAdminStore;

const CHILD_ENV: &str = "MOOS_STORE_PROCESS_CHILD";
const PATH_ENV: &str = "MOOS_STORE_PROCESS_PATH";

#[test]
fn mutation_worker() -> Result<(), String> {
    if std::env::var_os(CHILD_ENV).is_none() {
        return Ok(());
    }
    let path = std::env::var_os(PATH_ENV)
        .map(PathBuf::from)
        .ok_or_else(|| "child store path is missing".to_owned())?;
    DeviceAdminStore::new(path).add("Concurrent iPhone", ["status"])?;
    Ok(())
}

#[test]
fn concurrent_process_mutations_preserve_every_device() -> Result<(), String> {
    let mut random = [0_u8; 8];
    getrandom::fill(&mut random).map_err(|_| "random failure")?;
    let suffix = random
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let directory = std::env::temp_dir().join(format!("moos-process-store-{suffix}"));
    fs::create_dir(&directory).map_err(|error| error.to_string())?;
    fs::set_permissions(&directory, fs::Permissions::from_mode(0o750))
        .map_err(|error| error.to_string())?;
    let path = directory.join("devices.json");
    let executable = std::env::current_exe().map_err(|error| error.to_string())?;
    let mut children = Vec::new();
    for _ in 0..12 {
        children.push(
            Command::new(&executable)
                .arg("--exact")
                .arg("mutation_worker")
                .arg("--nocapture")
                .env(CHILD_ENV, "1")
                .env(PATH_ENV, &path)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::piped())
                .spawn()
                .map_err(|error| error.to_string())?,
        );
    }
    for child in children {
        let output = child
            .wait_with_output()
            .map_err(|error| error.to_string())?;
        if !output.status.success() {
            return Err(format!(
                "concurrent child failed: {}",
                String::from_utf8_lossy(&output.stderr)
            ));
        }
    }
    let devices = DeviceAdminStore::new(&path).load()?;
    assert_eq!(devices.len(), 12);
    fs::remove_dir_all(directory).map_err(|error| error.to_string())?;
    Ok(())
}
