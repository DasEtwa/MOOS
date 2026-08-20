use std::collections::BTreeSet;
use std::ffi::OsString;
use std::io;
use std::net::{IpAddr, SocketAddr, TcpListener};
use std::os::fd::{AsRawFd as _, OwnedFd};

use nix::sys::socket::{
    AddressFamily, Backlog, SockFlag, SockType, SockaddrIn, SockaddrIn6, bind, listen, setsockopt,
    socket, sockopt,
};
use nix::unistd::{Gid, Group, getegid, geteuid, getgroups};

pub const TAILSCALE_INTERFACE: &str = "tailscale0";

pub fn validate_tailscale_address(value: &str) -> Result<IpAddr, String> {
    let address: IpAddr = value
        .parse()
        .map_err(|_| "listen address must be a literal Tailscale IP".to_owned())?;
    let allowed = match address {
        IpAddr::V4(ipv4) => {
            let value = u32::from(ipv4);
            value & 0xffc0_0000 == 0x6440_0000
        }
        IpAddr::V6(ipv6) => {
            let octets = ipv6.octets();
            octets[..6] == [0xfd, 0x7a, 0x11, 0x5c, 0xa1, 0xe0]
        }
    };
    if !allowed {
        return Err("listen address is outside the Tailscale ranges".to_owned());
    }
    Ok(address)
}

fn new_bound_socket(address: IpAddr, port: u16, listen_socket: bool) -> Result<OwnedFd, String> {
    let family = if address.is_ipv6() {
        AddressFamily::Inet6
    } else {
        AddressFamily::Inet
    };
    let socket_fd = socket(family, SockType::Stream, SockFlag::SOCK_CLOEXEC, None)
        .map_err(|error| format!("failed to create listener socket: {error}"))?;
    setsockopt(
        &socket_fd,
        sockopt::BindToDevice,
        &OsString::from(TAILSCALE_INTERFACE),
    )
    .map_err(|error| format!("failed to bind listener to {TAILSCALE_INTERFACE}: {error}"))?;
    if listen_socket {
        setsockopt(&socket_fd, sockopt::ReuseAddr, &true)
            .map_err(|error| format!("failed to configure listener: {error}"))?;
    }
    let socket_address = SocketAddr::new(address, port);
    match socket_address {
        SocketAddr::V4(ipv4) => bind(socket_fd.as_raw_fd(), &SockaddrIn::from(ipv4)),
        SocketAddr::V6(ipv6) => bind(socket_fd.as_raw_fd(), &SockaddrIn6::from(ipv6)),
    }
    .map_err(|_| format!("listen address is not assigned to {TAILSCALE_INTERFACE}"))?;
    Ok(socket_fd)
}

pub fn validate_tailscale_interface_address(value: &str) -> Result<IpAddr, String> {
    let address = validate_tailscale_address(value)?;
    let _probe = new_bound_socket(address, 0, false)?;
    Ok(address)
}

pub fn bind_tailscale_listener(address: IpAddr, port: u16) -> Result<TcpListener, String> {
    if port == 0 {
        return Err("port must be from 1 to 65535".to_owned());
    }
    let socket_fd = new_bound_socket(address, port, true)?;
    listen(
        &socket_fd,
        Backlog::new(16).map_err(|error| format!("invalid listener backlog: {error}"))?,
    )
    .map_err(|error| format!("failed to listen: {error}"))?;
    Ok(TcpListener::from(socket_fd))
}

pub fn validate_process_identity() -> Result<(), String> {
    let uid = geteuid();
    let control_gid = Group::from_name("moos-control")
        .map_err(|_| "moos-control group lookup failed".to_owned())?
        .ok_or_else(|| "moos-control group is unavailable".to_owned())?
        .gid;
    let supplementary: BTreeSet<u32> = getgroups()
        .map_err(|_| "gateway supplementary group lookup failed".to_owned())?
        .into_iter()
        .map(Gid::as_raw)
        .collect();
    validate_identity_policy(
        uid.as_raw(),
        getegid().as_raw(),
        control_gid.as_raw(),
        &supplementary,
    )
}

pub fn validate_identity_policy(
    effective_uid: u32,
    primary_gid: u32,
    control_gid: u32,
    supplementary: &BTreeSet<u32>,
) -> Result<(), String> {
    if effective_uid == 0 {
        return Err("gateway service must not run as root".to_owned());
    }
    validate_group_policy(primary_gid, control_gid, supplementary)
}

pub fn validate_group_policy(
    primary_gid: u32,
    control_gid: u32,
    supplementary: &BTreeSet<u32>,
) -> Result<(), String> {
    if !supplementary.contains(&control_gid)
        || supplementary
            .iter()
            .any(|group| *group != primary_gid && *group != control_gid)
    {
        return Err("gateway process has unexpected supplementary groups".to_owned());
    }
    Ok(())
}

pub fn io_error(error: String) -> io::Error {
    io::Error::other(error)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_literal_tailscale_ranges() {
        for address in ["100.64.0.0", "100.127.255.255", "fd7a:115c:a1e0::1"] {
            assert!(validate_tailscale_address(address).is_ok());
        }
        for address in ["0.0.0.0", "127.0.0.1", "192.168.1.4", "tailscale0"] {
            assert!(validate_tailscale_address(address).is_err());
        }
    }

    #[test]
    fn group_policy_requires_only_primary_and_control() {
        assert!(validate_group_policy(100, 200, &BTreeSet::from([200])).is_ok());
        assert!(validate_group_policy(100, 200, &BTreeSet::from([100, 200])).is_ok());
        assert!(validate_group_policy(100, 200, &BTreeSet::from([100])).is_err());
        assert!(validate_group_policy(100, 200, &BTreeSet::from([200, 300])).is_err());
        assert!(validate_identity_policy(0, 100, 200, &BTreeSet::from([200])).is_err());
    }
}
