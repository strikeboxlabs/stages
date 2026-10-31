#!/usr/bin/env bash
# Provision research-library skill tools that have no Kali APT package.
# Sources are upstream-canonical (repositories cited by the skill packages).
# Idempotent: every tool is skipped when its command already resolves.
# Individual tool failures warn and continue; the summary lists them.
set -Eeuo pipefail

export PIPX_HOME="${PIPX_HOME:-/opt/pipx}"
export PIPX_BIN_DIR="${PIPX_BIN_DIR:-/usr/local/bin}"
TOOLS_ROOT="${TOOLS_ROOT:-/opt/tools}"
failed=()
installed=()
skipped=()

have() { command -v "$1" >/dev/null 2>&1; }

pipx_tool() {  # pipx_tool <command> <spec> [extra pipx args...]
    local cmd=$1 spec=$2; shift 2
    if have "$cmd"; then skipped+=("$cmd"); return 0; fi
    if pipx install --quiet "$@" "$spec" >/dev/null 2>&1; then
        installed+=("$cmd")
    else
        failed+=("$cmd (pipx $spec)")
    fi
}

pipx_inject() {  # best-effort dependency fixup; never fails the tool
    pipx inject --quiet --force "$1" "${@:2}" >/dev/null 2>&1 || true
}

# --- 1. PyPI tools via pipx (Python 3.14 compatible, verified 2026-10-31)
pipx_tool fickling fickling
pipx_tool flask-unsign flask-unsign
pipx_tool git-dumper git-dumper
pipx_tool searchor searchor
pipx_tool uncompyle6 uncompyle6
pipx_tool fakedns fakedns
pipx_tool bbot bbot
pipx_tool awslocal awscli-local
pipx_tool passpie passpie && have passpie && pipx_inject passpie 'setuptools<81'
pipx_tool pwncat-cs pwncat-cs && have pwncat-cs && { pipx_inject pwncat-cs 'setuptools<81' 'packaging>=23'; }

