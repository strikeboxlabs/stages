#!/usr/bin/env python3
"""Stage 3: configure independent models and credentials for Hermes and Pi."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The managed Hermes runtime executes this file through runpy with python -I.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_providers import (configure_pi_model, load_pi_documents,
                             prompt_model_and_key, saved_pi_key,
                             save_pi_documents, verify_pi_model)

try:
    import hermes_yaml as yaml
except ModuleNotFoundError as exc:
    if exc.name != 'hermes_yaml':
        raise
    import yaml  # Older Hermes releases used PyYAML directly.


def main():
    # Verify backend dependencies in the actual Hermes runtime before saving.
    import fastapi, uvicorn, ptyprocess  # noqa: F401

    home = Path(os.environ['HERMES_HOME'])
    path = home / 'config.yaml'
    config = yaml.safe_load(path.read_text()) if path.exists() else {}
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError('Hermes config.yaml must contain a mapping.')
    model = config.get('model') or {}
    if isinstance(model, str):
        model = {'default': model}
    if not isinstance(model, dict):
        raise ValueError('Hermes model configuration must be a mapping.')
    pi_root = Path.home() / '.pi/agent'
    documents = load_pi_documents(pi_root)
    # Separate streams work on a non-seekable TTY, including through sudo.
    with open('/dev/tty', 'r') as input_stream, open('/dev/tty', 'w') as terminal:
        selected, key = prompt_model_and_key(
            'Hermes', model.get('default'),
            lambda choice: model.get('api_key') if str(model.get('base_url') or '').rstrip('/') == choice['base_url'] else None,
            terminal, input_stream)
        pi_selected, pi_key = prompt_model_and_key(
            'Pi', documents['settings.json'].get('defaultModel'),
            lambda choice: saved_pi_key(documents, choice), terminal, input_stream)
    # Finish all prompting and validation before writing either agent's files.
    configure_pi_model(documents, pi_selected, pi_key)
    model.update(default=selected['id'], provider='custom',
                 base_url=selected['base_url'], api_mode='chat_completions',
                 api_key=key, context_length=selected['context_length'])
    for field in ('key_env', 'api_key_env'):
        model.pop(field, None)
    config['model'] = model
    if selected.get('reasoning'):
        agent = config.setdefault('agent', {})
        if not isinstance(agent, dict):
            raise ValueError('Hermes agent configuration must be a mapping.')
        # GLM-5.3 rejects disabled reasoning and unsupported effort levels.
        if agent.get('reasoning_effort') not in ('low', 'high', 'max'):
            agent['reasoning_effort'] = 'max'
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    if path.exists():
        backup = Path(tempfile.mkdtemp(prefix='stage3-backup-', dir=home)) / 'config.yaml'
        shutil.copyfile(path, backup)
        backup.chmod(0o600)
    fd, temporary = tempfile.mkstemp(prefix='.config-', dir=home)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(config, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"[stage3] Hermes configured for {selected['label']}; credentials saved with mode 0600.")
    save_pi_documents(pi_root, documents)
    print('[stage3] Verifying Pi model registration offline...', flush=True)
    verify_pi_model(pi_root, pi_selected)
    print(f"[stage3] Pi independently configured for {pi_selected['label']}; credentials saved with mode 0600.")


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError, RuntimeError, yaml.YAMLError, subprocess.SubprocessError) as exc:
        sys.exit(f'Error [stage3: Hermes configuration]: {exc}')
    except (KeyboardInterrupt, EOFError):
        sys.exit('Error [stage3]: Configuration cancelled; rerun stage3.sh to finish.')
