#!/usr/bin/env bash
# Stage 5: install Strikebox Board and run its web/API server under systemd.
set -Eeuo pipefail
current_step=startup
trap 'rc=$?; printf "Error [stage5: %s]: line %s failed (exit %s).\nResolve the error above, then rerun stage5.sh.\n" "$current_step" "$LINENO" "$rc" >&2; exit "$rc"' ERR
step() { current_step=$1; printf '\n[stage5] %s\n' "$current_step"; }
die() { printf 'Error [stage5: %s]: %s\n' "$current_step" "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sudo ./stage5.sh [--host ADDRESS] [--port PORT] [--dry-run]

Clone https://github.com/strikeboxlabs/board.git into /opt/board and install
its locked Python dependencies. Enable and start board.service at boot.
Requires Debian/Kali, Python 3.12+, systemd, and internet access.

  --host ADDRESS  IPv4 address to listen on (default: 0.0.0.0, all interfaces).
                  Use 127.0.0.1 for local access only.
  --port PORT     TCP port (default: 8765).
  --dry-run       Describe actions without changing anything.
  --help          Show this help.

Runs as a dedicated board system account. Data lives under /var/lib/board;
the database uses /var/lib/board/.local/share/board/board.db.
Reruns reuse the current checkout and data, reinstall locked dependencies,
and replace/restart the managed service. Existing checkouts are not updated.
This stage runs the Board server, not the separate agent worker dispatcher.
Allows the configured TCP port through local input firewall rules on each
service start. After manually reloading a firewall, restart board.service.
EOF
}
bind_host=0.0.0.0
port=8765
dry_run=false
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=true; shift ;;
        --host|--port)
            (( $# >= 2 )) || die "$1 requires a value."
            if [[ "$1" == --host ]]; then bind_host=$2; else port=$2; fi
            shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done
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
        'Would preserve existing code on reruns; no dispatcher would be started.'
    exit 0
fi
(( EUID == 0 )) || die 'Run this script with sudo.'
export PATH="/usr/local/bin:$PATH:/usr/sbin:/sbin"
[[ -d /run/systemd/system ]] || die 'A running systemd system is required.'

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
existing_unit=$(systemctl show board.service --property=FragmentPath --value)
if [[ -n "$existing_unit" && "$existing_unit" != "$unit" ]]; then
    die "An existing board.service is managed elsewhere ($existing_unit); refusing to replace it."
fi
if [[ -e "$unit" ]] && ! grep -q '^# Managed by stage5.sh$' "$unit"; then
    die 'An unmanaged board.service already exists; refusing to overwrite it.'
fi
# Stop an earlier managed instance before modifying its virtual environment.
if systemctl is-active --quiet board.service; then
    systemctl stop board.service
fi
as_board /opt/board-tools/bin/uv sync --frozen --no-dev \
    --project /opt/board/board --python /usr/bin/python3 || \
    die 'Application dependency installation failed. Resolve the error and rerun; any previous managed instance is stopped.'
as_board /opt/board/board/.venv/bin/board serve --help >/dev/null

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

step 'Installing the reboot-persistent service'
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
Environment=BOARD_PROJECTS_DIR=/var/lib/board/.local/share/board/projects
ExecStartPre=+/usr/local/libexec/board-firewall $port
ExecStart=/opt/board/board/.venv/bin/board serve --host $bind_host --port $port --no-access-log
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0027
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$unit"
systemd-analyze verify "$unit"
systemctl daemon-reload
systemctl enable board.service
systemctl restart board.service

step 'Checking service health'
health_host=$bind_host
[[ "$health_host" != 0.0.0.0 ]] || health_host=127.0.0.1
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
printf '\n[stage5] Board is running and enabled at boot: http://%s:%s\n' "$health_host" "$port"
printf 'Status: sudo systemctl status board\nLogs: sudo journalctl -u board -f\n'
if [[ "$bind_host" == 0.0.0.0 ]]; then
    printf 'Network access: http://<Kali-IP>:%s (local access: http://127.0.0.1:%s)\n' "$port" "$port"
    printf 'Kali network addresses: '
    hostname -I
fi
if [[ "$bind_host" == 127.0.0.1 ]]; then
    printf 'From your workstation: ssh -N -L %s:127.0.0.1:%s kali@<Kali-IP>\n' "$port" "$port"
    printf 'Then open http://127.0.0.1:%s in your browser.\n' "$port"
fi
