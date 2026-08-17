# Dynamic login banner for MOOS Linux.
# This file is sourced by /etc/profile, so keep it POSIX-sh compatible.

[ -n "${PS1:-}" ] || return 0

kernel="$(uname -r 2>/dev/null || printf '?')"
ram="$(awk '/^MemTotal:/ {printf "%d MiB", $2 / 1024; exit}' /proc/meminfo 2>/dev/null)"
uptime="$(awk '{total=int($1); days=int(total / 86400); hours=int((total % 86400) / 3600); mins=int((total % 3600) / 60); printf "%dd %02dh %02dm", days, hours, mins}' /proc/uptime 2>/dev/null)"
ip="$(ip -4 addr show scope global 2>/dev/null | awk '/inet / {sub("/.*", "", $2); print $2; exit}')"
rootfs="$(df -h / 2>/dev/null | awk 'NR == 2 {print $3 " used / " $2; exit}')"

[ -n "$ram" ] || ram='?'
[ -n "$uptime" ] || uptime='?'
[ -n "$ip" ] || ip='offline'
[ -n "$rootfs" ] || rootfs='?'

green=''
dim=''
reset=''
if [ -t 1 ] && [ "${TERM:-dumb}" != 'dumb' ]; then
    green="$(printf '\033[32m')"
    dim="$(printf '\033[2m')"
    reset="$(printf '\033[0m')"
fi

printf '%s\n' "${green}███╗   ███╗ ██████╗  ██████╗ ███████╗${reset}"
printf '%s\n' "${green}████╗ ████║██╔═══██╗██╔═══██╗██╔════╝${reset}"
printf '%s\n' "${green}██╔████╔██║██║   ██║██║   ██║███████╗${reset}"
printf '%s\n' "${green}██║╚██╔╝██║██║   ██║██║   ██║╚════██║${reset}"
printf '%s\n' "${green}██║ ╚═╝ ██║╚██████╔╝╚██████╔╝███████║${reset}"
printf '%s\n' "${green}╚═╝     ╚═╝ ╚═════╝  ╚═════╝  ╚══════╝${reset}"
printf '%s\n\n' "        ${green}tiny. green. alive.${reset}"

printf '  %s%-8s%s %s\n' "$dim" 'kernel' "$reset" "$kernel"
printf '  %s%-8s%s %s\n' "$dim" 'ram' "$reset" "$ram"
printf '  %s%-8s%s %s\n' "$dim" 'uptime' "$reset" "$uptime"
printf '  %s%-8s%s %s\n' "$dim" 'ip' "$reset" "$ip"
printf '  %s%-8s%s %s\n\n' "$dim" 'rootfs' "$reset" "$rootfs"