# --- 2. GitHub script tools: clone + system-site venv + /usr/local/bin wrapper
git_tool() {  # git_tool <skill-id> <repo-url> <entry-relative-path> [deps...]
    local sid=$1 repo=$2 entry=$3; shift 3
    local cmd=$sid
    [[ "$sid" == docker-registry-grabber ]] && cmd=drg
    if have "$cmd"; then skipped+=("$cmd"); return 0; fi
    local d="$TOOLS_ROOT/$sid"
    if [[ ! -d "$d" ]]; then
        git clone -q --depth 1 "$repo" "$d" || { failed+=("$cmd (clone $repo)"); return 0; }
    fi
    python3 -m venv --system-site-packages "$d/.venv" 2>/dev/null || true
    if (( $# )); then
        "$d/.venv/bin/pip" -q install "$@" >/dev/null 2>&1 || true
    fi
    printf '#!/bin/sh\nexec %s/.venv/bin/python %s/%s "$@"\n' "$d" "$d" "$entry" > "/usr/local/bin/$cmd"
    chmod 0755 "/usr/local/bin/$cmd"
    if have "$cmd"; then installed+=("$cmd"); else failed+=("$cmd (wrapper)"); fi
}

# windapsearch needs system python3-ldap (APT) for its LDAP dependency.
git_tool windapsearch https://github.com/ropnop/windapsearch windapsearch.py 'pyasn1>=0.4.5' 'pyasn1-modules>=0.2.5'
git_tool targetedkerberoast https://github.com/ShutdownRepo/targetedKerberoast targetedKerberoast.py impacket
git_tool passthecert https://github.com/AlmondOffSec/PassTheCert Python/passthecert.py impacket pyOpenSSL
git_tool docker-registry-grabber https://github.com/Syzik/DockerRegistryGrabber drg.py requests
git_tool evtx2json https://github.com/Silv3rHorn/evtx2json evtx2json.py python-evtx

# --- 3. jwt_tool: upstream ships requirements only (termcolor cprint pycryptodomex requests ratelimit)
if have jwt-tool; then
    skipped+=("jwt-tool")
else
    d="$TOOLS_ROOT/jwt-tool"
    [[ -d "$d" ]] || git clone -q --depth 1 https://github.com/ticarpi/jwt_tool "$d" || true
    if [[ -d "$d" ]]; then
        python3 -m venv "$d/.venv" 2>/dev/null || true
        "$d/.venv/bin/pip" -q install termcolor cprint pycryptodomex requests ratelimit >/dev/null 2>&1 || true
        printf '#!/bin/sh\nexec %s/.venv/bin/python %s/jwt_tool.py "$@"\n' "$d" "$d" > /usr/local/bin/jwt-tool
        chmod 0755 /usr/local/bin/jwt-tool
        have jwt-tool && installed+=("jwt-tool") || failed+=("jwt-tool (wrapper)")
    else
        failed+=("jwt-tool (clone)")
    fi
fi

# --- 4. username-anarchy: upstream ships a prebuilt Ruby script
if have username-anarchy; then
    skipped+=("username-anarchy")
else
    d="$TOOLS_ROOT/username-anarchy"
    [[ -d "$d" ]] || git clone -q --depth 1 https://github.com/urbanadventurer/username-anarchy "$d" || true
    if [[ -f "$d/username-anarchy" ]]; then
        ln -sf "$d/username-anarchy" /usr/local/bin/username-anarchy
        installed+=("username-anarchy")
    else
        failed+=("username-anarchy (clone)")
    fi
fi

# --- 5. Go binary: kerbrute (module root, not cmd/kerbrute)
if have kerbrute; then
    skipped+=("kerbrute")
elif have go; then
    GOBIN=/usr/local/bin go install github.com/ropnop/kerbrute@latest \
        && installed+=("kerbrute") || failed+=("kerbrute (go install)")
else
    failed+=("kerbrute (no Go toolchain)")
fi

# --- 6. npm: asar (Electron archive CLI)
if have asar; then
    skipped+=("asar")
elif have npm; then
    npm install -g --silent asar >/dev/null 2>&1 \
        && installed+=("asar") || failed+=("asar (npm)")
else
    failed+=("asar (no npm)")
fi

# --- 7. Java jar: ysoserial via JitPack build of upstream master
if have ysoserial; then
    skipped+=("ysoserial")
elif have java && have curl; then
    mkdir -p "$TOOLS_ROOT/jars"
    if curl -fsSL --max-time 180 -o "$TOOLS_ROOT/jars/ysoserial.jar" \
        https://jitpack.io/com/github/frohoff/ysoserial/master-SNAPSHOT/ysoserial-master-SNAPSHOT.jar \
        && file "$TOOLS_ROOT/jars/ysoserial.jar" | grep -q 'Java archive'; then
        printf '#!/bin/sh\nexec java -jar %s/jars/ysoserial.jar "$@"\n' "$TOOLS_ROOT" > /usr/local/bin/ysoserial
        chmod 0755 /usr/local/bin/ysoserial
        installed+=("ysoserial")
    else
        rm -f "$TOOLS_ROOT/jars/ysoserial.jar"
        failed+=("ysoserial (jitpack)")
    fi
else
    failed+=("ysoserial (no java/curl)")
fi

# --- 8. PATH fixups for APT packages that install tools off PATH
if have memcached-tool; then
    skipped+=("memcached-tool(path)")
elif [[ -x /usr/share/memcached/scripts/memcached-tool ]]; then
    ln -sf /usr/share/memcached/scripts/memcached-tool /usr/local/bin/memcached-tool \
        && installed+=("memcached-tool(path)") || failed+=("memcached-tool (symlink)")
fi

# --- summary
printf '\nResearch pip/git tools: %s installed, %s already present, %s failed.\n' \
    "${#installed[@]}" "${#skipped[@]}" "${#failed[@]}"
(( ${#installed[@]} )) && printf 'installed: %s\n' "${installed[*]}"
(( ${#failed[@]} )) && { printf 'FAILED (kept as deferred):\n'; printf '  - %s\n' "${failed[@]}"; }
# Known deferred with reasons (verified 2026-10-31):
#   marshalsec: upstream repository unavailable (GitHub 404, JitPack 'repo not found').
#   featherduster: upstream is Python-2 syntax; not runnable on this image.
#   flask-session-cookie-manager: PyPI wheel ships no importable module (broken
#     packaging); flask-unsign covers the same Flask cookie task and works.
exit 0
