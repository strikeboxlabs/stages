#!/usr/bin/env bash
# Optional, composable tool and skill provisioning. Stage 5 never invokes this.
set -Eeuo pipefail
die() { printf 'Error [install-tools]: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'HELP'
Usage: sudo ./install-tools.sh [--groups research,sast] [--user USER] [--sast-repo PATH] [--dry-run]

Provision tools, independent skill packages and native Board catalogs separately
from stage5.sh. Default groups: research,sast (Kali). On plain Debian/Ubuntu select
--groups sast. The research group includes SecLists wordlists and requires Kali
and its APT package indexes.

  --groups LIST     Comma-separated research and/or sast; default both.
  --user USER       Actual Board dispatcher/Hermes/Pi account (sudo user by default).
  --sast-repo PATH  Use this existing SAST tree instead of the managed /opt/sast clone.
  --dry-run         Read-only plan; no clone, install, links or config writes.
  --help            Show help.

Default checkouts are cloned once from public strikeboxlabs repositories; existing
revisions/edits are preserved without pulling. Explicit SAST trees need no Git metadata.
An active dispatcher is stopped during provisioning and restored on exit, including
installation failures and interrupts. An inactive dispatcher stays inactive.
Provisioning never executes scanners. Provider authentication and stage5 remain independent.
HELP
}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
groups=research,sast
target_user=${SUDO_USER:-$(id -un)}
sast_repo=/opt/sast
selected_sast=false
dry_run=false
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=true; shift ;;
        --groups|--user|--sast-repo|--repo)
            (( $# >= 2 )) || die "$1 requires a value."
            case "$1" in
                --groups) groups=$2 ;;
                --user) target_user=$2 ;;
                --sast-repo|--repo) sast_repo=$2; selected_sast=true ;;
            esac
            shift 2 ;;
        *) die "Unknown option: $1" ;;
    esac
done
[[ -n "$groups" && "$groups" != ,* && "$groups" != *, && "$groups" != *,,* ]] || die 'Select --groups research, sast, or research,sast.'
research=false; sast=false
IFS=, read -r -a requested <<< "$groups"
for group in "${requested[@]}"; do
    case "$group" in research) research=true ;; sast) sast=true ;; *) die "Unknown tool group: $group" ;; esac
done
if "$selected_sast" && ! "$sast"; then die '--sast-repo requires the sast group.'; fi
[[ "$target_user" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*\$?$ ]] || die 'Invalid account name.'
id "$target_user" >/dev/null 2>&1 || die "Unknown account: $target_user"
[[ -f /etc/os-release && -f /etc/debian_version ]] || die 'This installer requires Debian/Kali.'
if "$research"; then
    os_id=$( . /etc/os-release; printf '%s' "${ID:-}" )
    [[ "$os_id" == kali ]] || die 'The research group requires Kali package indexes. On Debian/Ubuntu use --groups sast; no changes made.'
fi
if ! "$dry_run"; then
    (( EUID == 0 )) || die 'Run as root or with sudo.'
fi
for helper in configure-sast-checkout.py configure-research-skills.py configure-sast-skills.py configure-board-skills.py board_config.py; do
    [[ -f "$script_dir/$helper" ]] || die "Missing helper: $helper"
done
if "$research"; then [[ -f "$script_dir/lib/install-research.sh" ]] || die 'Missing internal research module.'; fi
if "$sast"; then [[ -f "$script_dir/lib/install-sast.sh" ]] || die 'Missing internal SAST module.'; fi
[[ -f "$script_dir/lib/dispatcher-maintenance.sh" ]] || die 'Missing dispatcher maintenance helper.'
if [[ -f /opt/board/board/src/board/dispatcher/workers/adapters/pi.py ]]; then
    [[ -x /opt/board/board/.venv/bin/python ]] || die 'Run stage5.sh after updating Board to native skill profiles.'
    /opt/board/board/.venv/bin/python "$script_dir/configure-board-skills.py" --check-native
    if [[ -d /run/systemd/system ]]; then
        service_path=$(systemctl show board-dispatcher.service -p FragmentPath --value)
        if [[ -n "$service_path" ]]; then
            dispatcher_user=$(systemctl show board-dispatcher.service -p User --value)
            dispatcher_user=${dispatcher_user:-root}
            [[ $(id -u "$dispatcher_user") == $(id -u "$target_user") ]] || die "Board uses $dispatcher_user; select that --user."
        fi
    fi
fi
sast_arguments=(--checkout "$sast_repo")
"$selected_sast" && sast_arguments+=(--existing)
if "$sast"; then
    python3 "$script_dir/configure-sast-checkout.py" "${sast_arguments[@]}"
fi
if "$dry_run"; then
    printf 'Would temporarily stop an active Board dispatcher and restore it on exit; an inactive dispatcher stays inactive.\n'
    if "$research"; then bash "$script_dir/lib/install-research.sh" --user "$target_user" --dry-run; fi
    if "$sast"; then
        if [[ -f "$sast_repo/catalog.json" ]]; then
            bash "$script_dir/lib/install-sast.sh" --user "$target_user" --repo "$sast_repo" --dry-run
        else
            printf 'Would clone /opt/sast, preflight its catalog, install seven SAST CLIs, link skills and register its native Board catalog.\n'
        fi
    fi
    exit 0
fi
# Serialize provisioning so another invocation cannot install while we restore.
command -v flock >/dev/null || die 'Missing flock; install util-linux first.'
exec 9>/run/lock/stages-install-tools.lock
flock -n 9 || die 'Another tool installation is running.'
# shellcheck source=lib/dispatcher-maintenance.sh
source "$script_dir/lib/dispatcher-maintenance.sh"
pause_dispatcher || die 'Could not stop Board dispatcher; provisioning aborted.'
# Prepare absent SAST source before any package installation; never pull existing trees.
if "$sast"; then python3 "$script_dir/configure-sast-checkout.py" "${sast_arguments[@]}" --apply; fi
# Read-only group preflights happen before either group installs packages.
if "$research"; then bash "$script_dir/lib/install-research.sh" --user "$target_user" --dry-run; fi
if "$sast"; then bash "$script_dir/lib/install-sast.sh" --user "$target_user" --repo "$sast_repo" --dry-run; fi
if "$research"; then bash "$script_dir/lib/install-research.sh" --user "$target_user"; fi
if "$sast"; then bash "$script_dir/lib/install-sast.sh" --user "$target_user" --repo "$sast_repo"; fi
printf 'Selected tools and skills provisioned.\n'
