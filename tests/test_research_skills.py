"""Run with: python3 -m unittest discover -s tests -v (no agent/model calls)."""
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = runpy.run_path(str(ROOT / 'configure-research-skills.py'))


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
        (path / 'catalog.json').write_text('{"schema_version":1,"library_id":"research-library","skills":[]}')
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
        self.git('-C', upstream, 'add', 'skills', 'catalog.json')
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



if __name__ == '__main__':
    unittest.main()
