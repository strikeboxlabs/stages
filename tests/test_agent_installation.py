"""Non-mutating installer planning checks; no agent/model or package execution."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HermesDownload(unittest.TestCase):
    def run_download(self, mode):
        source = (ROOT / 'stage3.sh').read_text()
        start = source.index('hermes_installer_url=')
        end = source.index('\nif "$dry_run";', start)
        fixture = r'''
hermes_root=/unused
calls=0
curl() {
    calls=$((calls + 1))
    local output
    while (( $# )); do
        if [[ "$1" == --output ]]; then output=$2; shift; fi
        shift
    done
    printf 'echo PARTIAL_MUST_NOT_RUN\n' > "$output"
    if [[ "$MODE" == fail || "$calls" == 1 ]]; then return 22; fi
    if [[ "$MODE" == invalid ]]; then printf 'if\n' > "$output"; return 0; fi
    printf 'echo INSTALLER_RAN "$@"\n' > "$output"
}
as_agent() { "$@"; }
install_hermes
'''
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ['bash', '-c', source[start:end] + fixture],
                env={**os.environ, 'TMPDIR': tmp, 'MODE': mode},
                capture_output=True, text=True, timeout=10)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            return result

    def test_failed_primary_uses_official_fallback(self):
        result = self.run_download('fallback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('raw.githubusercontent.com/NousResearch/hermes-agent', result.stdout)
        self.assertIn('INSTALLER_RAN --skip-setup', result.stdout)
        self.assertNotIn('PARTIAL_MUST_NOT_RUN', result.stdout)

    def test_failed_or_invalid_download_is_never_executed(self):
        for mode in ['fail', 'invalid']:
            with self.subTest(mode=mode):
                result = self.run_download(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('INSTALLER_RAN', result.stdout)
                self.assertNotIn('PARTIAL_MUST_NOT_RUN', result.stdout)


@unittest.skipUnless(os.name == 'posix', 'Stage shell planning fixtures require Linux')
class PiInstallationPlan(unittest.TestCase):
    def plan(self, *args, environment=None):
        env = dict(os.environ)
        env.pop('PI_VERSION', None)
        env.update(environment or {})
        return subprocess.run(['bash', str(ROOT / 'stage3.sh'), '--dry-run', *args],
                              env=env, capture_output=True, text=True, timeout=10)

    def test_default_is_the_supported_exact_pi_release(self):
        result = self.plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('@earendil-works/pi-coding-agent@1.3.0', result.stdout)
        self.assertNotIn('@earendil-works/pi-coding-agent@latest', result.stdout)
        self.assertIn('22.19+', result.stdout)

    def test_explicit_version_overrides_environment_default(self):
        result = self.plan('--pi-version', '1.4.2', environment={'PI_VERSION': '1.3.1'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('@earendil-works/pi-coding-agent@1.4.2', result.stdout)
        self.assertNotIn('@earendil-works/pi-coding-agent@1.3.1', result.stdout)

    def test_unpinned_and_shell_shaped_versions_are_refused(self):
        for value in ['latest', '^1.3.0', '1.3', '1.3.0;echo unsafe', '--help']:
            with self.subTest(value=value):
                result = self.plan('--pi-version', value)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('npm install', result.stdout)


if __name__ == '__main__':
    unittest.main()
