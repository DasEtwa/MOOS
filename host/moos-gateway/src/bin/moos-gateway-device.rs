use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use moos_gateway::auth::pairing_code;
use moos_gateway::store::DeviceAdminStore;
use nix::sys::stat::{Mode, umask};
use nix::unistd::{Group, geteuid};

const VERSION: &str = env!("CARGO_PKG_VERSION");

enum Command {
    Add {
        name: String,
        permissions: Vec<String>,
    },
    List,
    Revoke {
        device_id: String,
    },
    Rotate {
        device_id: String,
    },
    Permissions {
        device_id: String,
        permissions: Vec<String>,
    },
    Help,
    Version,
}

struct Parsed {
    devices: PathBuf,
    command: Command,
}

fn usage() -> &'static str {
    "Usage:
  moos-gateway-device [--devices PATH] add --name NAME --allow status
  moos-gateway-device [--devices PATH] list
  moos-gateway-device [--devices PATH] revoke DEVICE_ID
  moos-gateway-device [--devices PATH] rotate DEVICE_ID
  moos-gateway-device [--devices PATH] permissions DEVICE_ID --allow status
  moos-gateway-device --version

All administration commands require root. Pairing codes are printed only by
add and rotate and must be transferred privately."
}

fn take_permissions(arguments: &[String], mut index: usize) -> Result<Vec<String>, String> {
    let mut permissions = Vec::new();
    while index < arguments.len() {
        if arguments.get(index).map(String::as_str) != Some("--allow") {
            return Err(format!("unknown argument: {}", arguments[index]));
        }
        let permission = arguments
            .get(index + 1)
            .ok_or_else(|| "--allow requires a value".to_owned())?;
        if permission != "status" {
            return Err("--allow supports only status".to_owned());
        }
        permissions.push(permission.clone());
        index += 2;
    }
    if permissions.is_empty() {
        return Err("at least one --allow status is required".to_owned());
    }
    Ok(permissions)
}

fn parse_command(arguments: &[String]) -> Result<Parsed, String> {
    if matches!(arguments, [flag] if flag == "-h" || flag == "--help") {
        return Ok(Parsed {
            devices: PathBuf::new(),
            command: Command::Help,
        });
    }
    if matches!(arguments, [flag] if flag == "--version") {
        return Ok(Parsed {
            devices: PathBuf::new(),
            command: Command::Version,
        });
    }
    let mut devices = PathBuf::from("/var/lib/moos-gateway/devices.json");
    let mut index = 0;
    if arguments.get(index).map(String::as_str) == Some("--devices") {
        devices = PathBuf::from(
            arguments
                .get(index + 1)
                .ok_or_else(|| "--devices requires a value".to_owned())?,
        );
        index += 2;
    }
    let command = arguments
        .get(index)
        .ok_or_else(|| "a command is required".to_owned())?;
    index += 1;
    let command = match command.as_str() {
        "list" if index == arguments.len() => Command::List,
        "add" => {
            if arguments.get(index).map(String::as_str) != Some("--name") {
                return Err("add requires --name NAME".to_owned());
            }
            let name = arguments
                .get(index + 1)
                .ok_or_else(|| "--name requires a value".to_owned())?
                .clone();
            Command::Add {
                name,
                permissions: take_permissions(arguments, index + 2)?,
            }
        }
        "revoke" if index + 1 == arguments.len() => Command::Revoke {
            device_id: arguments[index].clone(),
        },
        "rotate" if index + 1 == arguments.len() => Command::Rotate {
            device_id: arguments[index].clone(),
        },
        "permissions" => {
            let device_id = arguments
                .get(index)
                .ok_or_else(|| "permissions requires DEVICE_ID".to_owned())?
                .clone();
            Command::Permissions {
                device_id,
                permissions: take_permissions(arguments, index + 1)?,
            }
        }
        _ => return Err(format!("unknown or malformed command: {command}")),
    };
    Ok(Parsed { devices, command })
}

fn main() -> ExitCode {
    let arguments: Vec<String> = env::args().skip(1).collect();
    let parsed = match parse_command(&arguments) {
        Ok(parsed) => parsed,
        Err(error) => {
            eprintln!("error: {error}\n{}", usage());
            return ExitCode::from(2);
        }
    };
    match parsed.command {
        Command::Help => {
            println!("{}", usage());
            return ExitCode::SUCCESS;
        }
        Command::Version => {
            println!("moos-gateway-device {VERSION}");
            return ExitCode::SUCCESS;
        }
        _ => {}
    }
    if !geteuid().is_root() {
        eprintln!("error: device administration must run as root");
        return ExitCode::from(1);
    }
    let _previous_umask = umask(Mode::from_bits_truncate(0o077));
    let group = match Group::from_name("moos-gateway") {
        Ok(Some(group)) => group,
        _ => {
            eprintln!("error: moos-gateway group is unavailable");
            return ExitCode::from(1);
        }
    };
    let store = DeviceAdminStore::with_expected_owner(parsed.devices, 0, group.gid.as_raw());
    let result = match parsed.command {
        Command::Add { name, permissions } => store.add(&name, &permissions).map(|device| {
            println!("device ID: {}", device.device_id);
            println!("pairing code (shown once): {}", pairing_code(&device));
        }),
        Command::List => store.load().map(|devices| {
            for device in devices.values() {
                let state = if device.revoked {
                    "revoked"
                } else {
                    "authorized"
                };
                let grants = device
                    .permissions
                    .iter()
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(",");
                println!(
                    "{}\t{}\t{}\t{}",
                    device.device_id, state, grants, device.name
                );
            }
        }),
        Command::Revoke { device_id } => store.revoke(&device_id).map(|device| {
            println!("revoked: {}", device.device_id);
        }),
        Command::Rotate { device_id } => store.rotate(&device_id).map(|device| {
            println!("rotated: {}", device.device_id);
            println!("pairing code (shown once): {}", pairing_code(&device));
        }),
        Command::Permissions {
            device_id,
            permissions,
        } => store
            .set_permissions(&device_id, &permissions)
            .map(|device| println!("updated: {}", device.device_id)),
        Command::Help | Command::Version => return ExitCode::from(2),
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(1)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn arguments(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| (*value).to_owned()).collect()
    }

    #[test]
    fn version_does_not_require_root_or_store_arguments() {
        assert!(matches!(
            parse_command(&arguments(&["--version"])),
            Ok(Parsed {
                command: Command::Version,
                ..
            })
        ));
    }

    #[test]
    fn grants_are_explicit_and_status_only() {
        assert!(
            parse_command(&arguments(&["add", "--name", "Phone", "--allow", "status"])).is_ok()
        );
        assert!(parse_command(&arguments(&["add", "--name", "Phone", "--allow", "exec"])).is_err());
        assert!(parse_command(&arguments(&["add", "--name", "Phone"])).is_err());
    }
}
