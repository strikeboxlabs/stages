#!/usr/bin/env bash
# Stage 1: base OS settings and root filesystem expansion.
# Missing dependencies are installed automatically using apt-get.
set -Eeuo pipefail
current_step='startup'
trap 'rc=$?; printf "Error [stage1: %s]: line %s failed (exit %s): %s\nResolve the error above, then rerun stage1.sh.\n" "$current_step" "$LINENO" "$rc" "$BASH_COMMAND" >&2; exit "$rc"' ERR
step() { current_step=$1; printf '\n[stage1] %s\n' "$current_step"; }

usage() {
    cat <<'EOF'
Usage: sudo ./stage1.sh [--dry-run]

Detects the root partition, checks disk capacity, and grows the partition
and filesystem into all contiguous space following it. Supports ext2/3/4
and XFS on a plain partition. LVM, encryption, and RAID are not supported.
Back up important data before changing a partition table.

Sets the persistent system keyboard layout to Danish, installs kali-root-login,
and applies persistent inotify limits (512 instances, 524288 watches per user).
Works headlessly; also runs setxkbmap dk when an X display is available.

  --dry-run  Preview changes; skip disk writes and base configuration.
  --help     Show this help.

Missing dependencies are automatically installed using apt-get on Debian/Kali,
including in --dry-run mode. Package installation requires network access.
EOF
}

die() { printf 'Error [stage1: %s]: %s\n' "$current_step" "$*" >&2; exit 1; }
apt_updated=false
install_packages() {
    command -v apt-get >/dev/null || die "apt-get is unavailable; install these packages manually: $*"
    if ! "$apt_updated"; then
        apt-get update || die 'Could not refresh package lists. Check network access and apt sources, then rerun.'
        apt_updated=true
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@" || \
        die 'Package installation failed. Resolve the apt error and rerun this script.'
}

ensure_commands() {
    local tool package
    local -a missing=() packages=()
    while (( $# )); do
        tool=$1
        package=$2
        shift 2
        if ! command -v "$tool" >/dev/null; then
            missing+=("$tool")
            # Each package only needs to be requested once.
            if [[ " ${packages[*]} " != *" $package "* ]]; then
                packages+=("$package")
            fi
        fi
    done
    (( ${#packages[@]} )) || return 0
    command -v apt-get >/dev/null || die "Missing commands: ${missing[*]}. Install packages manually: ${packages[*]} (apt-get is unavailable)."
    printf 'Installing missing dependencies: %s\n' "${packages[*]}"
    install_packages "${packages[@]}"
    for tool in "${missing[@]}"; do
        command -v "$tool" >/dev/null || die "Dependency installation completed but $tool is still unavailable."
    done
}

dry_run=false
case "${1:-}" in
    --dry-run) dry_run=true ;;
    --help|-h) usage; exit 0 ;;
    '') ;;
    *) usage >&2; exit 2 ;;
esac
(( $# <= 1 )) || die 'Too many arguments.'
(( EUID == 0 )) || die 'Run this script with sudo.'
export PATH="$PATH:/usr/sbin:/sbin"
step 'Checking disk dependencies and root partition'
ensure_commands findmnt util-linux lsblk util-linux readlink coreutils \
    blockdev util-linux growpart cloud-guest-utils sfdisk fdisk \
    cat coreutils mktemp coreutils df coreutils

# Resolve the root filesystem by device number, avoiding aliases and bind paths.
root_id=$(findmnt -n -o MAJ:MIN --target /)
root_id=${root_id//[[:space:]]/}
[[ "$root_id" =~ ^[0-9]+:[0-9]+$ ]] || die "Invalid root device number: $root_id"
partition=$(readlink -f "/dev/block/$root_id")
[[ -b "$partition" ]] || die "Root device is not accessible: $partition"
[[ $(lsblk -dn -o TYPE "$partition") == part ]] || die 'Root must be on a plain partition (no LVM, encryption, or RAID).'
parent=$(lsblk -dn -o PKNAME "$partition")
[[ -n "$parent" && "$parent" != *$'\n'* ]] || die 'Could not determine a single parent disk.'
disk="/dev/$parent"
[[ $(lsblk -dn -o TYPE "$disk") == disk ]] || die 'The parent device is not a plain disk.'
partition_name=${partition##*/}
[[ -r "/sys/class/block/$partition_name/partition" ]] || die 'Cannot determine partition number.'
number=$(cat "/sys/class/block/$partition_name/partition")
fstype=$(findmnt -n -o FSTYPE --target /)
case "$fstype" in
    ext2|ext3|ext4) ensure_commands resize2fs e2fsprogs ;;
    xfs) ensure_commands xfs_growfs xfsprogs ;;
    *) die "Unsupported root filesystem: $fstype" ;;
esac
mount_options=$(findmnt -n -o OPTIONS --target /)
[[ ",$mount_options," == *,rw,* ]] || die 'The root filesystem is not mounted read/write.'

disk_bytes=$(blockdev --getsize64 "$disk")
before_bytes=$(blockdev --getsize64 "$partition")
printf 'Disk:       %s (%s bytes)\nPartition:  %s (%s bytes)\nFilesystem: %s\n' \
    "$disk" "$disk_bytes" "$partition" "$before_bytes" "$fstype"
printf '\nChecking available contiguous space with growpart...\n'
# growpart returns 1 when no growth is possible, and 2 on error.
status=0
proposal=$(growpart --dry-run --fudge 0 "$disk" "$number" 2>&1) || status=$?
printf '%s\n' "$proposal"
case "$status" in
    0) can_grow=true ;;
    1)
        [[ "$proposal" == *NOCHANGE:* ]] || die 'Partition check failed.'
        can_grow=false
        ;;
    *) die 'Partition check failed; no changes made.' ;;
