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

# --- 5. Go binaries (module roots; verify module path before adding)
go_tool() {  # go_tool <command> <module@version>
    local cmd=$1 module=$2
    if have "$cmd"; then skipped+=("$cmd"); return 0; fi
    if have go; then
        GOBIN=/usr/local/bin go install "$module" \
            && installed+=("$cmd") || failed+=("$cmd (go install $module)")
    else
        failed+=("$cmd (no Go toolchain)")
    fi
}
go_tool kerbrute github.com/ropnop/kerbrute@latest   # module root, not cmd/kerbrute
go_tool gau github.com/lc/gau/v2/cmd/gau@latest
go_tool waybackurls github.com/tomnomnom/waybackurls@latest

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

# --- 8. Cloud/container tooling (pipx + upstream releases)
pipx_tool scoutsuite scoutsuite
if have scout && ! have scoutsuite; then
    ln -sf "$(command -v scout)" /usr/local/bin/scoutsuite && installed+=("scoutsuite(cmd-symlink)") || failed+=("scoutsuite (symlink)")
fi
pipx_tool kube-hunter kube-hunter
release_tool() {  # release_tool <command> <versioned-url>
    local cmd=$1 url=$2
    if have "$cmd"; then skipped+=("$cmd"); return 0; fi
    if curl -fsSL --max-time 180 -o "/usr/local/bin/$cmd" "$url" && chmod 0755 "/usr/local/bin/$cmd" && have "$cmd"; then
        installed+=("$cmd")
    else
        rm -f "/usr/local/bin/$cmd"; failed+=("$cmd (release download)")
    fi
}
release_tool cdk https://github.com/cdk-team/CDK/releases/latest/download/cdk_linux_amd64
release_tool kubeletctl https://github.com/cyberark/kubeletctl/releases/latest/download/kubeletctl_linux_amd64
if have deepce; then
    skipped+=("deepce")
elif curl -fsSL --max-time 60 -o /usr/local/bin/deepce \
    https://raw.githubusercontent.com/stealthcopter/deepce/master/deepce.sh \
    && chmod 0755 /usr/local/bin/deepce; then
    installed+=("deepce")
else
    rm -f /usr/local/bin/deepce; failed+=("deepce (download)")
fi

# --- 9. Injection/API/stego/symbolic tooling
git_tool tplmap https://github.com/epinna/tplmap tplmap.py
go_tool kiterunner github.com/assetnote/kiterunner/cmd/kiterunner@latest
if have zsteg; then
    skipped+=("zsteg")
elif have gem; then
    gem install zsteg -q >/dev/null 2>&1 && have zsteg && installed+=("zsteg") || failed+=("zsteg (gem)")
else
    failed+=("zsteg (no ruby)")
fi
# angr is a library: dedicated venv invoked by absolute interpreter path
if /opt/tools/angr/.venv/bin/python -c 'import angr' >/dev/null 2>&1; then
    skipped+=("angr(venv)")
else
    python3 -m venv /opt/tools/angr/.venv 2>/dev/null \
        && /opt/tools/angr/.venv/bin/pip -q install angr unicorn >/dev/null 2>&1 \
        && /opt/tools/angr/.venv/bin/python -c 'import angr' >/dev/null 2>&1 \
        && installed+=("angr(venv)") || failed+=("angr (venv build)")
fi

# --- 10. PATH fixups for APT packages that install tools off PATH
if have memcached-tool; then
    skipped+=("memcached-tool(path)")
elif [[ -x /usr/share/memcached/scripts/memcached-tool ]]; then
    ln -sf /usr/share/memcached/scripts/memcached-tool /usr/local/bin/memcached-tool \
        && installed+=("memcached-tool(path)") || failed+=("memcached-tool (symlink)")
fi

# --- 11. REA reverse-engineering orchestration (npm CLI + /opt/rea checkout)
if have rea; then
    skipped+=("rea")
else
    if have node && have npm; then
        npm install --global --allow-scripts=frida rea-agents >/dev/null 2>&1 \
            && have rea && installed+=("rea") || failed+=("rea (npm)")
    else
        failed+=("rea (no node/npm)")
    fi
fi
if [[ -d /opt/rea/.git ]]; then
    skipped+=("rea-checkout")
else
    git clone -q --depth 1 https://github.com/morluto/rea /opt/rea \
        && installed+=("rea-checkout") || failed+=("rea-checkout (clone)")
fi
# Empirical: node 24.21.0 runs rea 6.7.0 despite the documented 24.22.1 floor.
# Ghidra/JADX providers arrive through the APT resolver via their skill entries.
# MCP: stdio server via the installed CLI (`rea mcp`; `rea mcp doctor` validates).
# Interactive registration into Hermes for the staging account:
rea_user="${target_user:-root}"
rea_home=$(getent passwd "$rea_user" | cut -d: -f6)
if [[ -f "$rea_home/.hermes/config.yaml" ]] && command -v hermes >/dev/null 2>&1; then
    if [[ -t 0 ]]; then
        printf 'Register the REA MCP server into Hermes for %s? [y/N] ' "$rea_user"
        read -r rea_answer
        case "$rea_answer" in
            y|Y|yes|YES)
                if runuser -u "$rea_user" -- hermes config set mcp_servers.rea.command /usr/local/bin/rea \
                   && runuser -u "$rea_user" -- hermes config set mcp_servers.rea.args '["mcp"]'; then
                    printf 'REA MCP registered for %s; restart Hermes to load it.\n' "$rea_user"
                else
                    printf 'Warning: Hermes MCP registration failed; register manually with: hermes config set mcp_servers.rea.command /usr/local/bin/rea\n'
                fi
                ;;
            *) printf 'Skipped REA MCP registration.\n' ;;
        esac
    else
        printf 'Note: non-interactive shell; register REA MCP manually with hermes config set (mcp_servers.rea.command=/usr/local/bin/rea, mcp_servers.rea.args=[mcp]).\n'
    fi
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
