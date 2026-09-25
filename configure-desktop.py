#!/usr/bin/env python3
"""Stage 4: reuse Hermes credentials for Pi and prepare SSH keys."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
try:
    import hermes_yaml as yaml
except ModuleNotFoundError as exc:
    if exc.name != 'hermes_yaml':
        raise
    import yaml  # Older Hermes releases used PyYAML directly.


def write_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)  # mkstemp gives owner-only permissions.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    root = Path.home() / '.pi/agent'
    print(f'[stage4] Configuring {root}', flush=True)
    documents = {}
    for name in ('models.json', 'settings.json', 'auth.json'):
        path = root / name
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError(f'{name} must contain a JSON object; no files changed.')
        documents[name] = data
    providers = documents['models.json'].setdefault('providers', {})
    if not isinstance(providers, dict):
        raise ValueError('models.json providers must be an object; no files changed.')

    hermes_home = Path(os.environ['HERMES_HOME'])
    hermes_path = hermes_home / 'config.yaml'
    hermes_config = yaml.safe_load(hermes_path.read_text()) if hermes_path.exists() else {}
    model = hermes_config.get('model', {}) if isinstance(hermes_config, dict) else {}
    if not isinstance(model, dict) or model.get('base_url') != 'https://api.abliteration.ai/v1' or model.get('default') != 'abliterated-model-large-v2':
        raise ValueError('Hermes is not configured for Abliteration Large v2. Run stage3.sh for this account first.')
    key = model.get('api_key')
    if not isinstance(key, str) or not key or any(char.isspace() for char in key):
        raise ValueError('Hermes has no valid API key. Run stage3.sh for this account first.')
    ssh_dir = Path.home() / '.ssh'
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    ssh_dir.chmod(0o700)
    authorized = ssh_dir / 'authorized_keys'
    if authorized.is_symlink():
        raise ValueError('Refusing to overwrite a symlink at ~/.ssh/authorized_keys.')
    client_key = ssh_dir / 'hermes-desktop-client'
    public_path = client_key.with_suffix('.pub')
    if client_key.is_symlink() or public_path.is_symlink():
        raise ValueError('Refusing symlinked Hermes Desktop SSH key files.')
    if not client_key.exists():
        if public_path.exists():
            raise ValueError('Hermes Desktop public key exists without its private key; move it aside before retrying.')
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                        '-C', 'hermes-desktop-client', '-f', str(client_key)], check=True)
    client_key.chmod(0o600)
    # Derive the public key from the private key, including on reruns.
    result = subprocess.run(['ssh-keygen', '-y', '-P', '', '-f', str(client_key)],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Cannot read the existing Hermes Desktop private key (must be valid and unencrypted).')
    public_key = result.stdout.strip()
    public_path.write_text(public_key + ' hermes-desktop-client\n')
    public_path.chmod(0o600)
    # Escape Pi's config interpolation syntax so the saved key stays literal.
    key = key.replace('$', '$$')
    if key.startswith('!'):
        key = '$!' + key[1:]
    documents['auth.json']['abliteration'] = {'type': 'api_key', 'key': key}
    providers['abliteration'] = {
        'baseUrl': 'https://api.abliteration.ai/v1',
        'api': 'openai-completions',
        'apiKey': '$ABLIT_KEY',
        'models': [{
            'id': 'abliterated-model-large-v2',
            'name': 'Abliteration Large v2 (Cyber)',
            'input': ['text'],
            'contextWindow': 1000000,
            # Conservative per-response limit; not the provider's maximum.
            'maxTokens': 8192,
        }],
    }
    documents['settings.json'].update(
        defaultProvider='abliteration', defaultModel='abliterated-model-large-v2'
    )
    existing_keys = authorized.read_text() if authorized.exists() else ''
    key_blob = public_key.split()[1]
    if not any(key_blob in line.split() for line in existing_keys.splitlines() if not line.lstrip().startswith('#')):
        with authorized.open('a') as stream:
            if existing_keys and not existing_keys.endswith('\n'):
                stream.write('\n')
            stream.write(public_key + '\n')
    authorized.chmod(0o600)

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    existing = [name for name in documents if (root / name).exists()]
    if existing:
        backup = Path(tempfile.mkdtemp(prefix='stage4-backup-', dir=root))
        for name in existing:
            shutil.copyfile(root / name, backup / name)
            (backup / name).chmod(0o600)
        print(f'[stage4] Previous configuration backed up in {backup}')
    for name, data in documents.items():
        write_json(root / name, data)

    print('[stage4] Verifying Pi model registration offline...', flush=True)
    env = dict(os.environ, PI_CODING_AGENT_DIR=str(root), PI_OFFLINE='1')
    result = subprocess.run([
        'pi', '--offline', '--no-extensions', '--no-skills',
        '--no-prompt-templates', '--no-themes', '--no-context-files',
        '--no-approve', '--list-models', 'abliterated-model-large-v2',
    ], env=env, cwd=Path.home(), text=True, capture_output=True, timeout=60)
    if result.returncode or not any(
        line.split()[:2] == ['abliteration', 'abliterated-model-large-v2']
        for line in result.stdout.splitlines()
    ):
        raise RuntimeError('Configuration saved, but Pi did not list the v2 model. Check the installed Pi version and ~/.pi/agent/models.json.')
    print('[stage4] Pi configured with the stage-3 Hermes API key; workstation SSH key installed.')
    print('Local commands: pi or hermes. Credentials have owner-only permissions.')
    print(f'Workstation PRIVATE key to transfer securely: {client_key}')
    print('Store that key on the workstation with owner-only access, then select it in SSH configuration.')
    print('Default: abliteration / abliterated-model-large-v2. API access has not been tested.')


try:
    main()
except (KeyboardInterrupt, EOFError):
    sys.exit('\nError [stage4]: Cancelled. Configuration already written, if any, remains in place; rerun to finish.')
except (OSError, ValueError, RuntimeError, yaml.YAMLError, subprocess.SubprocessError) as exc:
    sys.exit(f'Error [stage4]: {exc}')
