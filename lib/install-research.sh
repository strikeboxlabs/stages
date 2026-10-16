#!/usr/bin/env bash
# Optional research-library checkout and agent/Board skill integration.
set -Eeuo pipefail
trap 'rc=$?; printf "Error [install-offensive-tools]: line %s failed (exit %s).\n" "$LINENO" "$rc" >&2; exit "$rc"' ERR
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'HELP'
Usage: sudo ./install-tools.sh --groups research [--user USER] [--dry-run]

Clone/reuse strikeboxlabs/research-library at /opt/research-library and link its
skills into the selected account's Hermes and Pi directories. Honors HERMES_HOME.
Default account: sudo-invoking user, otherwise the current account.
Existing checkouts are validated and reused without pulling or discarding edits.
Conflicting link destinations and Board builds without native profiles are refused.

If Board is installed at /opt/board, register the installed catalog in dispatch.yaml
for curated skill-profile selection. Board owns its Pi launch arguments.
Run after stage5.sh with its dispatcher account to integrate Board. If Board is
absent, installs agent links only; rerun after Board is installed.
Finish active work and stop the dispatcher before provisioning. It is not restarted.

This extracts the research-library provisioning from stages 3 and 5. It installs
skills plus tools available as packages in the configured Kali repositories.
Tools without a resolvable Kali package are listed for later; no source builds
or alternate package managers are used. Requires Kali with APT Contents indexes.
Starting the dispatcher later can resume its existing active projects.
--dry-run performs validation only; it does not install prerequisites or write.
HELP
}
target_user=${SUDO_USER:-$(id -un)}
dry_run=false
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=true; shift ;;
        --user) (( $# >= 2 )) || die '--user requires an account.'; target_user=$2; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
[[ "$target_user" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*\$?$ ]] || die 'Invalid account name.'
id "$target_user" >/dev/null 2>&1 || die "Unknown account: $target_user"
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target_home=$(getent passwd "$target_user" | cut -d: -f6)
hermes_home=${HERMES_HOME:-$target_home/.hermes}
for helper in configure-research-skills.py configure-board-skills.py board_config.py kali-research-packages.py; do
    [[ -f "$script_dir/$helper" ]] || die "Missing $helper; keep helpers together."
done
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
if ! "$dry_run" && command -v systemctl >/dev/null && systemctl is-active --quiet board-dispatcher.service; then
    die 'Board dispatcher is active. Finish active work and stop board-dispatcher.service before provisioning; no changes made.'
fi
board_adapter=/opt/board/board/src/board/dispatcher/workers/adapters/pi.py
board_present=false
if [[ -e "$board_adapter" ]]; then
    board_present=true
    if ! "$dry_run" && [[ -d /run/systemd/system ]]; then
        dispatcher_user=$(systemctl show board-dispatcher.service -p User --value)
        [[ -z "$dispatcher_user" || "$dispatcher_user" == "$target_user" ]] || die "Board dispatcher uses $dispatcher_user; select --user $dispatcher_user."
    fi
    [[ -x /opt/board/board/.venv/bin/python ]] || die 'Board must be installed with native skill-profile support; run stage5.sh after updating Board.'
    /opt/board/board/.venv/bin/python "$script_dir/configure-board-skills.py" --board-root /opt/board --check-native
fi
python3 "$script_dir/configure-research-skills.py" --user "$target_user" --hermes-home "$hermes_home"
if "$dry_run"; then
    printf 'Would install git, Python and CA certificates, then clone/reuse /opt/research-library and link Hermes and Pi skills for %s.\n' "$target_user"
    if [[ -d /opt/research-library/skills ]]; then
        /usr/bin/python3 "$script_dir/kali-research-packages.py"
    else
        printf 'Kali package resolution will run after the research-library checkout exists.\n'
    fi
    if "$board_present"; then
        printf 'Would register /opt/research-library/catalog.json for native Board curated skill profiles.\n'
        printf 'Would verify the dispatcher account; active dispatchers must be stopped before provisioning.\n'
    else
        printf 'Board is absent; rerun this installer after stage5.sh for Board integration.\n'
    fi
    exit 0
fi
if ! "$dry_run"; then
    (( EUID == 0 )) || die 'Run as root or with sudo.'
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git python3 python3-apt apt-file ca-certificates util-linux
    apt-file update
fi
python3 "$script_dir/configure-research-skills.py" --user "$target_user" --hermes-home "$hermes_home" --apply
runuser -u "$target_user" -- test -r /opt/research-library/skills
printf '\nResolving tools against Kali package indexes...\n'
install -d -m 0755 /var/lib/stages
plan=$(mktemp /var/lib/stages/.offensive-packages.XXXXXXXX)
trap 'rm -f -- "$plan"' EXIT
/usr/bin/python3 "$script_dir/kali-research-packages.py" > "$plan"
chmod 0644 "$plan"
mv "$plan" /var/lib/stages/offensive-packages.json
trap - EXIT
mapfile -t packages < <(/usr/bin/python3 -c 'import json; print("\n".join(json.load(open("/var/lib/stages/offensive-packages.json"))["packages"]))')
(( ${#packages[@]} > 0 )) || die 'No Kali packages resolved; inspect APT repositories and Contents indexes.'
printf 'Installing %s deduplicated Kali packages. Plan: /var/lib/stages/offensive-packages.json\n' "${#packages[@]}"
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-remove --no-install-recommends "${packages[@]}"
for package in "${packages[@]}"; do
    [[ $(dpkg-query -W -f='${Status}' "$package") == 'install ok installed' ]] || die "Package verification failed: $package"
done
/usr/bin/python3 -c 'import json; p=json.load(open("/var/lib/stages/offensive-packages.json")); print("Deferred skill tools without a resolved Kali package:\n" + "\n".join(p["deferred"]))'
if "$board_present"; then
    dispatcher_group=$(id -gn "$target_user")
    if [[ -d /run/systemd/system ]]; then
        service_group=$(systemctl show board-dispatcher.service -p Group --value)
        [[ -z "$service_group" ]] || dispatcher_group=$service_group
    fi
    doctor_identity=(--user "$target_user" --group "$dispatcher_group")
    /opt/board/board/.venv/bin/python "$script_dir/configure-board-skills.py" --board-root /opt/board \
        "${doctor_identity[@]}" --apply
    printf 'Board catalog configured. Start board-dispatcher.service when ready; existing sessions were preserved.\n'
fi
printf '\nResearch-library skills and available Kali tool packages installed for %s.\n' "$target_user"
