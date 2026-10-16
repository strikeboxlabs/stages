"""Non-mutating installer planning checks; no agent/model or package execution."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


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
