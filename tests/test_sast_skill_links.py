"""Exercise SAST links and Board integration in temporary directories."""
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LINKS = runpy.run_path(str(ROOT / 'configure-sast-skills.py'))


class SastLinks(unittest.TestCase):
    def test_links_are_idempotent_and_conflicts_prevent_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'checkout'
            repo.mkdir()
            (repo / 'catalog.json').write_text('{"schema_version":1,"library_id":"sast","skills":[]}')
            skill = repo / 'skills/bandit'
            skill.mkdir(parents=True)
            (skill / 'SKILL.md').touch()
            home = root / 'home'
            home.mkdir()
            tools = root / 'tools'
            tools.mkdir()
            account = SimpleNamespace(pw_dir=str(home), pw_uid=0, pw_gid=0)
            function = LINKS['configure']
            with patch.object(function.__globals__['pwd'], 'getpwnam', return_value=account):
                conflict = home / '.pi/agent/skills/sast'
                conflict.mkdir(parents=True)
                with self.assertRaisesRegex(ValueError, 'conflicting'):
                    function(repo, 'test', tools_root=tools, apply=True)
                self.assertFalse((home / '.hermes').exists())
                self.assertFalse((tools / 'skills').exists())
                conflict.rmdir()
                # Use the test process owner even when run without root.
                import os
                account.pw_uid, account.pw_gid = os.getuid(), os.getgid()
                for _ in range(2):
                    function(repo, 'test', tools_root=tools, apply=True)
                for link in [home / '.hermes/skills/sast', conflict, tools / 'skills']:
                    self.assertEqual(link.resolve(), repo / 'skills')
                self.assertEqual((tools / 'catalog.json').resolve(), repo / 'catalog.json')


if __name__ == '__main__':
    unittest.main()
