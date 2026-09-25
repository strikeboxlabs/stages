#!/usr/bin/env bash
# Stage 3: install agents, configure Hermes, and start its persistent backend.
# Package sources:
# https://developers.openai.com/codex/cli/
# https://pi.dev/
# https://code.claude.com/docs/en/setup#install-with-npm
# https://hermes-agent.nousresearch.com/install.sh
set -Eeuo pipefail
current_step='startup'
trap 'rc=$?; printf "Error [stage3: %s]: line %s failed (exit %s): %s\nResolve the error above, then rerun stage3.sh.\n" "$current_step" "$LINENO" "$rc" "$BASH_COMMAND" >&2; exit "$rc"' ERR
step() { current_step=$1; printf '\n[stage3] %s\n' "$current_step"; }
die() { printf 'Error [stage3: %s]: %s\n' "$current_step" "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./stage3.sh [--user USER] [--dry-run]

Install OpenAI Codex, Pi coding harness, and Claude Code using global npm
packages under /usr/local (commands in /usr/local/bin).
Install Hermes as the selected user, configure Abliteration Large v2 (prompting
for the API key on first run), and enable/start hermes-backend.service at boot.
The backend listens on 127.0.0.1:9119 for local or SSH-tunneled access.
Existing installations and HERMES_HOME/HERMES_INSTALL_DIR are respected.
Defaults to the sudo-invoking user, otherwise the current account.
Requires a running systemd system. Stage 4 reuses Hermes credentials for Pi.
Run stage2.sh first. Requires Node.js 22+, npm, curl, and network access.

  --user USER  Account for Hermes (use the same account in stage 4).
  --dry-run  Print installation commands without changing anything.
  --help     Show this help.

Rerunning installs the latest releases, including upgrades. Each CLI is
checked with --version; backend health is checked before completion. Codex
and Claude authentication remain specific to those agents.
EOF
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=hermes-runtime.sh
source "$script_dir/hermes-runtime.sh"
target_user=${SUDO_USER:-$(id -un)}
dry_run=false
while (( $# )); do
    case "$1" in
        --dry-run) dry_run=true; shift ;;
        --help|-h) usage; exit 0 ;;
        --user) (( $# >= 2 )) || die '--user requires an account name.'
            target_user=$2; shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ "$target_user" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*\$?$ ]] || die 'Select a valid account with --user USER.'

packages=(
    '@openai/codex@latest'
    '@earendil-works/pi-coding-agent@latest'
    '@anthropic-ai/claude-code@latest'
)
commands=(codex pi claude)
# Use a stable system prefix rather than a root user's personal npm prefix.
npm_prefix=/usr/local
npm_flags=(--global --prefix "$npm_prefix" --engine-strict --include=optional)
hermes_installer_url=https://hermes-agent.nousresearch.com/install.sh

if "$dry_run"; then
    printf '[stage3] Requires Node.js 22+ and npm from stage2.sh. Would run:\n'
    for i in "${!packages[@]}"; do
        script_flag=--ignore-scripts=false
        # Pi explicitly recommends disabling lifecycle scripts for its package.
        [[ "${commands[$i]}" != pi ]] || script_flag=--ignore-scripts
        printf 'npm install'
        printf ' %q' "${npm_flags[@]}" "$script_flag" "${packages[$i]}"
        printf '\n'
    done
    printf 'Then verify /usr/local/bin/{codex,pi,claude} with --version.\n'
    printf 'curl -fsSL %q | bash -s -- --skip-setup\n' "$hermes_installer_url"
    printf 'Install Hermes as %s and publish its launcher at /usr/local/bin/hermes.\n' "$target_user"
    printf 'Configure Hermes for Abliteration Large v2, prompting for a key if needed.\n'
    printf 'Enable/restart hermes-backend.service as %s; check http://127.0.0.1:9119/api/health.\n' "$target_user"
    exit 0
fi

(( EUID == 0 )) || die 'Run with sudo to install system-wide under /usr/local.'
export PATH="/usr/local/bin:$PATH:/usr/sbin:/sbin"
step 'Checking Node.js, npm, and curl'
for tool in node npm curl python3 runuser systemctl systemd-analyze; do
    command -v "$tool" >/dev/null || die "Missing $tool. Run sudo ./stage2.sh first."
done
id "$target_user" >/dev/null 2>&1 || die "Unknown account: $target_user"
target_home=$(getent passwd "$target_user" | cut -d: -f6)
hermes_home=${HERMES_HOME:-$target_home/.hermes}
hermes_root=${HERMES_INSTALL_DIR:-$hermes_home/hermes-agent}
for path in "$target_home" "$hermes_home" "$hermes_root"; do
    [[ "$path" =~ ^/[a-zA-Z0-9_./-]+$ ]] || die 'Hermes paths must be simple absolute paths.'
