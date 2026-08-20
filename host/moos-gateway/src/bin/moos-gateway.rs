use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use moos_gateway::gateway::serve;
use moos_gateway::platform_linux::{
    validate_process_identity, validate_tailscale_address, validate_tailscale_interface_address,
};
use moos_gateway::store::DeviceStore;
use nix::unistd::getegid;

const VERSION: &str = env!("CARGO_PKG_VERSION");

enum Command {
    Run(Options),
    ValidateAddress(Options),
    ValidateInterface(Options),
    Help,
    Version,
}

struct Options {
    listen_address: String,
    port: u16,
    devices: PathBuf,
    moosd_socket: PathBuf,
}

fn usage() -> &'static str {
    "Usage:
  moos-gateway --listen-address IP --port PORT [--devices PATH] [--moosd-socket PATH]
  moos-gateway validate-address --listen-address IP --port PORT
  moos-gateway validate-interface --listen-address IP --port PORT
  moos-gateway --version

Options:
  --listen-address IP  Literal Tailscale IPv4 or IPv6 address.
  --port PORT           TCP port from 1 to 65535.
  --devices PATH        Device store (default: /var/lib/moos-gateway/devices.json).
  --moosd-socket PATH   Local Protocol-v1 socket (default: /run/moos/moosd.sock).
  -h, --help            Show this help.
  --version             Show the binary version."
}

fn parse_options(arguments: &[String]) -> Result<Options, String> {
    let mut listen_address = None;
    let mut port = None;
    let mut devices = PathBuf::from("/var/lib/moos-gateway/devices.json");
    let mut moosd_socket = PathBuf::from("/run/moos/moosd.sock");
    let mut index = 0;
    while index < arguments.len() {
        let flag = &arguments[index];
        let value = arguments
            .get(index + 1)
            .ok_or_else(|| format!("{flag} requires a value"))?;
        match flag.as_str() {
            "--listen-address" if listen_address.is_none() => listen_address = Some(value.clone()),
            "--port" if port.is_none() => {
                let parsed = value
                    .parse::<u16>()
                    .map_err(|_| "--port must be from 1 to 65535".to_owned())?;
                if parsed == 0 {
                    return Err("--port must be from 1 to 65535".to_owned());
                }
                port = Some(parsed);
            }
            "--devices" => devices = PathBuf::from(value),
            "--moosd-socket" => moosd_socket = PathBuf::from(value),
            _ => return Err(format!("unknown or duplicate argument: {flag}")),
        }
        index += 2;
    }
    Ok(Options {
        listen_address: listen_address.ok_or_else(|| "--listen-address is required".to_owned())?,
        port: port.ok_or_else(|| "--port is required".to_owned())?,
        devices,
        moosd_socket,
    })
}

fn parse_command(arguments: &[String]) -> Result<Command, String> {
    match arguments {
        [flag] if flag == "-h" || flag == "--help" => Ok(Command::Help),
        [flag] if flag == "--version" => Ok(Command::Version),
        [first, rest @ ..] if first == "validate-address" => {
            Ok(Command::ValidateAddress(parse_options(rest)?))
        }
        [first, rest @ ..] if first == "validate-interface" => {
            Ok(Command::ValidateInterface(parse_options(rest)?))
        }
        _ => Ok(Command::Run(parse_options(arguments)?)),
    }
}

fn main() -> ExitCode {
    let arguments: Vec<String> = env::args().skip(1).collect();
    let command = match parse_command(&arguments) {
        Ok(command) => command,
        Err(error) => {
            eprintln!("error: {error}\n{}", usage());
            return ExitCode::from(2);
        }
    };
    match command {
        Command::Help => {
            println!("{}", usage());
            ExitCode::SUCCESS
        }
        Command::Version => {
            println!("moos-gateway {VERSION}");
            ExitCode::SUCCESS
        }
        Command::ValidateAddress(options) => {
            match validate_tailscale_address(&options.listen_address) {
                Ok(_) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("error: {error}");
                    ExitCode::from(2)
                }
            }
        }
        Command::ValidateInterface(options) => {
            match validate_tailscale_interface_address(&options.listen_address) {
                Ok(_) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("error: {error}");
                    ExitCode::from(2)
                }
            }
        }
        Command::Run(options) => run(options),
    }
}

fn run(options: Options) -> ExitCode {
    let address = match validate_tailscale_interface_address(&options.listen_address) {
        Ok(address) => address,
        Err(error) => {
            eprintln!("error: {error}");
            return ExitCode::from(2);
        }
    };
    if let Err(error) = validate_process_identity() {
        eprintln!("error: {error}");
        return ExitCode::from(1);
    }
    let devices = DeviceStore::with_expected_owner(options.devices, 0, getegid().as_raw());
    if let Err(error) = devices.load() {
        eprintln!("error: invalid device store: {error}");
        return ExitCode::from(1);
    }
    match serve(address, options.port, devices, &options.moosd_socket) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: gateway stopped: {error}");
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
    fn parser_requires_address_and_port() {
        assert!(parse_command(&arguments(&[])).is_err());
        assert!(parse_command(&arguments(&["--listen-address", "100.64.0.1"])).is_err());
        assert!(
            parse_command(&arguments(&[
                "--listen-address",
                "100.64.0.1",
                "--port",
                "0"
            ]))
            .is_err()
        );
    }

    #[test]
    fn version_is_standalone() {
        assert!(matches!(
            parse_command(&arguments(&["--version"])),
            Ok(Command::Version)
        ));
        assert!(parse_command(&arguments(&["--version", "x"])).is_err());
    }
}
