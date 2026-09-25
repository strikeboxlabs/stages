#!/usr/bin/env python3
"""Stage 3: configure Hermes without deferring the provider wizard."""
import getpass
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

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
    key = model.get('api_key') if model.get('base_url') == 'https://api.abliteration.ai/v1' else None
    if not key:
        with open('/dev/tty', 'w') as terminal:
            key = getpass.getpass('Abliteration API key for Hermes and Pi (hidden): ', stream=terminal).strip()
    if not isinstance(key, str) or not key or any(char.isspace() for char in key):
        raise ValueError('A nonempty API key without whitespace is required.')
    model.update(default='abliterated-model-large-v2', provider='custom',
                 base_url='https://api.abliteration.ai/v1', api_mode='chat_completions',
                 api_key=key, context_length=1000000)
    for field in ('key_env', 'api_key_env'):
        model.pop(field, None)
    config['model'] = model
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
    print('[stage3] Hermes configured for Abliteration Large v2; credentials saved with mode 0600.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError, yaml.YAMLError) as exc:
        sys.exit(f'Error [stage3: Hermes configuration]: {exc}')
    except (KeyboardInterrupt, EOFError):
        sys.exit('Error [stage3]: Configuration cancelled; rerun stage3.sh to finish.')