done
[[ -d "$target_home" ]] || die "Home directory does not exist: $target_home"
[[ -d /run/systemd/system ]] || die 'Stage 3 requires systemd to start the Hermes backend.'
unit=/etc/systemd/system/hermes-backend.service
if [[ -e "$unit" ]] && ! grep -q '^# Managed by stage3.sh$' "$unit"; then
    die "Refusing to overwrite unmanaged service: $unit"
fi
node_version=$(node --version)
node_major=$(node -p 'process.versions.node.split(".")[0]')
[[ "$node_major" =~ ^[0-9]+$ ]] || die "Could not parse Node.js version: $node_version"
(( node_major >= 22 )) || die "Node.js $node_version is too old. Install Node.js 22 or newer, then rerun stage3.sh."
printf 'Node.js: %s\nnpm: %s\n' "$node_version" "$(npm --version)"

for i in "${!packages[@]}"; do
    package=${packages[$i]}
    cli=${commands[$i]}
    step "Installing $package"
    script_flag=--ignore-scripts=false
    [[ "$cli" != pi ]] || script_flag=--ignore-scripts
    npm install "${npm_flags[@]}" "$script_flag" "$package" || \
        die "Installation failed for $package. Check the npm error above (network, Node.js compatibility, or file permissions), then rerun stage3.sh. Earlier successful installations remain in place."
    step "Verifying $cli"
    cli_path="$npm_prefix/bin/$cli"
    [[ -x "$cli_path" ]] || die "Installation completed but $cli_path is missing or not executable."
    "$cli_path" --version || die "$cli_path could not start. Check the error above and rerun after fixing it."
done

step 'Installing Nous Hermes Agent'
# Download as root, but install with the selected account's HOME and ownership.
# Configuration is performed below, in this stage, after the installer finishes.
curl -fsSL "$hermes_installer_url" | as_agent env HERMES_INSTALL_DIR="$hermes_root" bash -s -- --skip-setup || \
    die 'Hermes download or installation failed. Fix the installer error above and rerun stage3.sh.'
init_hermes_runtime
step 'Publishing and verifying Hermes Agent'
as_agent "$hermes_cli" --version || die 'Hermes could not start.'
if [[ "$hermes_cli" != /usr/local/bin/hermes ]]; then
    # Preserve a pre-existing launcher before replacing it on the first run.
    if [[ -e /usr/local/bin/hermes && ! -L /usr/local/bin/hermes ]]; then
        cp -p /usr/local/bin/hermes "/usr/local/bin/hermes.stage3-backup-$(date +%s)"
    fi
    ln -sfn "$hermes_cli" /usr/local/bin/hermes
fi
hash -r
step 'Configuring Hermes model and credentials'
install -d -m 0755 /usr/local/libexec/stages
install -m 0644 "$script_dir/configure-hermes.py" /usr/local/libexec/stages/configure-hermes.py
run_hermes_python /usr/local/libexec/stages/configure-hermes.py

step 'Enabling and starting Hermes backend'
cat > "$unit" <<EOF
# Managed by stage3.sh
[Unit]
Description=Hermes Agent headless backend
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=$target_user
WorkingDirectory=$target_home
Environment=HOME=$target_home
Environment=HERMES_HOME=$hermes_home
Environment=PATH=$target_home/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$hermes_cli serve --host 127.0.0.1 --port 9119
Restart=always
RestartSec=5
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$unit"
systemd-analyze verify "$unit"
systemctl daemon-reload
systemctl enable hermes-backend.service
systemctl restart hermes-backend.service
healthy=false
for (( attempt=0; attempt<60; attempt++ )); do
    if systemctl is-active --quiet hermes-backend.service && \
        curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:9119/api/health 2>/dev/null | \
        python3 -c 'import json, sys; sys.exit(json.load(sys.stdin).get("ok") is not True)' 2>/dev/null; then
        backend_pid=$(systemctl show hermes-backend.service -p MainPID --value)
        sleep 2
        if [[ "$backend_pid" != 0 && "$backend_pid" == "$(systemctl show hermes-backend.service -p MainPID --value)" ]] && \
            systemctl is-active --quiet hermes-backend.service; then
            healthy=true
            break
        fi
    fi
    sleep 2
done
"$healthy" || die 'Hermes backend did not become healthy. Inspect: journalctl -u hermes-backend.service -n 80'
systemctl is-active --quiet hermes-backend.service || die 'Hermes backend stopped after its health check.'
printf '\nStage 3 complete. Hermes is configured for %s and running at 127.0.0.1:9119.\n' "$target_user"
printf 'hermes-backend.service is enabled at boot. Hermes CLI: /usr/local/bin/hermes\n'
printf 'Installed codex, pi, and claude in /usr/local/bin. Stage 4 will reuse the Hermes key for Pi.\n'
printf 'Backend health verified; provider API access has not been tested.\n'
