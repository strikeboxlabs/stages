#!/usr/bin/env python3
"""Migrate the local session default in dispatch.yaml; never patch Board source."""
import argparse
from pathlib import Path
from board_config import doctor, require_native, update_config


def configure(board_root, apply=False, *, config=None, user=None, group=None,
              backup_dir=Path('/var/lib/stages/board-config-backups'), board_command=None):
    board_root = Path(board_root)
    board_bin = board_command or board_root / 'board/.venv/bin/board'
    require_native(board_bin)
    path = Path(config) if config else board_root / 'dispatch.yaml'
    changed = update_config(path, 'local',
                            lambda mapping: mapping.setdefault('pi_session_storage', 'project'),
                            apply=apply, backup_dir=backup_dir,
                            validate=lambda candidate: doctor(board_bin, candidate, user, group))
    print('Native Pi session configuration validated; existing storage choice and transcripts preserved.')
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board-root', type=Path, default=Path('/opt/board'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--user')
    parser.add_argument('--group')
    parser.add_argument('--backup-dir', type=Path, default=Path('/var/lib/stages/board-config-backups'))
    parser.add_argument('--board-bin', type=Path, help='Override the installed native Board executable')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        configure(args.board_root, args.apply, config=args.config, user=args.user, group=args.group, backup_dir=args.backup_dir, board_command=args.board_bin)
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Cannot configure native Pi sessions: {exc}') from exc
