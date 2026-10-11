"""Portable setup tests: temporary homes, dummy keys, and no model/API calls."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
# JSON is valid YAML. These fixtures keep tests independent of Hermes/PyYAML.
yaml = ModuleType('hermes_yaml')
yaml.safe_load = json.loads
yaml.YAMLError = ValueError
with patch.dict(sys.modules, hermes_yaml=yaml):
    HERMES = runpy.run_path(str(ROOT / 'configure-hermes.py'))
    PI = runpy.run_path(str(ROOT / 'configure-desktop.py'))
    SHARED = sys.modules['model_providers']


class Terminal:
    def __init__(self, answers):
        self.answers = io.StringIO(answers)
        self.output = io.StringIO()

    def readline(self):
        return self.answers.readline()

    def write(self, text):
        return self.output.write(text)

    def flush(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class ModelSelection(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.hermes_home = self.home / 'hermes'
        self.hermes_home.mkdir()
        self.config = self.hermes_home / 'config.yaml'
        self.pi_root = self.home / '.pi/agent'

    def configure(self, answers, keys=('hermes-dummy', 'pi-dummy')):
        terminal = Terminal(answers)
        dependencies = {name: ModuleType(name) for name in ('fastapi', 'uvicorn', 'ptyprocess')}
        with patch.dict(os.environ, HERMES_HOME=str(self.hermes_home)), \
                patch.dict(sys.modules, dependencies), \
                patch.object(Path, 'home', return_value=self.home), \
                patch.dict(HERMES['main'].__globals__, open=lambda *args: terminal,
                           verify_pi_model=lambda *args: None), \
                patch.object(SHARED.getpass, 'getpass', side_effect=keys) as prompt, \
                redirect_stdout(io.StringIO()):
            HERMES['main']()
        return json.loads(self.config.read_text()), SHARED.load_pi_documents(self.pi_root), terminal, prompt

    def test_all_model_combinations_and_same_provider_distinct_keys(self):
        for hermes_choice, pi_choice in [('1', '1'), ('1', '2'), ('2', '1'), ('2', '2')]:
            with self.subTest(hermes=hermes_choice, pi=pi_choice):
                self.config.write_text(json.dumps({'display': {'keep': True},
                                                   'agent': {'reasoning_effort': 'none'}}))
                original = {'models.json': {'providers': {'unrelated': {'models': [{'id': 'keep'}]}}},
                            'settings.json': {'theme': 'keep', 'defaultThinkingLevel': 'off'},
                            'auth.json': {'unrelated': {'type': 'api_key', 'key': 'keep'}}}
                self.pi_root.mkdir(parents=True, exist_ok=True)
                for name, data in original.items():
                    (self.pi_root / name).write_text(json.dumps(data))
                config, documents, terminal, prompt = self.configure(
                    hermes_choice + '\n' + pi_choice + '\n', ('!hermes$key', '!pi$key'))
                hermes_model = SHARED.MODELS[int(hermes_choice) - 1]
                pi_model = SHARED.MODELS[int(pi_choice) - 1]
                self.assertEqual(config['model']['default'], hermes_model['id'])
                self.assertEqual(config['model']['base_url'], hermes_model['base_url'])
                self.assertEqual(config['model']['api_key'], '!hermes$key')
                self.assertEqual(config['display'], {'keep': True})
                settings = documents['settings.json']
                self.assertEqual((settings['defaultProvider'], settings['defaultModel']),
                                 (pi_model['pi_provider'], pi_model['id']))
                self.assertEqual(documents['auth.json'][pi_model['pi_provider']]['key'], '$!pi$$key')
                self.assertEqual(SHARED.saved_pi_key(documents, pi_model), '!pi$key')
                self.assertEqual(documents['auth.json']['unrelated'], original['auth.json']['unrelated'])
                self.assertEqual(documents['models.json']['providers']['unrelated'],
                                 original['models.json']['providers']['unrelated'])
                self.assertEqual(settings['theme'], 'keep')
                self.assertEqual(prompt.call_count, 2)
                self.assertIn('Hermes', prompt.call_args_list[0].args[0])
                self.assertIn('Pi', prompt.call_args_list[1].args[0])
                self.assertNotIn('!hermes$key', terminal.output.getvalue())
                self.assertNotIn('!pi$key', terminal.output.getvalue())
                for path in [self.config] + [self.pi_root / name for name in documents]:
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                if hermes_choice == '2':
                    self.assertEqual(config['agent']['reasoning_effort'], 'max')
                if pi_choice == '2':
                    self.assertEqual(settings['defaultThinkingLevel'], 'max')
                    entry = documents['models.json']['providers']['zai']['models'][0]
                    self.assertTrue(entry['reasoning'])
                    self.assertIsNone(entry['thinkingLevelMap']['off'])

    def test_rerun_keeps_each_agents_own_model_and_literal_key(self):
        self.configure('2\n1\n', ('!hermes$key', '!pi$key'))
        config, documents, _, prompt = self.configure('\n\n', ('', ''))
        self.assertEqual(config['model']['default'], 'glm-5.3')
        self.assertEqual(config['model']['api_key'], '!hermes$key')
        self.assertEqual(documents['settings.json']['defaultProvider'], 'abliteration')
        self.assertEqual(SHARED.saved_pi_key(documents, SHARED.MODELS[0]), '!pi$key')
        self.assertEqual(prompt.call_count, 2)
        self.assertTrue(all('Enter to keep' in call.args[0] for call in prompt.call_args_list))
        for parent in [self.hermes_home, self.pi_root]:
            backups = list(parent.glob('stage3-backup-*/*'))
            self.assertTrue(backups)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in backups))

    def test_each_key_can_be_replaced_without_changing_the_other_agent(self):
        self.configure('1\n1\n', ('hermes-original', 'pi-original'))
        config, documents, _, _ = self.configure('\n\n', ('hermes-new', ''))
        self.assertEqual(config['model']['api_key'], 'hermes-new')
        self.assertEqual(SHARED.saved_pi_key(documents, SHARED.MODELS[0]), 'pi-original')
        config, documents, _, _ = self.configure('\n\n', ('', 'pi-new'))
        self.assertEqual(config['model']['api_key'], 'hermes-new')
        self.assertEqual(SHARED.saved_pi_key(documents, SHARED.MODELS[0]), 'pi-new')

    def test_pi_switch_does_not_change_hermes_model_or_key(self):
        self.configure('1\n1\n', ('hermes-only', 'pi-abliteration'))
        config, documents, _, prompt = self.configure('\n2\n', ('', 'pi-zai'))
        self.assertEqual(config['model']['default'], 'abliterated-model-large-v2')
        self.assertEqual(config['model']['api_key'], 'hermes-only')
        self.assertEqual(documents['settings.json']['defaultProvider'], 'zai')
        self.assertEqual(documents['auth.json']['zai']['key'], 'pi-zai')
        self.assertNotIn('Enter to keep', prompt.call_args_list[1].args[0])

    def test_hermes_switch_does_not_change_pi_model_or_key(self):
        self.configure('1\n2\n', ('hermes-abliteration', 'pi-only'))
        config, documents, _, prompt = self.configure('2\n\n', ('hermes-zai', ''))
        self.assertEqual(config['model']['default'], 'glm-5.3')
        self.assertEqual(config['model']['api_key'], 'hermes-zai')
        self.assertEqual(documents['settings.json']['defaultProvider'], 'zai')
        self.assertEqual(documents['auth.json']['zai']['key'], 'pi-only')
        self.assertNotIn('Enter to keep', prompt.call_args_list[0].args[0])

    def test_regular_zai_key_is_not_reused_for_coding_plan(self):
        self.config.write_text(json.dumps({'model': {'default': 'glm-5.3',
            'base_url': 'https://api.z.ai/api/paas/v4', 'api_key': 'regular-only'}}))
        config, _, _, prompt = self.configure('\n1\n', ('coding-only', 'pi-only'))
        self.assertEqual(config['model']['base_url'], 'https://api.z.ai/api/coding/paas/v4')
        self.assertEqual(config['model']['api_key'], 'coding-only')
        self.assertNotIn('Enter to keep', prompt.call_args_list[0].args[0])

    def test_old_hermes_key_is_not_offered_to_an_unconfigured_pi(self):
        self.config.write_text(json.dumps({'model': {'default': 'abliterated-model-large-v2',
            'base_url': 'https://api.abliteration.ai/v1', 'api_key': 'hermes-existing',
            'key_env': 'OLD_ENV', 'api_key_env': 'OLD_ENV', 'unrelated': 'keep'}}))
        config, documents, _, prompt = self.configure('\n1\n', ('', 'pi-new'))
        self.assertEqual(config['model']['api_key'], 'hermes-existing')
        self.assertEqual(documents['auth.json']['abliteration']['key'], 'pi-new')
        self.assertNotIn('Enter to keep', prompt.call_args_list[1].args[0])
        self.assertEqual(config['model']['unrelated'], 'keep')
        self.assertNotIn('key_env', config['model'])
        self.assertNotIn('api_key_env', config['model'])

    def test_cancelling_or_invalid_pi_key_leaves_both_existing_configs_untouched(self):
        self.configure('1\n2\n')
        originals = {path: path.read_bytes() for path in [self.config] +
                     [self.pi_root / name for name in ['auth.json', 'models.json', 'settings.json']]}
        for answers, keys, error in [('1\n', ('new-hermes',), EOFError),
                                    ('1\n2\n', ('new-hermes', 'invalid pi key'), ValueError)]:
            with self.subTest(error=error), self.assertRaises(error):
                self.configure(answers, keys)
            for path, data in originals.items():
                self.assertEqual(path.read_bytes(), data)
            self.assertFalse(list(self.hermes_home.glob('stage3-backup-*')))
            self.assertFalse(list(self.pi_root.glob('stage3-backup-*')))

    def test_invalid_model_reprompts_and_blank_new_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Hermes requires'):
            self.configure('1\n', ('',))
        self.assertFalse(self.config.exists())
        _, _, terminal, _ = self.configure('99\n2\nwrong\n1\n')
        self.assertEqual(terminal.output.getvalue().count('Enter one of the listed numbers.'), 2)

    def test_saved_pi_key_never_evaluates_commands_or_environment_references(self):
        self.configure('1\n2\n')
        documents = SHARED.load_pi_documents(self.pi_root)
        for value in ['!read-secret', '$ZAI_API_KEY', '$' + '{ZAI_API_KEY}']:
            documents['auth.json']['zai']['key'] = value
            self.assertIsNone(SHARED.saved_pi_key(documents, SHARED.MODELS[1]))
        documents['models.json']['providers']['zai']['baseUrl'] = 'https://api.z.ai/api/paas/v4'
        documents['auth.json']['zai']['key'] = 'regular-only'
        self.assertIsNone(SHARED.saved_pi_key(documents, SHARED.MODELS[1]))

    def test_pi_other_models_and_reasoning_defaults_are_preserved(self):
        self.configure('1\n2\n')
        documents = SHARED.load_pi_documents(self.pi_root)
        documents['models.json']['providers']['zai']['models'].insert(0, {'id': 'keep'})
        documents['settings.json']['defaultThinkingLevel'] = 'low'
        SHARED.configure_pi_model(documents, SHARED.MODELS[1], 'pi-new')
        SHARED.configure_pi_model(documents, SHARED.MODELS[1], 'pi-new')
        self.assertEqual([m['id'] for m in documents['models.json']['providers']['zai']['models']],
                         ['keep', 'glm-5.3'])
        self.assertEqual(documents['settings.json']['defaultThinkingLevel'], 'low')

    def test_stage4_preserves_pi_choices_and_does_not_read_hermes_configuration(self):
        self.configure('2\n1\n', ('hermes-only', 'pi-only'))
        original = {path: path.read_bytes() for path in self.pi_root.glob('*.json')}
        self.config.write_text('invalid Hermes config: stage 4 must not read this')
        ssh_dir = self.home / '.ssh'
        ssh_dir.mkdir()
        (ssh_dir / 'hermes-desktop-client').write_text('dummy-private-key')
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            stdout = 'ssh-ed25519 AAAADUMMY' if argv[0] == 'ssh-keygen' else \
                     'abliteration abliterated-model-large-v2\n'
            return SimpleNamespace(returncode=0, stdout=stdout)

        with patch.object(Path, 'home', return_value=self.home), \
                patch.object(SHARED.subprocess, 'run', side_effect=run), \
                patch.object(SHARED.getpass, 'getpass', side_effect=AssertionError('No stage 4 key prompts')), \
                redirect_stdout(io.StringIO()):
            PI['main']()
        for path, contents in original.items():
            self.assertEqual(path.read_bytes(), contents)
        pi_call = next((argv, kwargs) for argv, kwargs in calls if argv[0] == 'pi')
        self.assertEqual(pi_call[0][-2:], ['--list-models', 'abliterated-model-large-v2'])
        self.assertEqual(pi_call[1]['env']['PI_OFFLINE'], '1')
        self.assertEqual(json.loads((self.pi_root / 'auth.json').read_text())['abliteration']['key'], 'pi-only')

    def test_stage4_rejects_missing_pi_setup_before_touching_ssh(self):
        with patch.object(Path, 'home', return_value=self.home), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'Run stage3.sh'):
                PI['main']()
        self.assertFalse((self.home / '.ssh').exists())


if __name__ == '__main__':
    unittest.main()
