#!/usr/bin/env python3
"""Stage 4: verify Pi's independent configuration and prepare SSH keys."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_providers import load_pi_documents, saved_pi_model, verify_pi_model


def main():
    root = Path.home() / '.pi/agent'
    print(f'[stage4] Configuring {root}', flush=True)
    selected = saved_pi_model(load_pi_documents(root))
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
    existing_keys = authorized.read_text() if authorized.exists() else ''
    key_blob = public_key.split()[1]
    if not any(key_blob in line.split() for line in existing_keys.splitlines() if not line.lstrip().startswith('#')):
        with authorized.open('a') as stream:
            if existing_keys and not existing_keys.endswith('\n'):
                stream.write('\n')
            stream.write(public_key + '\n')
    authorized.chmod(0o600)

    print('[stage4] Verifying Pi model registration offline...', flush=True)
    verify_pi_model(root, selected)
    print('[stage4] Independent Pi configuration verified; workstation SSH key installed.')
    print('Local commands: pi or hermes. Credentials have owner-only permissions.')
    print(f'Workstation PRIVATE key to transfer securely: {client_key}')
    print('Store that key on the workstation with owner-only access, then select it in SSH configuration.')
    print(f"Default: {selected['pi_provider']} / {selected['id']}. API access has not been tested.")


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit('\nError [stage4]: Cancelled. Configuration already written, if any, remains in place; rerun to finish.')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.exit(f'Error [stage4]: {exc}')
