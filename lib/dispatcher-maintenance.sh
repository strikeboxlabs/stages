#!/usr/bin/env bash
# Service lifecycle for the optional installer. Source, then pause_dispatcher.
dispatcher_needs_restore=false

restore_dispatcher() {
    local rc=$?
    trap - EXIT INT TERM
    if "$dispatcher_needs_restore"; then
        printf '[install-tools] Restoring board-dispatcher.service.\n'
        if ! systemctl start board-dispatcher.service ||
            ! systemctl is-active --quiet board-dispatcher.service; then
            printf 'Error [install-tools]: Could not restore board-dispatcher.service. Inspect: journalctl -u board-dispatcher.service -n 80\n' >&2
            (( rc != 0 )) || rc=1
        fi
    fi
    exit "$rc"
}

pause_dispatcher() {
    command -v systemctl >/dev/null || return 0
    if systemctl is-active --quiet board-dispatcher.service; then
        # Arm cleanup before stopping: even a failed/interrupted stop can stop it.
        dispatcher_needs_restore=true
        trap restore_dispatcher EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM
        printf '[install-tools] Stopping board-dispatcher.service for provisioning.\n'
        systemctl stop board-dispatcher.service || return 1
        if systemctl is-active --quiet board-dispatcher.service; then
            printf 'Error [install-tools]: Dispatcher is still active after stopping it.\n' >&2
            return 1
        fi
    fi
}
