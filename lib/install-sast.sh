#!/usr/bin/env bash
# Provision the seven CLI scanners documented by strikeboxlabs/sast.
# Sources: semgrep.dev/products/community-edition/, bandit.readthedocs.io/en/latest/start.html,
# brakemanscanner.org/docs/install/, github.com/securego/gosec,
# github.com/aquasecurity/trivy/releases, docs.github.com (Set up CodeQL CLI).
set -Eeuo pipefail
current_step=startup
trap 'rc=$?; printf "Error [install-sast-tools: %s]: line %s failed (exit %s). Fix the error above and rerun.\n" "$current_step" "$LINENO" "$rc" >&2; exit "$rc"' ERR
step() { current_step=$1; printf '\n[install-sast-tools] %s\n' "$current_step"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'HELP'
Usage: sudo ./install-tools.sh --groups sast [--sast-repo PATH] [--user USER] [--dry-run]

Install Semgrep Community Edition, CodeQL with matching query packs, Bandit
(with TOML/SARIF/baseline extras), gosec, Brakeman, and Cppcheck plus Trivy.
Targets glibc Debian/Kali on x86-64 or ARM64. Requires root and network access.
--user USER  Hermes/Pi and Board worker account (default: sudo user or current user).
--repo PATH  Validate the skill catalogue (default: /opt/sast).
--dry-run    Show the plan without downloading, installing, or writing files.
--help       Show this help.

Uses apt for prerequisites/Cppcheck, isolated Python venvs and Ruby gems under
/opt/sast-tools, go install for gosec, and official GitHub releases for Trivy
and the CodeQL bundle. Publishes commands in /usr/local/bin. Existing unrelated
launchers are preserved by refusing collisions. Reruns upgrade managed tools.
Old release directories remain available; interrupted downloads stay unpublished.
GitHub asset SHA256 digests are checked when supplied by the release API.
Links skills into Hermes/Pi and configures existing Board local Pi workers.
The public install-tools.sh entry point manages dispatcher stop and restoration. Without Board,
rerun after stage5.sh to integrate it. Honors HERMES_HOME. No scans are run.
Rules and Trivy databases may require additional downloads at scan time.
CodeQL use is subject to GitHub's CodeQL terms and applicable licensing.
Project-specific SDKs/build dependencies are outside this installer.
HELP
}
repo=/opt/sast
target_user=${SUDO_USER:-$(id -un)}
dry_run=false
while (( $# )); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=true; shift ;;
        --user) (( $# >= 2 )) || die '--user requires an account.'; target_user=$2; shift 2 ;;
        --repo) (( $# >= 2 )) || die '--repo requires a path.'; repo=$2; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done
tools=(semgrep codeql bandit gosec brakeman cppcheck trivy)
[[ -d "$repo/skills" ]] || die "Missing skill catalogue: $repo/skills"
for tool in "${tools[@]}"; do
    [[ -f "$repo/skills/$tool/SKILL.md" ]] || die "Missing expected skill: $tool"
done
# Refuse to silently omit a scanner added to the catalogue later.
for skill in "$repo"/skills/*/SKILL.md; do
    name=${skill%/SKILL.md}; name=${name##*/}
    [[ " ${tools[*]} " == *" $name "* ]] || die "Unrecognized skill $name; update the installer before proceeding."
done
case $(uname -m) in
    x86_64) trivy_arch=64bit; codeql_platform=linux64 ;;
    aarch64|arm64) trivy_arch=ARM64; codeql_platform=linux-arm64 ;;
    *) die 'Supported architectures: x86-64 and ARM64.' ;;
esac
root=/opt/sast-tools
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
for helper in configure-sast-skills.py configure-research-skills.py configure-board-skills.py board_config.py; do
    [[ -f "$script_dir/$helper" ]] || die "Missing $helper; keep helpers together."
done
[[ "$target_user" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*\$?$ ]] || die 'Invalid account name.'
id "$target_user" >/dev/null 2>&1 || die "Unknown account: $target_user"
target_home=$(getent passwd "$target_user" | cut -d: -f6)
hermes_home=${HERMES_HOME:-$target_home/.hermes}
python3 "$script_dir/configure-sast-skills.py" --repo "$repo" --user "$target_user" --hermes-home "$hermes_home"
board_present=false
if [[ -f /opt/board/board/src/board/dispatcher/workers/adapters/pi.py ]]; then
    board_present=true
    [[ -x /opt/board/board/.venv/bin/python ]] || die 'Board must be installed with native skill-profile support; run stage5.sh after updating Board.'
    /opt/board/board/.venv/bin/python "$script_dir/configure-board-skills.py" --board-root /opt/board --check-native
