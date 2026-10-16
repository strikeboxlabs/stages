"""Exercise installer preflight without network calls or system writes."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'install-sast-tools.sh'
TOOLS = ('semgrep', 'codeql', 'bandit', 'gosec', 'brakeman', 'cppcheck', 'trivy')


class SastInstaller(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / 'catalog.json').write_text(json.dumps({'schema_version': 1, 'library_id': 'sast', 'skills': [
            {'id': tool, 'path': 'skills/' + tool, 'tags': [], 'requires': []} for tool in TOOLS]}))
        for tool in TOOLS:
            directory = self.repo / 'skills' / tool
            directory.mkdir(parents=True)
            (directory / 'SKILL.md').write_text('Test catalogue entry\n')

    def run_installer(self, *args):
        return subprocess.run(['bash', str(SCRIPT), '--repo', str(self.repo), *args],
                              capture_output=True, text=True, timeout=10)

    def test_dry_run_covers_catalogue_without_changing_it(self):
        before = sorted(p.relative_to(self.repo) for p in self.repo.rglob('*'))
        result = self.run_installer('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        for tool in TOOLS:
            self.assertIn(tool, result.stdout.lower())
        self.assertEqual(before, sorted(p.relative_to(self.repo) for p in self.repo.rglob('*')))

    def test_unknown_skill_is_not_silently_omitted(self):
        directory = self.repo / 'skills/new-scanner'
        directory.mkdir()
        (directory / 'SKILL.md').touch()
        result = self.run_installer('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unrecognized skill new-scanner', result.stderr)

    def test_incomplete_catalogue_is_rejected(self):
        (self.repo / 'skills/codeql/SKILL.md').unlink()
        result = self.run_installer('--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing', result.stderr)
        self.assertIn('codeql', result.stderr)

    def test_invalid_option_fails_before_installation(self):
        result = self.run_installer('--oops')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unknown', result.stderr)
        self.assertIn('--oops', result.stderr)
