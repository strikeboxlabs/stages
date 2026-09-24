#!/usr/bin/env bash
# Stage 4: configure Pi, Hermes, and SSH access for Hermes Desktop.
set -Eeuo pipefail
current_step='startup'
trap 'rc=$?; printf "Error [stage4: %s]: line %s failed (exit %s).\n" "$current_step" "$LINENO" "$rc" >&2; exit "$rc"' ERR
die() { printf 'Error [stage4: %s]: %s\n' "$current_step" "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: ./stage4.sh [--user USER] [--dry-run]

Configure Pi and Hermes with the same Abliteration API key and Large v2 model.
Enable OpenSSH at boot for Hermes Desktop connections from another computer.
Run as root or via sudo. Defaults to the sudo-invoking user when present,
otherwise the current account (including root). Override with --user.

Prompts once for the API key (hidden). Generates a dedicated SSH client key
for transfer to your workstation, or reuses the pair on reruns. The private
key has no passphrase for unattended use and is saved with mode 0600.
Preserves unrelated settings and SSH keys; backs up changed agent configs.

Run stages 2 and 3 first. Agent credentials/configuration are per-user.
Desktop manages the headless Hermes backend over SSH when you connect.
No graphical desktop or publicly listening Hermes service is needed.

  --user USER  Account for agents and SSH (root is supported).
  --dry-run    Describe actions without prompting or making changes.
  --help       Show this help.
EOF
}
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
        '[stage4] Would prompt for one Abliteration API key and generate a workstation SSH key pair.' \
        "Target account: ${target_user:-not selected} (use --user if needed)." \
        'Would configure Pi and Hermes for https://api.abliteration.ai/v1 and abliterated-model-large-v2.' \
        'Would preserve unrelated settings/keys and back up existing agent configuration.' \
        'Would check/install Hermes backend dependencies and openssh-server.' \
        'Would add the public key, generate missing SSH host keys, validate sshd, and enable/start SSH.' \
        'Hermes Desktop connects over SSH and starts its backend on demand.'
    exit 0
fi
(( EUID == 0 )) || die 'Run as root or with sudo; installing/enabling SSH requires root.'
[[ -n "$target_user" && "$target_user" != -* ]] || die 'Select a valid account with --user USER.'
id "$target_user" >/dev/null 2>&1 || die "Unknown account: $target_user"
export PATH="/usr/local/bin:$PATH:/usr/sbin:/sbin"
for tool in python3 pi hermes runuser systemctl apt-get; do
    command -v "$tool" >/dev/null || die "Missing $tool. Run stages 2 and 3 first."
done
[[ -d /run/systemd/system ]] || die 'This stage requires a running systemd system to enable SSH.'
target_home=$(getent passwd "$target_user" | cut -d: -f6)
[[ -d "$target_home" ]] || die "Home directory does not exist: $target_home"
hermes_root=''
for candidate in /usr/local/lib/hermes-agent "$target_home/.hermes/hermes-agent"; do
    for venv in venv .venv; do
        if [[ -x "$candidate/$venv/bin/python" ]]; then
            hermes_root=$candidate
            hermes_python="$candidate/$venv/bin/python"
            break 2
        fi
    done
done
[[ -n "$hermes_root" ]] || die 'Cannot locate the Hermes Python environment. Run stage3.sh first.'
current_step='Checking Hermes backend dependencies'
if ! "$hermes_python" -c 'import yaml, fastapi, uvicorn, ptyprocess' 2>/dev/null; then
    if "$hermes_python" -m pip --version >/dev/null 2>&1; then
        "$hermes_python" -m pip install -e "$hermes_root[web,pty]"
    else
        uv_bin=$(command -v uv || true)
        if [[ -z "$uv_bin" && -x /root/.local/bin/uv ]]; then uv_bin=/root/.local/bin/uv; fi
        [[ -n "$uv_bin" ]] || die 'Hermes backend dependencies are missing and neither pip nor uv is available.'
        "$uv_bin" pip install --python "$hermes_python" -e "$hermes_root[web,pty]"
    fi
fi
current_step='Installing OpenSSH server'
if ! command -v sshd >/dev/null || ! command -v ssh-keygen >/dev/null; then
    apt-get update || die 'Could not refresh apt indexes. Check network access and apt sources.'
    DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server || die 'OpenSSH installation failed.'
fi
current_step='Configuring agents and workstation SSH access'
runuser -u "$target_user" -- env HOME="$target_home" "$hermes_python" - <<'PY'
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import yaml


