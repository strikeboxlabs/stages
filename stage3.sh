#!/usr/bin/env bash
# Stage 3: install coding agents globally for this prepared system.
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
Usage: sudo ./stage3.sh [--dry-run]

Install OpenAI Codex, Pi coding harness, and Claude Code using global npm
packages under /usr/local (commands in /usr/local/bin).
Also installs Nous Hermes Agent using its official Linux installer, skipping
the interactive setup wizard. Hermes uses the installer's root install layout
(existing installations and HERMES_HOME/HERMES_INSTALL_DIR are respected).
Run stage2.sh first. Requires Node.js 22+, npm, curl, and network access.

  --dry-run  Print installation commands without changing anything.
  --help     Show this help.

Rerunning installs the latest releases, including upgrades. Each CLI is
checked with --version. Sign-in and API credentials are configured separately
by the user who will run the agents.
EOF
}

dry_run=false
case "${1:-}" in
    --dry-run) dry_run=true ;;
    --help|-h) usage; exit 0 ;;
    '') ;;
    *) usage >&2; exit 2 ;;
esac
(( $# <= 1 )) || die 'Too many arguments.'

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
    printf 'Then verify hermes with --version. Run hermes setup separately.\n'
    exit 0
fi

(( EUID == 0 )) || die 'Run with sudo to install system-wide under /usr/local.'
export PATH="/usr/local/bin:$PATH:/usr/sbin:/sbin"
step 'Checking Node.js, npm, and curl'
for tool in node npm curl; do
    command -v "$tool" >/dev/null || die "Missing $tool. Run sudo ./stage2.sh first."
done
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
# pipefail makes a failed curl download fail the stage even if bash exits zero.
curl -fsSL "$hermes_installer_url" | bash -s -- --skip-setup || \
    die 'Hermes download or installation failed. Check the installer error above, then rerun stage3.sh. Earlier successful installations remain in place.'

step 'Verifying Hermes Agent'
# A fresh root install uses /usr/local/bin; older installs may use ~/.local/bin.
export PATH="$PATH:$HOME/.local/bin"
hash -r
command -v hermes >/dev/null || die 'Hermes installer completed but the hermes command is missing. Check the installer output for its installation path.'
hermes --version || die 'Hermes could not start. Check the error above and rerun after fixing it.'

printf '\nStage 3 complete. Installed codex, pi, and claude in %s/bin.\n' "$npm_prefix"
printf 'Nous Hermes Agent is available at %s. Run hermes setup as your intended user.\n' "$(command -v hermes)"
printf 'Run each agent as your intended user to configure authentication.\n'
printf 'If your shell cannot find them, add /usr/local/bin to your PATH.\n'
