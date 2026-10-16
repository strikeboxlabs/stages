"""Real Board CLI validation against temporary, harmless catalog/config fixtures.

Run with the Python environment that has native-profile Board installed. No daemon,
Pi process, model request, source scan, or host installer is started.
"""
import importlib.util
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SKILLS = runpy.run_path(str(ROOT / 'configure-board-skills.py'))
SESSIONS = runpy.run_path(str(ROOT / 'configure-board-sessions.py'))


@unittest.skipUnless(importlib.util.find_spec('board') is not None,
                     'Install native-profile Board in the test Python environment')
class NativeBoardCliIntegration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='stages-native-cli-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        # Board exposes a console entrypoint; there is no board.__main__ module.
        self.command = [sys.executable, '-B', '-c', 'from board.cli import main; main()']
        self.config = self.root / 'dispatch.yaml'
        self.backups = self.root / 'private-backups'
        import yaml
        document = {
            'server': 'http://127.0.0.1:8765',
            'runtime': {'execution': 'local', 'worker_healthcheck': 'disabled',
                        'max_workers': 3, 'max_running_projects': 1,
                        'max_project_workers': 3, 'interval': 3, 'healthcheck_timeout': 15,
                        'prompt_group': 'default'},
            'tasks': {'bootstrap': {'timeout': 120, 'conclude_timeout': 30},
                      'reason': {'timeout': 60},
                      'explore': {'timeout': 300, 'conclude_timeout': 60}},
            'local': {'evidence_root': str(self.root / 'unused-runs'), 'completed_action': 'keep'},
            'workers': [{'name': 'fixture-pi', 'type': 'pi',
                         'task_types': ['bootstrap', 'reason', 'explore'], 'max_running': 1, 'priority': 0}],
            'common_env': {'FIXTURE_VALUE': 'literal $! unchanged'},
            'skills': {'enabled': False, 'catalogs': [], 'default_profiles': ['general'],
                       'max_metadata_tokens': 6000},
        }
        self.config.write_text('# preserve fixture heading\n' + yaml.safe_dump(document, sort_keys=False))

    def library(self, library_id, name, tags):
        root = self.root / library_id
        package = root / 'skills' / name
        (package / 'references').mkdir(parents=True)
        (package / 'SKILL.md').write_text('---\nname: ' + name + '\ndescription: Inspect supplied fixture text.\n---\nRead the fixture reference only.\n')
        (package / 'references' / 'fixture.md').write_text('Harmless local fixture; no commands.\n')
        catalog = root / 'catalog.json'
        catalog.write_text(json.dumps({'schema_version': 1, 'library_id': library_id,
            'skills': [{'id': name, 'path': 'skills/' + name, 'tags': tags, 'requires': []}]}))
        return catalog

    def cli(self, subcommand='doctor'):
        result = subprocess.run(self.command + ['skills', subcommand, '--config', str(self.config), '--json'],
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertTrue(result.stdout.strip(), result.stderr)
        return result, json.loads(result.stdout)

    def test_disabled_skills_validate_without_worker_or_model(self):
        result, report = self.cli()
        self.assertEqual(result.returncode, 0, report)
        self.assertFalse(report['enabled'])
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['profiles'], [])
        self.assertIsInstance(report['warnings'], list)
        self.assertFalse((self.root / 'unused-runs').exists())

    def test_real_cli_validates_both_catalog_registrations_and_session_migration(self):
        data = self.library('fixture-research', 'fixture-data', ['task:data-inspection'])
        source = self.library('fixture-sast', 'fixture-source', ['task:source-review', 'language:python'])
        kwargs = {'apply': True, 'board_command': self.command, 'backup_dir': self.backups}
        self.assertTrue(SKILLS['configure'](self.root, catalog=data, **kwargs))
        self.assertTrue(SKILLS['configure'](self.root, catalog=source, **kwargs))
        self.assertTrue(SESSIONS['configure'](self.root, **kwargs))
        saved = self.config.read_text()
        self.assertFalse(SKILLS['configure'](self.root, catalog=data, **kwargs))
        self.assertFalse(SESSIONS['configure'](self.root, **kwargs))
        self.assertEqual(self.config.read_text(), saved)
        self.assertTrue(saved.startswith('# preserve fixture heading\n'))
        import yaml
        document = yaml.safe_load(saved)
        self.assertEqual(document['common_env']['FIXTURE_VALUE'], 'literal $! unchanged')
        self.assertEqual(document['local']['pi_session_storage'], 'project')
        self.assertEqual(document['skills']['catalogs'], [str(data), str(source)])
        result, report = self.cli()
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['catalog_skill_count'], 2)
        profiles = {item['id']: item for item in report['profiles']}
        self.assertEqual(profiles['general']['skills'], 1)
        self.assertEqual(profiles['source-review']['skills'], 1)
        self.assertEqual(profiles['python']['skills'], 1)
        result, summaries = self.cli('profiles')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({item['id'] for item in summaries}, set(profiles))
        self.assertTrue(list(self.backups.glob('dispatch.yaml.before-native-skills-*')))
        self.assertFalse((self.root / 'unused-runs').exists())
        self.assertFalse((self.root / 'sessions').exists())

    def test_real_doctor_rejects_invalid_catalog_before_config_publication(self):
        catalog = self.library('fixture-invalid', 'fixture-data', ['task:data-inspection'])
        catalog.write_text('{"schema_version":99,"library_id":"fixture-invalid","skills":[]}')
        original = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'doctor rejected'):
            SKILLS['configure'](self.root, apply=True, catalog=catalog,
                               board_command=self.command, backup_dir=self.backups)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(self.backups.exists())
        self.assertFalse(list(self.root.glob('.dispatch-check-*')))

    def test_real_doctor_detects_missing_package_and_keeps_disabled_config(self):
        catalog = self.library('fixture-missing', 'fixture-data', ['task:data-inspection'])
        (catalog.parent / 'skills/fixture-data/SKILL.md').unlink()
        original = self.config.read_bytes()
        with self.assertRaisesRegex(ValueError, 'doctor rejected'):
            SKILLS['configure'](self.root, apply=True, catalog=catalog,
                               board_command=self.command, backup_dir=self.backups)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse(self.backups.exists())


if __name__ == '__main__':
    unittest.main()