def write_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)  # mkstemp gives owner-only permissions.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    root = Path.home() / '.pi/agent'
    print(f'[stage4] Configuring {root}', flush=True)
    documents = {}
    for name in ('models.json', 'settings.json', 'auth.json'):
        path = root / name
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError(f'{name} must contain a JSON object; no files changed.')
        documents[name] = data
    providers = documents['models.json'].setdefault('providers', {})
    if not isinstance(providers, dict):
        raise ValueError('models.json providers must be an object; no files changed.')

    hermes_home = Path.home() / '.hermes'
    hermes_path = hermes_home / 'config.yaml'
    hermes_config = yaml.safe_load(hermes_path.read_text()) if hermes_path.exists() else {}
    if hermes_config is None:
        hermes_config = {}
    if not isinstance(hermes_config, dict):
        raise ValueError('Hermes config.yaml must contain a mapping; no files changed.')
    hermes_model = hermes_config.get('model') or {}
    if isinstance(hermes_model, str):
        hermes_model = {'default': hermes_model}
    if not isinstance(hermes_model, dict):
        raise ValueError('Hermes model configuration is invalid; no files changed.')

    # The Python program arrives on stdin; read the secret from the terminal.
    with open('/dev/tty', 'w') as terminal:
        key = getpass.getpass('Abliteration API key (hidden): ', stream=terminal).strip()
    if not key:
        raise ValueError('Empty API key; no files changed.')
    if any(char.isspace() for char in key):
        raise ValueError('API key contains whitespace; no files changed.')
    raw_key = key
    ssh_dir = Path.home() / '.ssh'
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    ssh_dir.chmod(0o700)
    authorized = ssh_dir / 'authorized_keys'
    if authorized.is_symlink():
        raise ValueError('Refusing to overwrite a symlink at ~/.ssh/authorized_keys.')
    client_key = ssh_dir / 'hermes-desktop-client'
    public_path = client_key.with_suffix('.pub')
    if client_key.is_symlink() or public_path.is_symlink():
        raise ValueError('Refusing symlinked Hermes Desktop SSH key files.')
    if not client_key.exists():
        if public_path.exists():
            raise ValueError('Hermes Desktop public key exists without its private key; move it aside before retrying.')
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                        '-C', 'hermes-desktop-client', '-f', str(client_key)], check=True)
    client_key.chmod(0o600)
    # Derive the public key from the private key, including on reruns.
    result = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(client_key)],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Cannot read the existing Hermes Desktop private key (must be valid and unencrypted).')
    public_key = result.stdout.strip()
    public_path.write_text(public_key + ' hermes-desktop-client\n')
    public_path.chmod(0o600)
    # Escape Pi's config interpolation syntax so the saved key stays literal.
    key = key.replace('$', '$$')
    if key.startswith('!'):
        key = '$!' + key[1:]
    documents['auth.json']['abliteration'] = {'type': 'api_key', 'key': key}
    providers['abliteration'] = {
        'baseUrl': 'https://api.abliteration.ai/v1',
        'api': 'openai-completions',
        'apiKey': '$ABLIT_KEY',
        'models': [{
            'id': 'abliterated-model-large-v2',
            'name': 'Abliteration Large v2 (Cyber)',
            'input': ['text'],
            'contextWindow': 1000000,
            # Conservative per-response limit; not the provider's maximum.
            'maxTokens': 8192,
        }],
    }
    documents['settings.json'].update(
        defaultProvider='abliteration', defaultModel='abliterated-model-large-v2'
    )
    hermes_model.update(default='abliterated-model-large-v2', provider='custom',
                        base_url='https://api.abliteration.ai/v1', api_mode='chat_completions',
                        api_key=raw_key, context_length=1000000)
    for field in ('key_env', 'api_key_env'):
        hermes_model.pop(field, None)
    hermes_config['model'] = hermes_model
    hermes_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    hermes_home.chmod(0o700)
    if hermes_path.exists():
        backup = Path(tempfile.mkdtemp(prefix='stage4-backup-', dir=hermes_home))
        shutil.copyfile(hermes_path, backup / 'config.yaml')
        (backup / 'config.yaml').chmod(0o600)
    # JSON is valid YAML, and safely quotes arbitrary API key characters.
    write_json(hermes_path, hermes_config)
    existing_keys = authorized.read_text() if authorized.exists() else ''
    key_blob = public_key.split()[1]
    if not any(key_blob in line.split() for line in existing_keys.splitlines() if not line.lstrip().startswith('#')):
        with authorized.open('a') as stream:
            if existing_keys and not existing_keys.endswith('\n'):
                stream.write('\n')
            stream.write(public_key + '\n')
    authorized.chmod(0o600)

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    existing = [name for name in documents if (root / name).exists()]
    if existing:
        backup = Path(tempfile.mkdtemp(prefix='stage4-backup-', dir=root))
        for name in existing:
            shutil.copyfile(root / name, backup / name)
            (backup / name).chmod(0o600)
        print(f'[stage4] Previous configuration backed up in {backup}')
    for name, data in documents.items():
        write_json(root / name, data)

    print('[stage4] Verifying Pi model registration offline...', flush=True)
    env = dict(os.environ, PI_CODING_AGENT_DIR=str(root), PI_OFFLINE='1')
    result = subprocess.run([
        'pi', '--offline', '--no-extensions', '--no-skills',
        '--no-prompt-templates', '--no-themes', '--no-context-files',
        '--no-approve', '--list-models', 'abliterated-model-large-v2',
    ], env=env, cwd=Path.home(), text=True, capture_output=True, timeout=60)
    if result.returncode or not any(
        line.split()[:2] == ['abliteration', 'abliterated-model-large-v2']
        for line in result.stdout.splitlines()
    ):
        raise RuntimeError('Configuration saved, but Pi did not list the v2 model. Check the installed Pi version and ~/.pi/agent/models.json.')
    print('[stage4] Pi and Hermes configured with the same API key; workstation SSH key installed.')
    print('Local commands: pi or hermes. Credentials have owner-only permissions.')
    print(f'Workstation PRIVATE key to transfer securely: {client_key}')
    print('Store that key on the workstation with owner-only access, then select it in SSH configuration.')
    print('Default: abliteration / abliterated-model-large-v2. API access has not been tested.')


try:
    main()
except (KeyboardInterrupt, EOFError):
    sys.exit('\nError [stage4]: Cancelled. Configuration already written, if any, remains in place; rerun to finish.')
except (OSError, ValueError, RuntimeError, yaml.YAMLError, subprocess.SubprocessError) as exc:
    sys.exit(f'Error [stage4]: {exc}')
PY

current_step='Validating and enabling SSH'
ssh-keygen -A
install -d -m 0755 /run/sshd
sshd -t || die 'SSH configuration is invalid; inspect the error above.'
ssh_effective=$(sshd -T -C "user=$target_user,host=localhost,addr=127.0.0.1")
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
