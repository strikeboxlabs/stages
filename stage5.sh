#!/usr/bin/env bash
# Stage 5: install Strikebox Board's server and local Pi dispatcher under systemd.
set -Eeuo pipefail
current_step=startup
trap 'rc=$?; printf "Error [stage5: %s]: line %s failed (exit %s).\nResolve the error above, then rerun stage5.sh.\n" "$current_step" "$LINENO" "$rc" >&2; exit "$rc"' ERR
step() { current_step=$1; printf '\n[stage5] %s\n' "$current_step"; }
die() { printf 'Error [stage5: %s]: %s\n' "$current_step" "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sudo ./stage5.sh [--user USER] [--host ADDRESS] [--port PORT] [--dry-run]

Clone https://github.com/strikeboxlabs/board.git into /opt/board and install
its locked Python dependencies. Enable the server and Pi dispatcher at boot.
Requires Debian/Kali, Python 3.12+, systemd, and internet access.
Run stages 2–4 first to install Pi and configure its model and credentials.

  --user USER     Run the dispatcher as this stage-4 account (defaults to
                  the sudo-invoking user, otherwise the current account).
  --host ADDRESS  IPv4 address to listen on (default: 0.0.0.0, all interfaces).
                  Use 127.0.0.1 for local access only.
  --port PORT     TCP port (default: 8765).
  --dry-run       Describe actions without changing anything.
  --help          Show this help.

The API runs as board; its database stays in /var/lib/board/.local/share/board.
The dispatcher runs as --user and uses that account's existing Pi configuration.
Creates /opt/board/dispatch.yaml if absent, with 5 total worker slots, 2 projects,
3 workers per project, and up to 3 concurrent Pi sessions. Runs are stored in
/data/cairn-runs. Edit dispatch.yaml to customize these settings.
Reruns preserve dispatch.yaml, the checkout, credentials and data, reinstall
locked dependencies, and replace/restart both managed services. Existing configs
must point to the API address selected by --host/--port.
Starting the dispatcher also starts work on existing active projects.
Allows the configured TCP port through local input firewall rules on each
service start. After manually reloading a firewall, restart board.service.
EOF
}
bind_host=0.0.0.0
port=8765
target_user=${SUDO_USER:-$(id -un)}
dry_run=false
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=true; shift ;;
        --host|--port|--user)
            (( $# >= 2 )) || die "$1 requires a value."
            case "$1" in
                --host) bind_host=$2 ;;
                --port) port=$2 ;;
                --user) target_user=$2 ;;
            esac
            shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$target_user" && "$target_user" != -* ]] || die 'Select a valid account with --user USER.'
