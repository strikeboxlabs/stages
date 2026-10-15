#!/usr/bin/env python3
"""Link a SAST checkout into Hermes, Pi and the stable Board skill path."""
import argparse
import os
from pathlib import Path
import pwd
import runpy

HELPERS = runpy.run_path(str(Path(__file__).with_name('configure-research-skills.py')))


def configure(repo, user, hermes_home=None, tools_root=Path('/opt/sast-tools'), apply=False):
    account = pwd.getpwnam(user)
    target = repo.resolve() / 'skills'
    if not target.is_dir() or not any(target.glob('*/SKILL.md')):
        raise ValueError(f'No SAST skills found: {target}')
    home = Path(account.pw_dir)
    hermes_home = Path(hermes_home) if hermes_home else home / '.hermes'
    if not hermes_home.is_absolute():
        raise ValueError('HERMES_HOME must be absolute.')
    links = [hermes_home / 'skills/sast', home / '.pi/agent/skills/sast', tools_root / 'skills']
    for link in links:
        HELPERS['validate_link'](link, target)
    for link in links:
        if apply and not os.path.lexists(link):
            uid, gid = (account.pw_uid, account.pw_gid) if link != tools_root / 'skills' else (os.geteuid(), os.getegid())
            HELPERS['make_parents'](link.parent, uid, gid)
            link.symlink_to(target, target_is_directory=True)
            os.chown(link, uid, gid, follow_symlinks=False)
        print(f'{"Linked" if apply else "Would link"} {link} -> {target}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--hermes-home', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        configure(args.repo, args.user, args.hermes_home, apply=args.apply)
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f'Cannot link SAST skills: {exc}') from exc
