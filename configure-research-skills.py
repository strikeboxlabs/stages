#!/usr/bin/env python3
"""Check out the shared research skills and link them into one account's agents."""
import argparse
import os
from pathlib import Path
import pwd
import subprocess
import tempfile


REPOSITORY = 'https://github.com/strikeboxlabs/research-library.git'
CHECKOUT = Path('/opt/research-library')


def validate_checkout(checkout):
    if checkout.is_symlink() or not (checkout / '.git').is_dir():
        raise ValueError(f'Refusing an unmanaged checkout: {checkout}')
    origin = subprocess.check_output(
        ['git', '-C', str(checkout), 'remote', 'get-url', 'origin'], text=True).strip()
    if origin.removesuffix('.git') != REPOSITORY.removesuffix('.git'):
        raise ValueError(f'Refusing a checkout with a different Git origin: {checkout}')
    skills = checkout / 'skills'
    if skills.is_symlink() or not skills.is_dir() or not any(skills.glob('*/SKILL.md')):
        raise ValueError(f'Repository layout changed: expected skills/*/SKILL.md in {checkout}')


def validate_link(link, target):
    if os.path.lexists(link) and not (link.is_symlink() and os.readlink(link) == str(target)):
        raise ValueError(f'Refusing conflicting skill destination: {link}')
    for parent in link.parents:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError(f'Refusing non-directory or symlinked parent: {parent}')


def make_parents(path, uid, gid):
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        os.chown(directory, uid, gid)


def configure(user, *, checkout=CHECKOUT, hermes_home=None, apply=False):
    account = pwd.getpwnam(user)
    home = Path(account.pw_dir)
    hermes_home = Path(hermes_home) if hermes_home else home / '.hermes'
    if not home.is_dir() or not hermes_home.is_absolute() or not checkout.is_absolute():
        raise ValueError('An existing account home and absolute paths are required.')
    target = checkout / 'skills'
    links = [hermes_home / 'skills/research-library', home / '.pi/agent/skills/research-library']
    # Preflight every destination before cloning or creating either link.
    for link in links:
        validate_link(link, target)
    exists = os.path.lexists(checkout)
    if exists:
        validate_checkout(checkout)
    if not apply:
        print(f'Would {"reuse" if exists else "clone"} {REPOSITORY} at {checkout}.')
        for link in links:
            print(f'Would link {link} -> {target} (existing content preserved).')
        return
    if not exists:
        # Publish only a complete, validated checkout. Do not pull existing trees:
        # local edits and deliberate revisions belong to the administrator.
        with tempfile.TemporaryDirectory(prefix='.research-library-', dir=checkout.parent) as temporary:
            clone = Path(temporary) / 'checkout'
            subprocess.run(['git', 'clone', '--', REPOSITORY, str(clone)], check=True, umask=0o022)
            validate_checkout(clone)
            if os.path.lexists(checkout):
                raise ValueError(f'Checkout appeared during clone; refusing to replace: {checkout}')
            clone.rename(checkout)
    for link in links:
        validate_link(link, target)
        if not os.path.lexists(link):
            make_parents(link.parent, account.pw_uid, account.pw_gid)
            link.symlink_to(target, target_is_directory=True)
            os.chown(link, account.pw_uid, account.pw_gid, follow_symlinks=False)
        print(f'{link} -> {target}')
    count = len(list(target.glob('*/SKILL.md')))
    print(f'{count} research skills linked; reference and script files stay in the shared checkout.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True, help='Account used by Hermes and local Pi workers')
    parser.add_argument('--hermes-home', type=Path, help='Same HERMES_HOME as stage 3, if customized')
    parser.add_argument('--apply', action='store_true', help='Clone/link; without this flag, check only')
    args = parser.parse_args()
    try:
        configure(args.user, hermes_home=args.hermes_home, apply=args.apply)
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Cannot configure research skills: {exc}') from exc
