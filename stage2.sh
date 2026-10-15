#!/usr/bin/env bash
# Stage 2 draft: basic development tools for agent workloads on Kali/Debian.
set -Eeuo pipefail
current_step='startup'
trap 'rc=$?; printf "Error [stage2: %s]: line %s failed (exit %s): %s\nResolve the error above, then rerun stage2.sh.\n" "$current_step" "$LINENO" "$rc" "$BASH_COMMAND" >&2; exit "$rc"' ERR
step() { current_step=$1; printf '\n[stage2] %s\n' "$current_step"; }
die() { printf 'Error [stage2: %s]: %s\n' "$current_step" "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: sudo ./stage2.sh [--dry-run]

Stage 2 draft: install basic development tools from the configured apt sources.
Includes Git, curl, CA certificates, compiler/build tools, pkg-config, Python
venvs, pipx, Node.js, npm, jq, ripgrep, unzip, and tmux.
Safe to rerun after a partial failure.

  --dry-run  Print the package list without installing or changing anything.
  --help     Show this help.

Run after stage1.sh. Additional runtimes, browsers, and agent software
can be added to this stage once selected. No swap or log management changes.
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
    git curl ca-certificates build-essential pkg-config
    python3 python3-venv pipx nodejs npm jq ripgrep unzip tmux
)
if "$dry_run"; then
    printf '[stage2] Would install these packages using apt-get:\n'
    printf '  %s\n' "${packages[@]}"
    exit 0
fi
(( EUID == 0 )) || die 'Run this script with sudo.'
export PATH="$PATH:/usr/sbin:/sbin"
command -v apt-get >/dev/null || die 'apt-get is required; this script targets Kali/Debian.'
command -v dpkg-query >/dev/null || die 'dpkg-query is required to verify installed packages.'

step 'Refreshing package lists'
apt-get update || die 'Could not refresh package lists. Check network access and apt sources, then rerun.'
step 'Installing basic development tools'
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}" || \
    die 'Package installation failed. Resolve the apt error above, then rerun stage2.sh.'

step 'Verifying installed packages and commands'
for package in "${packages[@]}"; do
    status=$(dpkg-query -W -f='${Status}' "$package") || die "Could not inspect package: $package"
    [[ "$status" == 'install ok installed' ]] || die "Package is not fully installed: $package ($status)"
done
for tool in git curl cc c++ make pkg-config python3 pipx node npm jq rg unzip tmux; do
    command -v "$tool" >/dev/null || die "Expected command is unavailable after installation: $tool"
done
printf '\nStage 2 complete. Basic development tools are installed.\n'
