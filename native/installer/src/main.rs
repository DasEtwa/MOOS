use moos_native_installer::{Installer, Mode, Result, phase};
use std::io::{self, BufRead, Read, Write};
use std::time::{Duration, Instant};

fn run() -> Result<()> {
    println!("MOOS Native Installer — experimental QEMU targets only; offline Core install");
    let installer = Installer::start()?;
    let mut planned = None;
    let mut input = io::stdin().lock();
    loop {
        println!(
            "Commands: inspect | plan SERIAL fresh|preserve | confirm ERASE|PRESERVE DIGEST | cancel | poweroff"
        );
        print!("installer> ");
        io::stdout().flush()?;
        let mut bytes = Vec::new();
        let count = input.by_ref().take(257).read_until(b'\n', &mut bytes)?;
        if count == 0 {
            return Err("console closed".into());
        }
        if count > 256 || !bytes.ends_with(b"\n") {
            return Err("oversized/incomplete command".into());
        }
        let line = String::from_utf8(bytes)?;
        let words = line.split_whitespace().collect::<Vec<_>>();
        let action = (|| -> Result<()> {
            match words.as_slice() {
                ["inspect"] => {
                    phase("INSPECT");
                    let disks = installer.disks()?;
                    println!("{} disks detected.", disks.len());
                    for disk in &disks {
                        println!(
                            "Disk: {} | Model: {} | Serial: {} | {} MiB | {}",
                            disk.kernel_name,
                            if disk.model.is_empty() {
                                "not available"
                            } else {
                                &disk.model
                            },
                            disk.serial,
                            disk.size / 1024 / 1024,
                            if disk.source {
                                "INSTALLER MEDIA — READ ONLY — NOT ELIGIBLE"
                            } else if disk.eligible {
                                "QEMU test candidate"
                            } else {
                                "NOT ELIGIBLE"
                            }
                        );
                        if disk.eligible {
                            match installer.existing_identity(disk) {
                                Ok(id) => println!(
                                    "Existing MOOS Native: schema 1; installation ID {id}; preserve candidate"
                                ),
                                Err(_) => println!(
                                    "Existing validated MOOS state: unavailable; preserve not offered"
                                ),
                            }
                        }
                    }
                    println!("DISKS={}", serde_json::to_string(&disks)?);
                }
                ["plan", serial, mode] => {
                    planned = None;
                    let mode = match *mode {
                        "fresh" => Mode::Fresh,
                        "preserve" => Mode::PreserveData,
                        _ => return Err("mode must be fresh or preserve".into()),
                    };
                    let plan = installer.plan(serial, mode)?;
                    phase("PLANNED");
                    println!(
                        "Target model: {}\nTarget serial: {}\nTarget size: {} MiB\nMode: {:?}\nDATA preserved: {}",
                        if plan.target.model.is_empty() {
                            "not available"
                        } else {
                            &plan.target.model
                        },
                        plan.target.serial,
                        plan.target.size / 1024 / 1024,
                        plan.mode,
                        plan.mode == Mode::PreserveData
                    );
                    println!("PLAN={}", serde_json::to_string(&plan)?);
                    println!(
                        "Current partition roles: {:?}\nOffline Core payload version: {}",
                        plan.target.partition_roles, plan.payload.version
                    );
                    println!("{}", plan.destructive_summary);
                    println!("Type exactly: confirm {}", plan.token()?);
                    planned = Some((plan, Instant::now()));
                }
                ["confirm", verb, digest] => {
                    let (plan, created) = planned.take().ok_or("no active plan")?;
                    if created.elapsed() > Duration::from_secs(120) {
                        return Err("plan expired; inspect again".into());
                    }
                    installer.apply(&plan, &format!("{verb} {digest}"))?;
                }
                ["cancel"] => {
                    planned = None;
                    phase("CANCELLED");
                }
                ["poweroff"] => {
                    std::process::Command::new("/sbin/poweroff")
                        .env_clear()
                        .status()?;
                }
                _ => return Err("unsupported local operation".into()),
            }
            Ok(())
        })();
        if let Err(error) = action {
            phase("FAILED");
            println!(
                "ERROR={}",
                error.to_string().chars().take(800).collect::<String>()
            );
        }
    }
}
fn main() {
    if let Err(error) = run() {
        phase("BLOCKED");
        eprintln!(
            "ERROR={}",
            error.to_string().chars().take(800).collect::<String>()
        );
    }
}