fi
if "$dry_run"; then
    printf 'Catalogue: %s\n' "$repo"
    printf '%s\n' \
        'apt: ca-certificates curl jq tar gzip zstd python3 python3-venv ruby ruby-dev build-essential golang-go git util-linux cppcheck' \
        "Semgrep: $root/semgrep venv, pip install --upgrade semgrep" \
        "Bandit: $root/bandit venv, pip install --upgrade bandit[toml,sarif,baseline]" \
        "Brakeman: isolated Ruby gems in $root/gems; publish a wrapper" \
        'gosec: go install github.com/securego/gosec/v2/cmd/gosec@latest (automatic toolchain updates enabled)' \
        "Trivy: official aquasecurity/trivy release, Linux-$trivy_arch archive" \
        "CodeQL: official github/codeql-action codeql-bundle-$codeql_platform.tar.gz (includes queries)" \
        'Verify all seven CLI versions plus CodeQL language and query-pack resolution.'
    printf 'Would link SAST skills for Hermes/Pi user %s and configure Board to load %s/skills when installed.\n' "$target_user" "$root"
    exit 0
fi
(( EUID == 0 )) || die 'Run as root or with sudo.'
if ! "$dry_run" && command -v systemctl >/dev/null && systemctl is-active --quiet board-dispatcher.service; then
    die 'Board dispatcher is active. Finish active work and stop board-dispatcher.service before provisioning; no changes made.'
fi
umask 022
[[ -f /etc/debian_version ]] || die 'This installer requires Debian/Kali.'
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
command -v apt-get >/dev/null || die 'apt-get is required.'
if [[ -e "$root" || -L "$root" ]]; then
    [[ -d "$root" && ! -L "$root" && -f "$root/.managed-by-install-sast-tools" ]] || die "Refusing unmanaged installation root: $root"
fi
for tool in semgrep codeql bandit gosec brakeman trivy; do
    destination=/usr/local/bin/$tool
    if [[ -e "$destination" || -L "$destination" ]]; then
        [[ -L "$destination" && $(readlink "$destination") == "$root/"* ]] || die "Refusing unrelated launcher: $destination"
    fi
done
if "$board_present" && [[ -d /run/systemd/system ]]; then
    dispatcher_user=$(systemctl show board-dispatcher.service -p User --value)
    [[ -z "$dispatcher_user" || "$dispatcher_user" == "$target_user" ]] || die "Board uses $dispatcher_user; select --user $dispatcher_user."
fi
step 'Installing system prerequisites and Cppcheck'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl jq tar gzip zstd python3 python3-venv ruby ruby-dev \
    build-essential golang-go git util-linux cppcheck
install -d -m 0755 "$root" /usr/local/bin
touch "$root/.managed-by-install-sast-tools"
# Serialize reruns and upgrades.
exec 9>"$root/.install.lock"
flock -n 9 || die 'Another SAST tool installation is running.'
work=$(mktemp -d "$root/.install.XXXXXXXX")
trap 'rm -rf -- "$work"' EXIT
fetch() { curl --fail --location --show-error --silent --retry 3 --connect-timeout 20 --max-time 3600 "$1" -o "$2"; }
# Choose an exact asset from the official latest release, retaining its metadata.
release_asset() {
    local repository=$1 asset=$2 output=$3 url digest
    if [[ -z "${4:-}" ]]; then
        fetch "https://api.github.com/repos/$repository/releases/latest" "$work/release.json"
    else
        cp "$4" "$work/release.json"
    fi
    jq -e --arg name "$asset" '[.assets[] | select(.name == $name)] | length == 1' "$work/release.json" >/dev/null || die "Release asset not found: $repository/$asset"
    url=$(jq -r --arg name "$asset" '.assets[] | select(.name == $name) | .browser_download_url' "$work/release.json")
    [[ "$url" == "https://github.com/$repository/releases/download/"* ]] || die 'Unexpected release download URL.'
    fetch "$url" "$output"
    digest=$(jq -r --arg name "$asset" '.assets[] | select(.name == $name) | .digest // ""' "$work/release.json")
    if [[ "$digest" == sha256:* ]]; then
        printf '%s  %s\n' "${digest#sha256:}" "$output" | sha256sum --check --status || die "SHA256 mismatch: $asset"
    else
        printf 'Release API supplies no SHA256 digest for %s; downloaded over HTTPS.\n' "$asset"
    fi
}
step 'Installing isolated Python scanners'
for tool in semgrep bandit; do
    [[ -x "$root/$tool/bin/python" ]] || python3 -m venv "$root/$tool"
    package=$tool
    [[ "$tool" != bandit ]] || package='bandit[toml,sarif,baseline]'
    "$root/$tool/bin/python" -m pip install --disable-pip-version-check --upgrade "$package"
    ln -sfn "$root/$tool/bin/$tool" "/usr/local/bin/$tool"
