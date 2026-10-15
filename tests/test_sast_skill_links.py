"""Exercise SAST links and Board integration in temporary directories."""
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LINKS = runpy.run_path(str(ROOT / 'configure-sast-skills.py'))
BOARD = runpy.run_path(str(ROOT / 'configure-board-skills.py'))
SESSIONS = runpy.run_path(str(ROOT / 'configure-board-sessions.py'))


class SastLinks(unittest.TestCase):
    def test_links_are_idempotent_and_conflicts_prevent_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'checkout'
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

    def test_board_sast_and_transcripts_work_in_both_orders(self):
        # Reuse the known adapter fixture used by the existing integration tests.
        import test_research_skills
        source = test_research_skills.SOURCE
        skills = lambda text: BOARD['patch_source'](text, '/opt/sast-tools/skills')
        sessions = SESSIONS['patch_source']
        self.assertEqual(skills(sessions(source)), sessions(skills(source)))
        result = sessions(skills(source))
        self.assertEqual(skills(result), result)
        self.assertIn('/opt/sast-tools/skills', result)
        self.assertNotIn('/opt/research-library/skills', result)
        both = BOARD['patch_source'](result)
        self.assertEqual(skills(both), both)
        self.assertEqual(sessions(both), both)