[[ "$port" =~ ^[0-9]{1,5}$ ]] || die 'Port must be an integer between 1 and 65535.'
port=$((10#$port))
(( port >= 1 && port <= 65535 )) || die 'Port must be between 1 and 65535.'
[[ "$bind_host" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || die '--host must be an IPv4 address.'
IFS=. read -r -a octets <<< "$bind_host"
for octet in "${octets[@]}"; do
    (( 10#$octet <= 255 )) || die 'Invalid IPv4 address.'
done
if "$dry_run"; then
    printf '%s\n' \
        'Would install git, Python venv support, curl, CA certificates, and uv.' \
        'Would create the board service account and clone into /opt/board.' \
        'Would install dependencies from board/uv.lock and retain data in /var/lib/board.' \
        "Would enable/start board.service on $bind_host:$port and check /projects." \
        "Would allow TCP $port through local IPv4 input firewall rules on every service start." \
        "Would run the local dispatcher as $target_user using that account's existing Pi configuration." \
        'Would create /opt/board/dispatch.yaml only if absent (5 worker slots, 2 projects, 3 concurrent Pi sessions).' \
        'Would prepare the configured runs directory (default /data/cairn-runs) for shared API/dispatcher access.' \
        'Would check the worker CLI and enable/start board-dispatcher.service after the API is healthy.' \
        'Would preserve existing dispatch.yaml, code, credentials and data on reruns.'
    exit 0
fi
(( EUID == 0 )) || die 'Run this script with sudo.'
export PATH="/usr/local/bin:$PATH:/usr/sbin:/sbin"
[[ -d /run/systemd/system ]] || die 'A running systemd system is required.'
command -v pi >/dev/null || die 'Pi is not installed. Run stage3.sh first.'
id "$target_user" >/dev/null 2>&1 || die "Unknown account: $target_user"
target_home=$(getent passwd "$target_user" | cut -d: -f6)
[[ "$target_home" =~ ^/[a-zA-Z0-9_./-]+$ && -d "$target_home" ]] || \
    die 'The dispatcher account needs an existing home with a simple absolute path.'
as_worker() {
    runuser -u "$target_user" -g "$board_group" -- env -u PI_CODING_AGENT_DIR \
        HOME="$target_home" \
        XDG_CONFIG_HOME="$target_home/.config" \
        XDG_CACHE_HOME="$target_home/.cache" \
        XDG_DATA_HOME="$target_home/.local/share" \
        XDG_STATE_HOME="$target_home/.local/state" \
        PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
        "$@"
}
health_host=$bind_host
[[ "$health_host" != 0.0.0.0 ]] || health_host=127.0.0.1

step 'Installing system dependencies'
apt-get update || die 'Could not refresh apt indexes. Check network access and apt sources.'
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git python3 python3-venv ca-certificates curl nftables iptables || die 'System dependency installation failed.'
/usr/bin/python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)' || \
    die 'Board requires Python 3.12 or newer.'

step 'Preparing the service account and checkout'
if ! id board >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/board --shell /usr/sbin/nologin board
fi
[[ $(getent passwd board | cut -d: -f6) == /var/lib/board ]] || \
    die 'An existing board account has a different home directory; refusing to repurpose it.'
(( $(id -u board) != 0 )) || die 'The board service account must not be root.'
board_group=$(id -gn board)
install -d -o board -g "$board_group" -m 0750 /var/lib/board
repo=https://github.com/strikeboxlabs/board.git
# runuser can retain the invoking account's XDG and uv paths. Keep all
# per-user configuration and caches in the service account's home.
as_board() {
    runuser -u board -- env \
        HOME=/var/lib/board \
        XDG_CONFIG_HOME=/var/lib/board/.config \
        XDG_CACHE_HOME=/var/lib/board/.cache \
        XDG_DATA_HOME=/var/lib/board/.local/share \
        XDG_STATE_HOME=/var/lib/board/.local/state \
        UV_CACHE_DIR=/var/lib/board/.cache/uv \
        PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
        "$@"
}
if [[ ! -e /opt/board ]]; then
    install -d -o board -g "$board_group" -m 0755 /opt/board
    as_board git clone "$repo" /opt/board
elif [[ -d /opt/board/.git ]]; then
    [[ $(stat -c %U /opt/board) == board ]] || die '/opt/board belongs to another user; refusing to change ownership.'
    origin=$(as_board git -C /opt/board remote get-url origin)
    [[ "${origin%.git}" == "${repo%.git}" ]] || die '/opt/board has a different Git origin.'
    printf 'Reusing existing checkout without pulling or discarding changes.\n'
elif [[ -d /opt/board && -z $(ls -A /opt/board) && $(stat -c %U /opt/board) == board ]]; then
    as_board git clone "$repo" /opt/board
else
    die '/opt/board already exists and is not a managed Board checkout.'
fi
[[ -f /opt/board/board/uv.lock && -f /opt/board/board/pyproject.toml ]] || \
    die 'Repository layout changed: expected board/pyproject.toml and board/uv.lock.'

step 'Installing locked application dependencies'
if [[ ! -x /opt/board-tools/bin/python ]]; then
    /usr/bin/python3 -m venv /opt/board-tools
fi
/opt/board-tools/bin/python -m pip install --disable-pip-version-check 'uv>=0.8.9,<1'
unit=/etc/systemd/system/board.service
dispatcher_unit=/etc/systemd/system/board-dispatcher.service
dispatch_config=/opt/board/dispatch.yaml
for managed_unit in "$unit" "$dispatcher_unit"; do
    service=${managed_unit##*/}
    existing_unit=$(systemctl show "$service" --property=FragmentPath --value)
    if [[ -n "$existing_unit" && "$existing_unit" != "$managed_unit" ]]; then
        die "An existing $service is managed elsewhere ($existing_unit); refusing to replace it."
    fi
    if [[ -e "$managed_unit" ]] && ! grep -q '^# Managed by stage5.sh$' "$managed_unit"; then
        die "An unmanaged $service already exists; refusing to overwrite it."
    fi
done
# Stop workers first, then the API, before modifying their shared environment.
for service in board-dispatcher.service board.service; do
    if systemctl is-active --quiet "$service"; then systemctl stop "$service"; fi
done
as_board /opt/board-tools/bin/uv sync --frozen --no-dev \
    --project /opt/board/board --python /usr/bin/python3 || \
    die 'Application dependency installation failed. Resolve the error and rerun; any previous managed instance is stopped.'
as_board /opt/board/board/.venv/bin/board serve --help >/dev/null

step 'Configuring the local dispatcher'
if [[ ! -e "$dispatch_config" ]]; then
cat > "$dispatch_config" <<EOF
# Local workers reuse the selected user's installed CLIs and credentials.
# Edit this file freely; stage5.sh preserves it on reruns.
server: "http://$health_host:$port"

runtime:
  execution: local
  worker_healthcheck: disabled
  max_workers: 5
  max_running_projects: 2
  max_project_workers: 3
  interval: 3
  healthcheck_timeout: 15
  prompt_group: "default"

tasks:
  bootstrap:
    timeout: 120
    conclude_timeout: 30
  reason:
    timeout: 240
  explore:
    timeout: 600
    conclude_timeout: 120

local:
  evidence_root: "/data/cairn-runs"
  completed_action: keep

# Optional environment overrides for local workers, e.g. proxies:
# common_env:
#   https_proxy: "http://127.0.0.1:7897"
#   http_proxy: "http://127.0.0.1:7897"
#   all_proxy: "http://127.0.0.1:7897"

workers:
  - name: "local-pi"
    type: "pi"
    task_types: [bootstrap, reason, explore]
    max_running: 3
    priority: 0

  # - name: "local-codex"
  #   type: "codex"
  #   task_types: [explore]
  #   max_running: 1
  #   priority: 1
EOF
    chown "$target_user:$board_group" "$dispatch_config"
    chmod 0640 "$dispatch_config"
else
    printf 'Preserving existing %s.\n' "$dispatch_config"
fi
# Read the same configuration used by the dispatcher so the API serves evidence
# from the correct directory, including when an existing config is reused.
evidence_root=$(as_worker /opt/board/board/.venv/bin/python - "$dispatch_config" "http://$health_host:$port" <<'PY'
import re
import sys
from pathlib import Path
from board.dispatcher.config import DispatchConfig

config = DispatchConfig.load(Path(sys.argv[1]))
if config.runtime.execution != 'local':
    sys.exit('stage5.sh requires runtime.execution: local in dispatch.yaml')
if config.server.rstrip('/') != sys.argv[2]:
    sys.exit('dispatch.yaml server differs from --host/--port; align them before rerunning.')
root = config.local.evidence_root or config.local.workspace_root
if not root or not re.fullmatch(r'/[a-zA-Z0-9_./-]+', root):
    sys.exit('Set local.evidence_root to a simple absolute path in dispatch.yaml.')
print(root)
PY
)
# The shared group and setgid directory let both service accounts access new
# evidence/reports. Existing run contents are not recursively changed.
install -d -o "$target_user" -g "$board_group" -m 2770 "$evidence_root"
as_board test -r "$evidence_root"
as_worker test -w "$evidence_root"
as_worker /opt/board/board/.venv/bin/board dispatch --config "$dispatch_config" --startup-healthcheck-only

step 'Installing the persistent Board port allowance'
install -d -m 0755 /usr/local/libexec
firewall_helper=/usr/local/libexec/board-firewall
if [[ -e "$firewall_helper" ]] && ! grep -q '^# Managed by stage5.sh$' "$firewall_helper"; then
    die 'An unmanaged board-firewall helper already exists; refusing to overwrite it.'
fi
cat > "$firewall_helper" <<'PY'
#!/usr/bin/python3
# Managed by stage5.sh
import json
import shlex
import subprocess
import sys


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


port = int(sys.argv[1])
if not 1 <= port <= 65535:
    raise SystemExit('Invalid Board port')
tag = 'stage5-board-port'
ruleset = json.loads(run('/usr/sbin/nft', '-j', '-a', 'list', 'ruleset',
                         capture_output=True).stdout)['nftables']
commands = []
# Accept in every IPv4 input base chain: an accept in one base chain does
# not bypass a later base chain's drop policy. Never flush unrelated rules.
for entry in ruleset:
    rule = entry.get('rule', {})
    if rule.get('comment') == tag and rule.get('family') in ('ip', 'inet'):
        commands.append({'delete': {'rule': {key: rule[key] for key in
                         ('family', 'table', 'chain', 'handle')}}})
for entry in ruleset:
    chain = entry.get('chain', {})
    if chain.get('hook') != 'input' or chain.get('family') not in ('ip', 'inet'):
        continue
    rule = {key: chain[key] for key in ('family', 'table')}
    rule.update(chain=chain['name'], comment=tag, expr=[
        {'match': {'op': '==', 'left': {'meta': {'key': 'nfproto'}}, 'right': 'ipv4'}},
        {'match': {'op': '==', 'left': {'payload': {'protocol': 'tcp', 'field': 'dport'}},
                   'right': port}},
        {'accept': None},
    ])
    commands.append({'insert': {'rule': rule}})
if commands:
    run('/usr/sbin/nft', '-j', '-f', '-', input=json.dumps({'nftables': commands}))

# Legacy iptables can coexist with nftables. Remove only our own earlier
# allowance (including a previous port), then insert the current allowance.
legacy = '/usr/sbin/iptables-legacy'
for line in run(legacy, '-w', '-S', 'INPUT', capture_output=True).stdout.splitlines():
    args = shlex.split(line)
    if '--comment' in args and args[args.index('--comment') + 1] == tag:
        run(legacy, '-w', '-D', *args[1:])
run(legacy, '-w', '-I', 'INPUT', '1', '-p', 'tcp', '--dport', str(port),
    '-m', 'comment', '--comment', tag, '-j', 'ACCEPT')
print(f'[stage5] Local IPv4 input firewall allows Board TCP port {port}.')
PY
chmod 0755 "$firewall_helper"

step 'Installing the reboot-persistent services'
if [[ -f "$unit" ]]; then cp -p "$unit" "$unit.backup"; fi
cat > "$unit" <<EOF
# Managed by stage5.sh
[Unit]
Description=Strikebox Board web and API server
Wants=network-online.target
After=network-online.target nftables.service ufw.service firewalld.service netfilter-persistent.service

[Service]
Type=simple
User=board
Group=$board_group
WorkingDirectory=/opt/board/board
Environment=HOME=/var/lib/board
Environment=XDG_CONFIG_HOME=/var/lib/board/.config
Environment=XDG_CACHE_HOME=/var/lib/board/.cache
Environment=XDG_DATA_HOME=/var/lib/board/.local/share
Environment=XDG_STATE_HOME=/var/lib/board/.local/state
Environment=UV_CACHE_DIR=/var/lib/board/.cache/uv
Environment=PYTHONUNBUFFERED=1
Environment=BOARD_PROJECTS_DIR=$evidence_root
ExecStartPre=+/usr/local/libexec/board-firewall $port
ExecStart=/opt/board/board/.venv/bin/board serve --host $bind_host --port $port --no-access-log
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0007
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$unit"
if [[ -f "$dispatcher_unit" ]]; then cp -p "$dispatcher_unit" "$dispatcher_unit.backup"; fi
cat > "$dispatcher_unit" <<EOF
# Managed by stage5.sh
[Unit]
Description=Strikebox Board local Pi worker dispatcher
Wants=network-online.target board.service
After=network-online.target board.service
PartOf=board.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=$target_user
Group=$board_group
WorkingDirectory=/opt/board
Environment=HOME=$target_home
Environment=XDG_CONFIG_HOME=$target_home/.config
Environment=XDG_CACHE_HOME=$target_home/.cache
Environment=XDG_DATA_HOME=$target_home/.local/share
Environment=XDG_STATE_HOME=$target_home/.local/state
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
Environment=PYTHONUNBUFFERED=1
Environment=BOARD_PROJECTS_DIR=$evidence_root
ExecStartPre=/usr/bin/curl --noproxy * --fail --silent --show-error --output /dev/null --max-time 2 --retry 30 --retry-connrefused --retry-delay 1 --retry-max-time 60 http://$health_host:$port/projects
ExecStart=/opt/board/board/.venv/bin/board dispatch --config /opt/board/dispatch.yaml
Restart=on-failure
RestartSec=5
TimeoutStartSec=90
TimeoutStopSec=30
KillMode=control-group
UMask=0007
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target board.service
EOF
chmod 0644 "$dispatcher_unit"
systemd-analyze verify "$unit" "$dispatcher_unit"
systemctl daemon-reload
systemctl enable board.service board-dispatcher.service
systemctl restart board.service

step 'Checking service health'
healthy=false
for (( attempt=0; attempt<30; attempt++ )); do
    if systemctl is-active --quiet board.service && \
       curl --noproxy '*' -fsS --max-time 2 "http://$health_host:$port/projects" >/dev/null 2>&1; then
        healthy=true
        break
    fi
    sleep 1
done
if ! "$healthy"; then
    systemctl status board.service --no-pager >&2 || true
    die 'Board did not become healthy. Inspect: sudo journalctl -u board.service -n 80 --no-pager'
fi
systemctl is-enabled --quiet board.service || die 'Board is running but is not enabled at boot.'
# start is idempotent if board.service already pulled in the dispatcher.
systemctl start board-dispatcher.service
sleep 3
if ! systemctl is-active --quiet board-dispatcher.service; then
    systemctl status board-dispatcher.service --no-pager >&2 || true
    die 'Dispatcher failed to start. Inspect: sudo journalctl -u board-dispatcher.service -n 80 --no-pager'
fi
systemctl is-enabled --quiet board-dispatcher.service || die 'Dispatcher is not enabled at boot.'
printf '\n[stage5] Board server and Pi dispatcher are running and enabled at boot: http://%s:%s\n' "$health_host" "$port"
printf 'Status: sudo systemctl status board board-dispatcher\nLogs: sudo journalctl -u board -u board-dispatcher -f\n'
if [[ "$bind_host" == 0.0.0.0 ]]; then
    printf 'Network access: http://<Kali-IP>:%s (local access: http://127.0.0.1:%s)\n' "$port" "$port"
    printf 'Kali network addresses: '
    hostname -I
fi
if [[ "$bind_host" == 127.0.0.1 ]]; then
    printf 'From your workstation: ssh -N -L %s:127.0.0.1:%s kali@<Kali-IP>\n' "$port" "$port"
    printf 'Then open http://127.0.0.1:%s in your browser.\n' "$port"
fi
