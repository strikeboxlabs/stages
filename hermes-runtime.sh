# Shared by stages 3 and 4. Call after selecting target_user and target_home.
init_hermes_runtime() {
    hermes_home=${HERMES_HOME:-$target_home/.hermes}
    hermes_root=${HERMES_INSTALL_DIR:-$hermes_home/hermes-agent}
    hermes_cli=''
    for candidate in "$hermes_root/.hermes/bin/hermes" "$target_home/.local/bin/hermes" /usr/local/bin/hermes; do
        if [[ -x "$candidate" ]]; then hermes_cli=$candidate; break; fi
    done
    [[ -n "$hermes_cli" ]] || die "Missing Hermes for $target_user. Run stage3.sh --user $target_user."
}

as_agent() {
    runuser -u "$target_user" -- env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV \
        -u HERMES_PROFILE HOME="$target_home" HERMES_HOME="$hermes_home" \
        PATH="$target_home/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" "$@"
}

# New Hermes uses a managed runtime, not a fixed venv/bin/python. Ask its
# launcher for its interpreter, then load bootstrap to select and lease the
# current dependencies before executing our helper file.
run_hermes_python() {
    local runtime candidate venv
    local -a runtime_command
    if runtime=$(as_agent "$hermes_cli" --print-runtime-command --module runpy 2>/dev/null); then
        python3 -c 'import json, sys
argv = json.load(sys.stdin)
if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and "\0" not in x for x in argv):
    raise ValueError("Invalid Hermes runtime command")' <<< "$runtime" || die 'Invalid Hermes runtime response.'
        # Parse JSON via NUL delimiters; never eval the returned command.
        mapfile -d '' -t runtime_command < <(python3 -c 'import json, sys
for arg in json.load(sys.stdin):
    sys.stdout.buffer.write(arg.encode() + b"\0")' <<< "$runtime")
        as_agent "${runtime_command[0]}" -I -c '
import runpy, sys
sys.path.insert(0, sys.argv.pop(1))
import hermes_bootstrap
script = sys.argv.pop(1)
sys.argv[0] = script
runpy.run_path(script, run_name="__main__")
' "$hermes_root" "$@"
        return
    fi
    for candidate in "$hermes_root" /usr/local/lib/hermes-agent; do
        for venv in venv .venv; do
            if [[ -x "$candidate/$venv/bin/python" ]]; then
                as_agent "$candidate/$venv/bin/python" "$@"
                return
            fi
        done
    done
    die 'Cannot resolve the Hermes runtime. Rerun stage3.sh.'
}
