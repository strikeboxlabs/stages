"""Harmless checkout and Linux dry-run fixtures for the unified optional installer."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = runpy.run_path(str(ROOT / 'configure-sast-checkout.py'))
TOOLS = sorted(CHECKOUT['TOOLS'])


def fixture_catalog(root):
    for name in TOOLS:
        package = root / 'skills' / name
        package.mkdir(parents=True, exist_ok=True)
        (package / 'SKILL.md').write_text('Fixture only; no commands.\n')
    data = {'schema_version': 1, 'library_id': 'sast', 'skills': [
        {'id': name, 'path': 'skills/' + name, 'tags': [], 'requires': []} for name in TOOLS]}
    (root / 'catalog.json').write_text(json.dumps(data))
    return data


class SastCheckoutPreparation(unittest.TestCase):
    def test_explicit_existing_tree_needs_no_git_and_preserves_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_catalog(root)
            (root / 'keep-edit').write_text('keep')
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            CHECKOUT['prepare'](root, existing=True, apply=True)
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
            self.assertEqual(after, before)
            with self.assertRaisesRegex(ValueError, 'unmanaged'):
                CHECKOUT['prepare'](root)

    def test_malformed_catalogs_are_rejected_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = fixture_catalog(root)
            cases = ['[]', '{', '{"schema_version":1,"schema_version":1}']
            for change in [lambda d: d.update(schema_version=True),
                           lambda d: d.update(library_id='other'),
                           lambda d: d['skills'].append(d['skills'][0].copy()),
                           lambda d: d['skills'][0].update(path='../outside'),
                           lambda d: d['skills'][0].update(tags=['duplicate', 'duplicate'])]:
                data = json.loads(json.dumps(valid)); change(data); cases.append(json.dumps(data))
            for source in cases:
                with self.subTest(source=source):
                    (root / 'catalog.json').write_text(source)
                    with self.assertRaises((ValueError, TypeError)):
                        CHECKOUT['prepare'](root, existing=True, apply=True)

    def test_missing_default_checkout_dry_run_does_not_clone_or_create(self):
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'absent'
            function = CHECKOUT['prepare']
            with patch.object(function.__globals__['subprocess'], 'run', side_effect=AssertionError('No clone in dry run')):
                function(checkout, apply=False)
            self.assertFalse(checkout.exists())


@unittest.skipUnless(os.name == 'posix', 'Unified installer shell fixtures require Linux')
class UnifiedInstallerPlanning(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'sast'
        fixture_catalog(self.repo)
        self.log = self.root / 'forbidden-mutations'
        self.environment = dict(os.environ, HERMES_HOME=str(self.root / 'hermes'))
        fake = self.root / 'fake-commands.sh'
        fake.write_text(''.join(command + '() { printf forbidden >> "$MUTATION_LOG"; return 91; }\n'
                                for command in ['apt-get', 'git', 'runuser', 'npm', 'pip', 'ln', 'chown', 'chmod']) +
                        'systemctl() { case "$1" in show) return 0 ;; is-active) return 1 ;; '
                        '*) printf forbidden >> "$MUTATION_LOG"; return 91 ;; esac; }\n')
        self.environment.update(BASH_ENV=str(fake), MUTATION_LOG=str(self.log))

    def run_installer(self, *arguments, legacy=False):
        script = ROOT / ('install-sast-tools.sh' if legacy else 'install-tools.sh')
        return subprocess.run(['bash', str(script), *arguments], env=self.environment,
                              text=True, capture_output=True, timeout=20)

    def test_selected_sast_dry_run_and_legacy_alias_are_equivalent_and_read_only(self):
        arguments = ['--sast-repo', str(self.repo), '--dry-run']
        direct = self.run_installer('--groups', 'sast', *arguments)
        alias = self.run_installer('--repo', str(self.repo), '--dry-run', legacy=True)
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(alias.returncode, 0, alias.stderr)
        self.assertEqual(direct.stdout, alias.stdout)
        self.assertIn('seven CLI versions', direct.stdout)
        self.assertFalse(self.log.exists())
        self.assertFalse((self.root / 'hermes').exists())

    def test_unknown_empty_groups_and_unknown_account_fail_without_mutations(self):
        for arguments in [('--groups', ''), ('--groups', 'unknown'), ('--groups', 'sast,unknown'),
                          ('--groups', 'sast', '--user', 'stage-fixture-no-account-8293')]:
            with self.subTest(arguments=arguments):
                result = self.run_installer(*arguments, '--dry-run')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_plain_debian_research_selection_fails_before_mutations(self):
        release = Path('/etc/os-release').read_text()
        if '\nID=kali\n' in '\n' + release:
            return  # The incompatibility only applies to non-Kali hosts.
        result = self.run_installer('--groups', 'research', '--dry-run')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires Kali', result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
