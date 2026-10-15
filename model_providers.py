"""Model settings shared by the stage 3 and stage 4 configuration helpers."""
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MODELS = (
    {
        'label': 'Abliteration.ai — Large v2',
        'id': 'abliterated-model-large-v2',
        'pi_provider': 'abliteration',
        'base_url': 'https://api.abliteration.ai/v1',
        'key_env': 'ABLIT_KEY',
        'context_length': 1000000,
        # Conservative per-response limit; not the provider's maximum.
        'max_tokens': 8192,
    },
    {
        'label': 'Z.ai — GLM-5.3',
        'id': 'glm-5.3',
        'pi_provider': 'zai',
        'base_url': 'https://api.z.ai/api/coding/paas/v4',
        'key_env': 'ZAI_API_KEY',
        'context_length': 1000000,
        'max_tokens': 131072,
        'reasoning': True,
    },
    {
        'label': 'DeepSeek — Flash (Max)',
        'id': 'deepseek-flash',
        'pi_provider': 'deepseek',
        'base_url': 'https://api.deepseek.com',
        'key_env': 'DEEPSEEK_API_KEY',
        'context_length': 1000000,
        'max_tokens': 384000,
        'reasoning': True,
        'thinking_format': 'deepseek',
        'reasoning_effort': 'max',
    },
)


def valid_api_key(key):
    return isinstance(key, str) and bool(key) and not any(char.isspace() for char in key)


def prompt_model_and_key(agent, current_model, saved_key, terminal, input_stream):
    """Prompt independently; saved_key must read only this agent's credentials."""
    default = next((i for i, item in enumerate(MODELS) if item['id'] == current_model), 0)
    terminal.write(f'\nChoose the model for {agent}:\n')
    for number, item in enumerate(MODELS, 1):
        terminal.write(f"  {number}) {item['label']}\n")
    while True:
        terminal.write(f'Select [1-{len(MODELS)}] (default {default + 1}): ')
        terminal.flush()
        answer = input_stream.readline()
        if not answer:
            raise EOFError(f'{agent} model selection cancelled.')
        answer = answer.strip()
        if not answer:
            selected = dict(MODELS[default])
            break
        if answer in [str(number) for number in range(1, len(MODELS) + 1)]:
            selected = dict(MODELS[int(answer) - 1])
            break
        terminal.write('Enter one of the listed numbers.\n')
    previous = saved_key(selected)
    reuse = valid_api_key(previous)
    suffix = '; Enter to keep this agent\'s saved key' if reuse else ''
    key = getpass.getpass(f"{agent} — {selected['label']} API key (hidden{suffix}): ", stream=terminal).strip()
    if not key and reuse:
        key = previous
    if not valid_api_key(key):
        raise ValueError(f'{agent} requires a nonempty API key without whitespace.')
    return selected, key


def load_pi_documents(root):
    documents = {}
    for name in ('models.json', 'settings.json', 'auth.json'):
        path = root / name
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError(f'{name} must contain a JSON object; no files changed.')
        documents[name] = data
    if not isinstance(documents['models.json'].get('providers', {}), dict):
        raise ValueError('models.json providers must be an object; no files changed.')
    return documents


def saved_pi_key(documents, selected):
    provider_id = selected['pi_provider']
    provider = documents['models.json'].get('providers', {}).get(provider_id, {})
    if not isinstance(provider, dict) or str(provider.get('baseUrl') or '').rstrip('/') != selected['base_url']:
        return None
    auth = documents['auth.json'].get(provider_id, {})
    if not isinstance(auth, dict) or auth.get('type') != 'api_key':
        return None
    key = auth.get('key')
    if not valid_api_key(key) or key.startswith('!'):
        return None
    # Reuse only a stored literal; never execute commands or expand references.
    if key.startswith('$!'):
        key = '!' + key[2:]
    if '$' in key.replace('$$', ''):
        return None
    return key.replace('$$', '$')


def configure_pi_model(documents, selected, key):
    if not valid_api_key(key):
        raise ValueError('Pi requires a nonempty API key without whitespace.')
    providers = documents['models.json'].setdefault('providers', {})
    if not isinstance(providers, dict):
        raise ValueError('models.json providers must be an object; no files changed.')
    provider_id = selected['pi_provider']
    provider = providers.setdefault(provider_id, {})
    if not isinstance(provider, dict) or not isinstance(provider.get('models', []), list):
        raise ValueError(f'models.json provider {provider_id} must be an object with a model list; no files changed.')
    key = key.replace('$', '$$')
    if key.startswith('!'):
        key = '$!' + key[1:]
    documents['auth.json'][provider_id] = {'type': 'api_key', 'key': key}
    entry = {
        'id': selected['id'],
        'name': selected['label'],
        'input': ['text'],
        'contextWindow': selected['context_length'],
        'maxTokens': selected['max_tokens'],
    }
    if selected.get('reasoning'):
        entry.update(reasoning=True,
                     thinkingLevelMap={'off': None, 'minimal': None, 'low': 'low',
                                       'medium': None, 'high': 'high', 'xhigh': None, 'max': 'max'},
                     compat={'thinkingFormat': selected.get('thinking_format', 'zai'), 'supportsReasoningEffort': True,
                             'maxTokensField': 'max_tokens'})
        if selected.get('reasoning_effort'):
            documents['settings.json']['defaultThinkingLevel'] = selected['reasoning_effort']
        elif documents['settings.json'].get('defaultThinkingLevel') not in ('low', 'high', 'max'):
            documents['settings.json']['defaultThinkingLevel'] = 'max'
    provider.update(baseUrl=selected['base_url'], api='openai-completions',
                    apiKey='$' + selected['key_env'],
                    models=[item for item in provider.get('models', [])
                            if not isinstance(item, dict) or item.get('id') != selected['id']] + [entry])
    documents['settings.json'].update(defaultProvider=provider_id, defaultModel=selected['id'])


def saved_pi_model(documents):
    settings = documents['settings.json']
    for selected in MODELS:
        if (settings.get('defaultProvider'), settings.get('defaultModel')) == (selected['pi_provider'], selected['id']):
            if valid_api_key(saved_pi_key(documents, selected)):
                return dict(selected)
    raise ValueError('Pi has no valid saved model and API key. Run stage3.sh for this account first.')


def write_json(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_pi_documents(root, documents):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    existing = [name for name in documents if (root / name).exists()]
    if existing:
        backup = Path(tempfile.mkdtemp(prefix='stage3-backup-', dir=root))
        for name in existing:
            shutil.copyfile(root / name, backup / name)
            (backup / name).chmod(0o600)
        print(f'[stage3] Previous Pi configuration backed up in {backup}')
    for name, data in documents.items():
        write_json(root / name, data)


def verify_pi_model(root, selected):
    env = dict(os.environ, PI_CODING_AGENT_DIR=str(root), PI_OFFLINE='1')
    result = subprocess.run([
        'pi', '--offline', '--no-extensions', '--no-skills',
        '--no-prompt-templates', '--no-themes', '--no-context-files',
        '--no-approve', '--list-models', selected['id'],
    ], env=env, cwd=Path.home(), text=True, capture_output=True, timeout=60)
    if result.returncode or not any(
        line.split()[:2] == [selected['pi_provider'], selected['id']]
        for line in result.stdout.splitlines()
    ):
        raise RuntimeError('Configuration saved, but Pi did not list the selected model. Check the installed Pi version and ~/.pi/agent/models.json.')
