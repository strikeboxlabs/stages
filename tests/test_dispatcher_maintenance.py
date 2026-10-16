"""Exercise service cleanup with a fake systemctl; no real services or installs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DispatcherMaintenance(unittest.TestCase):
    def lifecycle(self, initial='active', body='exit 0', stop_rc=0, start_rc=0):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'calls'
            script = r'''
set -Eeuo pipefail
systemctl() {
    printf '%s\n' "$*" >> "$CALL_LOG"
    case "$1" in
        is-active) [[ "$STATE" == active ]] ;;
        stop) STATE=inactive; return "$STOP_RC" ;;
        start) [[ "$START_RC" != 0 ]] || STATE=active; return "$START_RC" ;;
        *) return 99 ;;
    esac
}
source "$HELPER"
pause_dispatcher || exit 42
'''
            result = subprocess.run(
                ['bash', '-c', script + body], capture_output=True, text=True,
                env={**os.environ, 'HELPER': str(ROOT / 'lib/dispatcher-maintenance.sh'),
                     'CALL_LOG': str(log), 'STATE': initial,
                     'STOP_RC': str(stop_rc), 'START_RC': str(start_rc)}, timeout=10)
            return result, log.read_text().splitlines()

    def test_active_service_restored_after_success_failure_and_signals(self):
        for body, expected in [('exit 0', 0), ('false', 1), ('exit 23', 23),
                               ('kill -INT $$', 130), ('kill -TERM $$', 143)]:
            with self.subTest(body=body):
                result, calls = self.lifecycle(body=body)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(calls, ['is-active --quiet board-dispatcher.service',
                                         'stop board-dispatcher.service',
                                         'is-active --quiet board-dispatcher.service',
                                         'start board-dispatcher.service',
                                         'is-active --quiet board-dispatcher.service'])

    def test_inactive_service_stays_inactive(self):
        result, calls = self.lifecycle(initial='inactive')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ['is-active --quiet board-dispatcher.service'])

    def test_failed_stop_aborts_and_attempts_restoration(self):
        result, calls = self.lifecycle(stop_rc=1, body='echo MUST_NOT_INSTALL')
        self.assertEqual(result.returncode, 42)
        self.assertNotIn('MUST_NOT_INSTALL', result.stdout)
        self.assertIn('start board-dispatcher.service', calls)

    def test_restore_failure_is_reported_and_preserves_install_failure(self):
        for install_rc in [0, 23]:
            with self.subTest(install_rc=install_rc):
                result, _ = self.lifecycle(start_rc=1, body=f'exit {install_rc}')
                self.assertEqual(result.returncode, install_rc or 1)
                self.assertIn('Could not restore', result.stderr)


if __name__ == '__main__':
    unittest.main()
