"""Run with: python3 -m unittest discover -s tests -v (no agent/model calls)."""
import json
import hashlib
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = runpy.run_path(str(ROOT / 'configure-research-skills.py'))
SKILLS = runpy.run_path(str(ROOT / 'configure-board-skills.py'))
SESSIONS = runpy.run_path(str(ROOT / 'configure-board-sessions.py'))
SOURCE = ('from __future__ import annotations\n\nclass PiDriver:\n' +
          (ROOT / 'tests/fixtures/pi-local-argv.txt').read_text() +
          '\n    def _session_dir(self, worker):\n        return worker.directory\n')


class ResearchLinks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.checkout = self.root / 'research-library'
        self.account = SimpleNamespace(pw_dir=str(self.home), pw_uid=os.getuid(), pw_gid=os.getgid())
        self.account_patch = patch.object(RESEARCH['pwd'], 'getpwnam', return_value=self.account)
        self.account_patch.start()
        self.addCleanup(self.account_patch.stop)
        self.links = [self.home / '.hermes/skills/research-library', self.home / '.pi/agent/skills/research-library']

    def git(self, *args):
        return subprocess.check_output(['git', *map(str, args)], text=True, stderr=subprocess.PIPE)

    def repository(self, path):
        path.mkdir()
        self.git('init', '-q', path)
        self.git('-C', path, 'remote', 'add', 'origin', RESEARCH['REPOSITORY'])
        skill = path / 'skills/example'
        (skill / 'references').mkdir(parents=True)
        (skill / 'scripts').mkdir()
        (skill / 'SKILL.md').write_text('---\nname: example\ndescription: Test skill\n---\nTest\n')
        (skill / 'references/guide.md').write_text('Supporting reference\n')
        (skill / 'scripts/example.py').write_text('print("never executed")\n')

    def configure(self, **kwargs):
        return RESEARCH['configure']('selected-user', checkout=self.checkout, **kwargs)

    def test_first_clone_links_support_files_and_preserves_account_content(self):
        upstream = self.root / 'upstream'
        self.repository(upstream)
        self.git('-C', upstream, 'add', 'skills')
        self.git('-C', upstream, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
        existing = self.home / '.hermes/skills/existing'
        existing.mkdir(parents=True)
        (existing / 'SKILL.md').write_text('Existing content')
        globals_ = RESEARCH['configure'].__globals__
        previous_umask = os.umask(0o077)
        try:
            with patch.dict(globals_, REPOSITORY=str(upstream)):
                self.configure(apply=True)
                (self.checkout / 'local-edit').write_text('keep')
                self.configure(apply=True)
        finally:
            os.umask(previous_umask)
        self.assertEqual((existing / 'SKILL.md').read_text(), 'Existing content')
        self.assertEqual((self.checkout / 'local-edit').read_text(), 'keep')
        self.assertEqual(self.checkout.stat().st_mode & 0o777, 0o755)
        for link in self.links:
            self.assertEqual(os.readlink(link), str(self.checkout / 'skills'))
            self.assertEqual(link.lstat().st_uid, self.account.pw_uid)
            for relative in ['example/SKILL.md', 'example/references/guide.md', 'example/scripts/example.py']:
                self.assertTrue((link / relative).samefile(self.checkout / 'skills' / relative))
        (self.checkout / 'skills/example/references/guide.md').write_text('Updated later')
        self.assertEqual((self.links[1] / 'example/references/guide.md').read_text(), 'Updated later')

    def test_dry_run_does_not_clone_or_create_links(self):
        self.configure()
        self.assertFalse(self.checkout.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_conflict_on_second_link_prevents_any_changes(self):
        self.links[1].mkdir(parents=True)
        (self.links[1] / 'keep').write_text('existing')
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.configure(apply=True)
        self.assertFalse(self.checkout.exists())
        self.assertFalse(self.links[0].parent.exists())
        self.assertEqual((self.links[1] / 'keep').read_text(), 'existing')

    def test_dangling_conflict_and_symlink_parent_are_refused(self):
        self.links[0].parent.mkdir(parents=True)
        self.links[0].symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.configure(apply=True)
        self.links[0].unlink()
        (self.home / '.pi').symlink_to(self.root / 'other')
        with self.assertRaisesRegex(ValueError, 'parent'):
            self.configure(apply=True)
        self.assertFalse(self.checkout.exists())

    def test_existing_checkout_is_not_pulled_and_custom_hermes_home_is_used(self):
        self.repository(self.checkout)
        self.git('-C', self.checkout, 'config', 'remote.origin.url', RESEARCH['REPOSITORY'])
        custom = self.home / 'custom-hermes'
        self.configure(hermes_home=custom, apply=True)
        self.assertTrue((custom / 'skills/research-library').is_symlink())
        self.assertFalse(self.links[0].exists())
        self.assertTrue(self.links[1].is_symlink())
        self.assertEqual((self.home / '.pi').stat().st_mode & 0o777, 0o700)

    def test_foreign_checkout_and_checkout_symlink_are_refused(self):
        self.repository(self.checkout)
        self.git('-C', self.checkout, 'remote', 'set-url', 'origin', 'https://example.invalid/other.git')
        with self.assertRaisesRegex(ValueError, 'different Git origin'):
            self.configure(apply=True)
        real = self.root / 'real'
        self.checkout.rename(real)
        self.checkout.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'unmanaged'):
            self.configure(apply=True)
        self.assertFalse(self.links[0].parent.exists())


class BoardIntegration(unittest.TestCase):
    def test_both_patch_orders_are_identical_and_idempotent(self):
        skills, sessions = SKILLS['patch_source'], SESSIONS['patch_source']
        self.assertEqual(skills(sessions(SOURCE)), sessions(skills(SOURCE)))
        for source in [SOURCE, skills(SOURCE), sessions(SOURCE), sessions(skills(SOURCE))]:
            with self.subTest(source=source):
                self.assertEqual(skills(skills(source)), skills(source))
                self.assertEqual(sessions(sessions(source)), sessions(source))
                self.assertIn('"--no-skills"', skills(source))
                self.assertEqual(skills(source).count('"--skill"'), 1)

    def test_argument_changes_preserve_sessions_tools_and_literal_prompts(self):
        worker = SimpleNamespace(directory='/tmp/fallback sessions')
        for source in [SOURCE, SESSIONS['patch_source'](SOURCE)]:
            original, updated = {}, {}
            exec(source, original)
            exec(SKILLS['patch_source'](source), updated)
            for session in [None, 'existing-session']:
                before = original['PiDriver']()._local_argv(worker, 'prompt with $literal quotes "', session)
                after = updated['PiDriver']()._local_argv(worker, 'prompt with $literal quotes "', session)
                index = after.index('--skill')
                self.assertEqual(after[index:index + 2], ['--skill', '/opt/research-library/skills'])
                self.assertEqual(after[:index] + after[index + 2:], before)

    def test_unknown_methods_remain_rejected_by_both_helpers(self):
        for source in [SOURCE, SKILLS['patch_source'](SOURCE), SESSIONS['patch_source'](SOURCE),
                       SESSIONS['patch_source'](SKILLS['patch_source'](SOURCE))]:
            bad = source.replace('"read,write,edit,bash,grep,find,ls"', '"bash"')
            for helper in [SKILLS, SESSIONS]:
                with self.assertRaisesRegex(ValueError, 'changed'):
                    helper['patch_source'](bad)
        wrong = SKILLS['patch_source'](SOURCE).replace('/opt/research-library/skills', '/other/skills')
        for helper in [SKILLS, SESSIONS]:
            with self.assertRaises(ValueError):
                helper['patch_source'](wrong)

    def test_apply_backs_up_preserves_metadata_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'board/src/board/dispatcher/workers/adapters/pi.py'
            path.parent.mkdir(parents=True)
            source = SOURCE + '\n# unrelated customization\n'
            path.write_text(source)
            path.chmod(0o640)
            self.assertTrue(SKILLS['configure'](root))
            self.assertEqual(path.read_text(), source)
            self.assertTrue(SKILLS['configure'](root, apply=True))
            self.assertFalse(SKILLS['configure'](root, apply=True))
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertTrue(path.read_text().endswith('# unrelated customization\n'))
            backups = list(path.parent.glob('pi.py.before-research-skills-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), source)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            real = path.with_suffix('.real')
            path.rename(real)
            path.symlink_to(real)
            with self.assertRaisesRegex(ValueError, 'symlinked'):
                SKILLS['configure'](root, apply=True)

    def test_session_shell_wrapper_keeps_project_directory_and_skill_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / 'fake_pi.py'
            fake.write_text('import json,sys; print(json.dumps(sys.argv[1:]))\n')
            namespace = {}
            exec(SESSIONS['patch_source'](SKILLS['patch_source'](SOURCE)), namespace)
            argv = namespace['PiDriver']()._local_argv(SimpleNamespace(directory=str(root / 'fallback')), 'PROMPT', 'session-123')
            argv[2] = argv[2].replace('exec pi ', 'exec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(fake)) + ' ')
            env = dict(os.environ, BOARD_PROJECT_DIR=str(root / 'project space'))
            result = subprocess.run(argv, env=env, capture_output=True, text=True, check=True, timeout=10)
            actual = json.loads(result.stdout)
            self.assertEqual(actual.count('--session-dir'), 1)
            self.assertEqual(actual[actual.index('--session-dir') + 1], str(root / 'project space/sessions/pi'))
            self.assertEqual(actual[actual.index('--skill') + 1], '/opt/research-library/skills')
            self.assertIn('--no-skills', actual)
            self.assertEqual(actual[actual.index('--session') + 1], 'session-123')
            self.assertEqual((root / 'project space/sessions/pi').stat().st_mode & 0o777, 0o700)

    def test_conflicting_backup_leaves_adapter_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'board/src/board/dispatcher/workers/adapters/pi.py'
            path.parent.mkdir(parents=True)
            path.write_text(SOURCE)
            backup = path.with_name(path.name + '.before-research-skills-' + hashlib.sha256(SOURCE.encode()).hexdigest()[:12])
            backup.write_text('unrelated backup')
            with self.assertRaisesRegex(ValueError, 'conflicting adapter backup'):
                SKILLS['configure'](root, apply=True)
            self.assertEqual(path.read_text(), SOURCE)
            self.assertEqual(backup.read_text(), 'unrelated backup')

    def test_core_stages_do_not_provision_research_library(self):
        for stage in ['stage3.sh', 'stage5.sh']:
            result = subprocess.run(['bash', str(ROOT / stage), '--dry-run'], capture_output=True, text=True, check=True)
            self.assertNotIn('/opt/research-library', result.stdout)
            source = (ROOT / stage).read_text()
            self.assertNotIn('configure-research-skills.py', source)
            self.assertNotIn('configure-board-skills.py', source)
        self.assertIn('configure-board-sessions.py', (ROOT / 'stage5.sh').read_text())

    def test_standalone_installer_describes_library_and_agent_integration(self):
        result = subprocess.run(['bash', str(ROOT / 'install-offensive-tools.sh'), '--dry-run'],
                                capture_output=True, text=True, check=True)
        self.assertIn('/opt/research-library', result.stdout)
        self.assertIn('Hermes and Pi', result.stdout)


if __name__ == '__main__':
    unittest.main()