esac
if "$dry_run"; then
    printf '\nDry run complete. A normal run also expands the filesystem to fill the partition.\n'
    printf 'Base configuration: persist Danish keyboard layout; setxkbmap dk when graphical;\n'
    printf 'install kali-root-login; persist and apply\n'
    printf 'fs.inotify.max_user_instances=512 and fs.inotify.max_user_watches=524288.\n'
    exit 0
fi

step 'Expanding the root partition and filesystem'
if "$can_grow"; then
    backup_dir=$(mktemp -d /var/tmp/expand-disk.XXXXXXXX)
    printf 'Saving partition-table layout in %s\n' "$backup_dir"
    sfdisk --dump "$disk" > "$backup_dir/partition-table.sfdisk"
    growpart --fudge 0 --update on "$disk" "$number" || \
        die 'Partition growth or kernel update failed. Inspect the partition table before retrying; a reboot may be needed.'
    after_bytes=$(blockdev --getsize64 "$partition")
    (( after_bytes > before_bytes )) || \
        die 'The kernel has not exposed the larger partition. Reboot, then rerun this script.'
else
    printf '\nNo contiguous space is available after this partition.\n'
fi

# Always resize: a previous run might have grown only the partition.
printf '\nExpanding the filesystem...\n'
case "$fstype" in
    ext2|ext3|ext4) resize2fs "$partition" ;;
    xfs) xfs_growfs -d / ;;
esac
printf '\nCurrent disk and filesystem sizes:\n'
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS "$disk"
df -h /

step 'Configuring the persistent Danish keyboard layout'
ensure_commands sysctl procps runuser util-linux install coreutils
install_packages keyboard-configuration console-setup
keyboard_file=/etc/default/keyboard
if [[ -f "$keyboard_file" && ! -e "$keyboard_file.stage1.bak" ]]; then
    cp -p "$keyboard_file" "$keyboard_file.stage1.bak"
fi
# Keep the keyboard model and unrelated options; replace layout and variant.
[[ -e "$keyboard_file" ]] || install -m 0644 /dev/null "$keyboard_file"
sed -i -E '/^# Danish layout managed by stage1\.sh\.$/d; /^[[:space:]]*(export[[:space:]]+)?(XKBLAYOUT|XKBVARIANT)[[:space:]]*=/d' "$keyboard_file"
printf '# Danish layout managed by stage1.sh.\nXKBLAYOUT="dk"\nXKBVARIANT=""\n' >> "$keyboard_file"
# Refresh the boot-time console cache even when running through SSH.
setupcon --keyboard-only --save-only
if command -v update-initramfs >/dev/null; then
    update-initramfs -u
fi
case "$(tty 2>/dev/null || true)" in
    /dev/tty[0-9]*) setupcon --keyboard-only ;;
esac
printf 'Danish system keyboard saved; reboot to apply on all local consoles.\n'
if [[ -n "${DISPLAY:-}" ]]; then
    ensure_commands setxkbmap x11-xkb-utils
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
        if ! runuser -u "$SUDO_USER" -- setxkbmap dk; then
            printf 'Warning: current X session was not updated. Run setxkbmap dk in that session.\n' >&2
        fi
    elif ! setxkbmap dk; then
        printf 'Warning: current X session was not updated. Run setxkbmap dk in that session.\n' >&2
    fi
else
    printf 'No X display detected; current-session keyboard update skipped.\n'
fi

step 'Installing kali-root-login'
install_packages kali-root-login

step 'Applying persistent inotify limits'
install -d -m 0755 /etc/sysctl.d
sysctl_file=/etc/sysctl.d/99-stage1-inotify.conf
cat > "$sysctl_file" <<'EOF'
# Managed by stage1.sh (stage 1).
fs.inotify.max_user_instances = 512
fs.inotify.max_user_watches = 524288
EOF
chmod 0644 "$sysctl_file"
sysctl -p "$sysctl_file"
printf '\nStage 1 complete. Inotify limits are applied and saved in %s.\n' "$sysctl_file"
