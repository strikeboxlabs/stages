"""Config migration tests use local fixtures and mocked doctor calls only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import board_config
SKILLS = runpy.run_path(str(ROOT / 'configure-board-skills.py'))
SESSIONS = runpy.run_path(str(ROOT / 'configure-board-sessions.py'))


class NativeBoardConfig(unittest.TestCase):
    def test_catalog_registration_preserves_user_choices_and_is_idempotent(self):
        mapping = {'enabled': False, 'catalogs': ['/existing/catalog.json'],
                   'default_profiles': ['python'], 'profiles': {'custom': {'include': ['x']}},
                   'max_metadata_tokens': 1234, 'future': {'keep': True}}
        original = deepcopy(mapping)
        for _ in range(2):
            SKILLS['register_catalog'](mapping, '/new/catalog.json')
        self.assertTrue(mapping['enabled'])
        self.assertEqual(mapping['catalogs'], ['/existing/catalog.json', '/new/catalog.json'])
        for field in ['default_profiles', 'profiles', 'max_metadata_tokens', 'future']:
            self.assertEqual(mapping[field], original[field])

    def test_only_selected_yaml_section_changes(self):
        source = ('# keep heading\nserver: http://localhost:8765\n'
                  'common_env:\n  DUMMY_TOKEN: "literal $! example"\n'
                  '# keep worker heading\nworkers:\n  - name: local-pi\n    type: pi\n'
                  'skills:\n  catalogs: []\n  default_profiles: [python]\n'
                  '# keep following heading\nlocal:\n  evidence_root: /data/existing\n')
        result = board_config.change_section(source, 'skills',
                 lambda mapping: SKILLS['register_catalog'](mapping, '/new/catalog.json'))
        self.assertEqual(result.split('skills:')[0], source.split('skills:')[0])
        self.assertTrue(result.endswith('# keep following heading\nlocal:\n  evidence_root: /data/existing\n'))
        self.assertIn('default_profiles:', result)
        self.assertIn('- python', result)

    def test_new_section_and_null_section_are_supported(self):
        for source in ['server: x\nlocal:\n  evidence_root: /keep\n', 'server: x\nskills: null\nlocal: {}\n']:
            result = board_config.change_section(source, 'skills',
                     lambda mapping: SKILLS['register_catalog'](mapping, '/catalog.json'))
            import yaml
            value = yaml.safe_load(result)
            self.assertTrue(value['skills']['enabled'])
            self.assertEqual(value['server'], 'x')
            self.assertEqual(value['local'], yaml.safe_load(source)['local'])

    def test_duplicate_keys_and_non_mapping_sections_fail(self):
        for source in ['skills: {}\nskills: {}\n', 'skills: wrong\n']:
            with self.assertRaises(ValueError):
                board_config.change_section(source, 'skills', lambda mapping: mapping.update(enabled=True))

    def test_validation_failure_leaves_config_and_backups_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dispatch.yaml'
            original = 'server: x\nlocal: {}\n'
            path.write_text(original)
            def reject(candidate):
                self.assertIn('pi_session_storage', candidate.read_text())
                raise ValueError('fixture rejection')
            with self.assertRaisesRegex(ValueError, 'fixture rejection'):
                board_config.update_config(path, 'local',
                    lambda mapping: mapping.update(pi_session_storage='project'), apply=True, validate=reject)
            self.assertEqual(path.read_text(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_apply_is_idempotent_and_retains_explicit_ephemeral_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'dispatch.yaml'
            original = '# keep\nserver: x\nlocal:\n  evidence_root: /keep\n'
            path.write_text(original)
            def mutate(mapping):
                mapping.setdefault('pi_session_storage', 'project')
            self.assertTrue(board_config.update_config(path, 'local', mutate, apply=True))
            saved = path.read_text()
            self.assertFalse(board_config.update_config(path, 'local', mutate, apply=True))
            self.assertEqual(path.read_text(), saved)
            backups = list(root.glob('*.before-native-skills-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            if os.name == 'posix':
                self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            explicit = 'local:\n  pi_session_storage: ephemeral\n'
            self.assertEqual(board_config.change_section(explicit, 'local', mutate), explicit)

    def test_private_backup_directory_can_live_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / 'checkout'
            checkout.mkdir()
            path = checkout / 'dispatch.yaml'
            original = 'local: {}\ncommon_env:\n  EXAMPLE: fixture-only\n'
            path.write_text(original)
            backups = root / 'private-backups'
            board_config.update_config(path, 'local',
                lambda mapping: mapping.update(pi_session_storage='project'),
                apply=True, backup_dir=backups)
            files = list(backups.iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_text(), original)
            self.assertFalse(list(checkout.glob('*.before-native-skills-*')))
            if os.name == 'posix':
                self.assertEqual(backups.stat().st_mode & 0o777, 0o700)

    def test_concurrent_edit_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dispatch.yaml'
            path.write_text('local: {}\n')
            def concurrent(candidate):
                path.write_text('# other editor\nlocal: {}\n')
            with self.assertRaisesRegex(ValueError, 'concurrent'):
                board_config.update_config(path, 'local',
                    lambda mapping: mapping.update(pi_session_storage='project'), apply=True, validate=concurrent)
            self.assertEqual(path.read_text(), '# other editor\nlocal: {}\n')
            self.assertFalse(list(path.parent.glob('*.before-native-skills-*')))

    def test_unsupported_board_does_not_touch_sources(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=2, stdout='', stderr='No such command')
        with self.assertRaisesRegex(ValueError, 'Upgrade Board first'):
            board_config.require_native('/fake/board', run)
        self.assertEqual(calls, [['/fake/board', 'skills', 'doctor', '--help']])

    def test_doctor_uses_dispatcher_identity_and_no_model_command(self):
        from types import ModuleType
        module = ModuleType('pwd')
        module.getpwnam = lambda user: SimpleNamespace(pw_dir='/home/worker', pw_uid=123, pw_gid=456)
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout='{"errors":[],"warnings":[],"profiles":[]}', stderr='')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'candidate.yaml'
            config.write_text('skills: {}\n')
            with patch.dict(sys.modules, pwd=module), patch.object(board_config.os, 'geteuid', return_value=123, create=True):
                board_config.doctor('/fake/board', config, 'worker', 'board', run)
        argv = calls[0]
        self.assertEqual(argv[:6], ['runuser', '-u', 'worker', '-g', 'board', '--'])
        self.assertIn('HOME=/home/worker', argv)
        self.assertIn('PI_CODING_AGENT_DIR', argv)
        self.assertEqual(argv[-5:], ['skills', 'doctor', '--config', str(config), '--json'])
        self.assertNotIn('-p', argv)

    def test_skill_registration_checks_complete_candidate_before_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / 'catalog.json'
            catalog.write_text('{}')
            config = root / 'dispatch.yaml'
            config.write_text('server: unchanged\nworkers: []\n')
            checked = []
            def validate(board_bin, candidate, user, group):
                import yaml
                document = yaml.safe_load(candidate.read_text())
                self.assertEqual(document['skills']['catalogs'], [str(catalog)])
                self.assertEqual((user, group), ('worker', 'board'))
                checked.append(candidate)
                self.assertNotIn('skills:', config.read_text())
            configure = SKILLS['configure']
            with patch.dict(configure.__globals__, require_native=lambda path: None, doctor=validate):
                self.assertTrue(configure(root, apply=True, catalog=catalog, user='worker', group='board', backup_dir=root / 'backups'))
            self.assertEqual(len(checked), 1)
            self.assertIn('skills:', config.read_text())
            self.assertFalse((root / 'board/src').exists())

    def test_session_and_skill_helpers_only_target_config(self):
        for name in ['configure-board-skills.py', 'configure-board-sessions.py', 'board_config.py']:
            source = (ROOT / name).read_text()
            self.assertNotIn('workers/adapters/pi.py', source)
            self.assertNotIn('patch_source', source)
            self.assertNotIn('models.json', source)
            self.assertNotIn('auth.json', source)


if __name__ == '__main__':
    unittest.main()
