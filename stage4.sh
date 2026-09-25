#!/usr/bin/env bash
# Stage 4: configure Pi from Hermes and enable SSH access for Hermes Desktop.
set -Eeuo pipefail
current_step='startup'
trap 'rc=$?; printf "Error [stage4: %s]: line %s failed (exit %s).\n" "$current_step" "$LINENO" "$rc" >&2; exit "$rc"' ERR
die() { printf 'Error [stage4: %s]: %s\n' "$current_step" "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: ./stage4.sh [--user USER] [--dry-run]

Configure Pi using the Abliteration API key and Large v2 model saved by stage 3.
Enable OpenSSH at boot for Hermes Desktop connections from another computer.
Allow TCP 22 and configured SSH ports through local IPv4 input firewall rules.
After manually reloading a firewall, restart hermes-ssh-firewall.service.
Run as root or via sudo. Defaults to the sudo-invoking user when present,
otherwise the current account (including root). Override with --user.

Reuses the stage-3 API key without prompting. Generates a dedicated SSH client key
for transfer to your workstation, or reuses the pair on reruns. The private
key has no passphrase for unattended use and is saved with mode 0600.
Preserves unrelated settings and SSH keys; backs up changed agent configs.

Run stages 2 and 3 first. Agent credentials/configuration are per-user.
Stage 3 runs the headless Hermes backend as hermes-backend.service.
No graphical desktop or publicly listening Hermes service is needed.

  --user USER  Account for agents and SSH (root is supported).
  --dry-run    Describe actions without prompting or making changes.
  --help       Show this help.
EOF
}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hermes-runtime.sh
source "$script_dir/hermes-runtime.sh"
target_user=${SUDO_USER:-$(id -un)}
dry_run=false
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=true; shift ;;
        --user) (( $# >= 2 )) || die '--user requires an account name.'
            target_user=$2; shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done
if "$dry_run"; then
    printf '%s\n' \
        '[stage4] Would reuse the stage-3 Hermes API key and generate a workstation SSH key pair.' \
        "Target account: ${target_user:-not selected} (use --user if needed)." \
        'Would configure Pi from Hermes for https://api.abliteration.ai/v1 and abliterated-model-large-v2.' \
        'Would preserve unrelated settings/keys and back up existing agent configuration.' \
        'Would check the running Hermes backend and check/install openssh-server.' \
        'Would add the public key, generate missing SSH host keys, validate sshd, and enable/start SSH.' \
        'Would persistently allow TCP 22 and configured SSH ports through the local IPv4 input firewall.' \
        'Hermes Desktop connects over SSH; stage 3 has already started the backend.'
    exit 0
fi
(( EUID == 0 )) || die 'Run as root or with sudo; installing/enabling SSH requires root.'
[[ -n "$target_user" && "$target_user" != -* ]] || die 'Select a valid account with --user USER.'
id "$target_user" >/dev/null 2>&1 || die "Unknown account: $target_user"
export PATH="/usr/local/bin:$PATH:/usr/sbin:/sbin"
for tool in python3 pi runuser systemctl apt-get; do
    command -v "$tool" >/dev/null || die "Missing $tool. Run stages 2 and 3 first."
done
[[ -d /run/systemd/system ]] || die 'This stage requires a running systemd system to enable SSH.'
target_home=$(getent passwd "$target_user" | cut -d: -f6)
[[ -d "$target_home" ]] || die "Home directory does not exist: $target_home"
init_hermes_runtime
as_agent "$hermes_cli" --version || die 'Hermes could not start. Rerun stage3.sh.'
systemctl is-active --quiet hermes-backend.service || die 'Hermes backend is not running. Rerun stage3.sh for this account.'
[[ "$(systemctl show hermes-backend.service -p User --value)" == "$target_user" ]] || \
    die 'Hermes backend runs as a different account. Use the same --user as stage3.sh.'
current_step='Installing OpenSSH server'
if ! command -v sshd >/dev/null || ! command -v ssh-keygen >/dev/null; then
    apt-get update || die 'Could not refresh apt indexes. Check network access and apt sources.'
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server || die 'OpenSSH installation failed.'
fi
current_step='Configuring agents and workstation SSH access'
install -d -m 0755 /usr/local/libexec/stages
install -m 0644 "$script_dir/configure-desktop.py" /usr/local/libexec/stages/configure-desktop.py
run_hermes_python /usr/local/libexec/stages/configure-desktop.py

current_step='Validating and enabling SSH'
ssh-keygen -A
install -d -m 0755 /run/sshd
sshd -t || die 'SSH configuration is invalid; inspect the error above.'
ssh_effective=$(sshd -T -C "user=$target_user,host=localhost,addr=127.0.0.1")
# OpenSSH versions differ in the capitalization of effective option names.
ssh_effective=${ssh_effective,,}
[[ "$ssh_effective" == *'pubkeyauthentication yes'* ]] || die 'Existing SSH policy disables public-key authentication. Enable it for the target account before retrying.'
if (( $(id -u "$target_user") == 0 )); then
    case "$(awk '$1 == "permitrootlogin" { print $2 }' <<< "$ssh_effective")" in
        yes|prohibit-password|without-password) ;;
        *) die 'Existing SSH policy prevents root key login with an interactive session. Set PermitRootLogin prohibit-password or yes before retrying.' ;;
    esac
