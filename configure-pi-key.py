#!/usr/bin/env python3
"""Save an Abliteration API key in the current user's Pi credential store."""
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path


def main():
    if os.geteuid() == 0:
        sys.exit('Run without sudo, as the user who runs Pi.')
    target = Path.home() / '.pi/agent/auth.json'
    data = json.loads(target.read_text()) if target.exists() else {}
    if not isinstance(data, dict):
        sys.exit('Pi auth.json must contain an object; no changes made.')
    if not sys.stdin.isatty():
        sys.exit('Run in a terminal so the API key can be entered with hidden input.')
    key = getpass.getpass('Abliteration API key (hidden): ').strip()
    if not key:
        sys.exit('Empty key; no changes made.')
    # Pi interprets $ and a leading ! as configuration syntax; escape literals.
    key = key.replace('$', '$$')
    if key.startswith('!'):
        key = '$!' + key[1:]
    data['abliteration'] = {'type': 'api_key', 'key': key}
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.auth-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print('Saved the API key with owner-only permissions. Start a new Pi session with: pi')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(f'Could not save the Pi API key: {exc}')
    except (KeyboardInterrupt, EOFError):
        sys.exit('\nCancelled; no key saved.')