done
step 'Installing isolated Brakeman gems'
install -d -m 0755 "$root/gems" "$root/bin"
env GEM_HOME="$root/gems" GEM_PATH="$root/gems" gem install --no-document --bindir "$root/gems/bin" brakeman
cat > "$root/bin/brakeman" <<'WRAPPER'
#!/usr/bin/env bash
export GEM_HOME=/opt/sast-tools/gems
export GEM_PATH=/opt/sast-tools/gems
exec /opt/sast-tools/gems/bin/brakeman "$@"
WRAPPER
chmod 0755 "$root/bin/brakeman"
ln -sfn "$root/bin/brakeman" /usr/local/bin/brakeman
step 'Building gosec using the Go toolchain'
install -d -m 0755 "$work/go-bin"
env GOBIN="$work/go-bin" GOTOOLCHAIN=auto go install github.com/securego/gosec/v2/cmd/gosec@latest
install -m 0755 "$work/go-bin/gosec" "$root/bin/gosec"
ln -sfn "$root/bin/gosec" /usr/local/bin/gosec
step 'Installing official Trivy release'
fetch https://api.github.com/repos/aquasecurity/trivy/releases/latest "$work/trivy-release.json"
trivy_version=$(jq -er '.tag_name | ltrimstr("v")' "$work/trivy-release.json")
[[ "$trivy_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die 'Unexpected Trivy release version.'
release_asset aquasecurity/trivy "trivy_${trivy_version}_Linux-$trivy_arch.tar.gz" "$work/trivy.tar.gz" "$work/trivy-release.json"
install -d "$work/trivy"
tar -xzf "$work/trivy.tar.gz" -C "$work/trivy" trivy
install -m 0755 "$work/trivy/trivy" "$root/bin/trivy"
ln -sfn "$root/bin/trivy" /usr/local/bin/trivy
step 'Installing CodeQL CLI and matching query bundle (large download)'
release_asset github/codeql-action "codeql-bundle-$codeql_platform.tar.gz" "$work/codeql.tar.gz"
codeql_tag=$(jq -er '.tag_name' "$work/release.json")
[[ "$codeql_tag" =~ ^[a-zA-Z0-9_.-]+$ ]] || die 'Unexpected CodeQL release tag.'
tar -xzf "$work/codeql.tar.gz" -C "$work"
[[ -x "$work/codeql/codeql" ]] || die 'Unexpected CodeQL bundle layout.'
"$work/codeql/codeql" version
release_dir=$(mktemp -d "$root/codeql-$codeql_tag.XXXXXXXX")
chmod 0755 "$release_dir"
mv "$work/codeql" "$release_dir/codeql"
cp "$work/release.json" "$release_dir/release.json"
ln -sfn "$release_dir/codeql/codeql" /usr/local/bin/codeql
step 'Verifying scanner versions and CodeQL resources'
semgrep --version
bandit --version
brakeman --version
gosec -version
cppcheck --version
trivy --version
codeql version
codeql resolve languages
codeql resolve qlpacks
printf '\nAll seven SAST CLIs installed. Project builds, rules and scanner data may need further dependencies/network access.\n'

step 'Linking SAST skills and configuring Board workers'
python3 "$script_dir/configure-sast-skills.py" --repo "$repo" --user "$target_user" --hermes-home "$hermes_home" --apply
for skill in "$repo"/skills/*/SKILL.md; do
    runuser -u "$target_user" -- test -r "$skill"
done
for tool in "${tools[@]}"; do
    version_flag=--version
    [[ "$tool" != codeql ]] || version_flag=version
    [[ "$tool" != gosec ]] || version_flag=-version
    runuser -u "$target_user" -- env HOME="$target_home" PATH=/usr/local/bin:/usr/bin:/bin "$tool" "$version_flag"
done
if "$board_present"; then
    dispatcher_group=$(id -gn "$target_user")
    if [[ -d /run/systemd/system ]]; then
        service_group=$(systemctl show board-dispatcher.service -p Group --value)
        [[ -z "$service_group" ]] || dispatcher_group=$service_group
    fi
    doctor_identity=(--user "$target_user" --group "$dispatcher_group")
    /opt/board/board/.venv/bin/python "$script_dir/configure-board-skills.py" --board-root /opt/board \
        --skills-dir "$root/skills" "${doctor_identity[@]}" --apply
    printf 'Board catalog configured; existing sessions were preserved.\n'
else
    printf 'Board is absent; rerun after stage5.sh to integrate its workers.\n'
fi
printf 'SAST tools and skills configured for %s.\n' "$target_user"