fi
if [[ "$ssh_effective" != *'allowtcpforwarding yes'* && "$ssh_effective" != *'allowtcpforwarding local'* ]]; then
    die 'Existing SSH policy prevents local TCP forwarding, which Hermes Desktop needs. Enable it for the target account.'
fi
current_step='Allowing SSH through the local firewall'
if ! command -v nft >/dev/null || ! command -v iptables-legacy >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y nftables iptables
fi
install -d -m 0755 /usr/local/libexec
firewall_helper=/usr/local/libexec/hermes-ssh-firewall
firewall_unit=/etc/systemd/system/hermes-ssh-firewall.service
for managed in "$firewall_helper" "$firewall_unit"; do
    if [[ -e "$managed" ]] && ! grep -q '^# Managed by stage4.sh$' "$managed"; then
        die "Refusing to overwrite unmanaged file: $managed"
    fi
done
cat > "$firewall_helper" <<'PY'
#!/usr/bin/python3
# Managed by stage4.sh
import json
import shlex
import subprocess
import sys


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


ports = sorted({22, *(int(value) for value in sys.argv[1:])})
if not all(1 <= port <= 65535 for port in ports):
    raise SystemExit('Invalid SSH port')
tag = 'stage4-ssh-port'
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
                   'right': {'set': ports}}},
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
for port in ports:
    run(legacy, '-w', '-I', 'INPUT', '1', '-p', 'tcp', '--dport', str(port),
        '-m', 'comment', '--comment', tag, '-j', 'ACCEPT')
print(f'[stage4] Local IPv4 input firewall allows SSH TCP ports {ports}.')
PY
chmod 0755 "$firewall_helper"
ssh_ports=$(awk '$1 == "port" { print $2 }' <<< "$ssh_effective" | sort -nu | tr '\n' ' ')
cat > "$firewall_unit" <<EOF
# Managed by stage4.sh
[Unit]
Description=Allow Hermes Desktop SSH connections through the local firewall
After=nftables.service ufw.service firewalld.service netfilter-persistent.service
Before=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/hermes-ssh-firewall $ssh_ports
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$firewall_unit"
systemd-analyze verify "$firewall_unit"
systemctl daemon-reload
systemctl enable hermes-ssh-firewall.service
systemctl restart hermes-ssh-firewall.service
current_step='Starting SSH'
systemctl enable --now ssh
systemctl is-active --quiet ssh || die 'SSH service did not start. Inspect: sudo systemctl status ssh'
printf '\n[stage4] Complete. SSH is enabled at boot for %s.\n' "$target_user"
printf 'In Hermes Desktop: Settings > Gateways > Add connection > SSH.\n'
printf 'SSH host: %s@<Kali-IP>; Hermes path: /usr/local/bin/hermes\n' "$target_user"
printf 'Kali network addresses: '
hostname -I
printf 'Transfer %s/.ssh/hermes-desktop-client to your workstation as a PRIVATE key.\n' "$target_home"
printf 'Use an SSH config entry with IdentityFile pointing to that file, then select Test in Desktop.\n'
printf '\nExample workstation ~/.ssh/config entry (replace <Kali-IP>):\n'
printf 'Host kali-hermes\n    HostName <Kali-IP>\n    User %s\n    IdentityFile ~/.ssh/hermes-desktop-client\n    IdentitiesOnly yes\n' "$target_user"
printf 'On Linux/macOS: chmod 600 ~/.ssh/hermes-desktop-client\n'
printf 'Test from the workstation with: ssh kali-hermes\n'
printf 'SSH normally uses TCP 22. Hermes backend default: TCP 9119, carried inside the SSH tunnel.\n'
printf 'TCP 8642 belongs to the separate OpenAI-compatible API server; it is not needed here.\n'
printf 'VM NAT, external firewalls, and routing must allow SSH to this host.\n'
