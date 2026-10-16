#!/usr/bin/env python3
"""Register installed catalogs in native Board config; never patch Board source."""
import argparse
from pathlib import Path
from board_config import doctor, require_native, update_config

MANAGED_CATALOGS = {
    '/opt/research-library/skills': '/opt/research-library/catalog.json',
    '/opt/sast-tools/skills': '/opt/sast-tools/catalog.json',
}


def register_catalog(mapping, catalog):
    catalogs = mapping.setdefault('catalogs', [])
    if not isinstance(catalogs, list) or not all(isinstance(item, str) for item in catalogs):
        raise ValueError('skills.catalogs must be a list of paths.')
    if str(catalog) not in catalogs:
        catalogs.append(str(catalog))
    mapping['enabled'] = True
    mapping.setdefault('default_profiles', ['general'])
    mapping.setdefault('max_metadata_tokens', 6000)


def configure(board_root, apply=False, skills_dir='/opt/research-library/skills', *,
              catalog=None, config=None, user=None, group=None, check_native=False,
              backup_dir=Path('/var/lib/stages/board-config-backups'), board_command=None):
    board_root = Path(board_root)
    board_bin = board_command or board_root / 'board/.venv/bin/board'
    require_native(board_bin)
    if check_native:
        print('Native Board skill-profile configuration is available; no source changes.')
        return False
    if catalog is None:
        if skills_dir not in MANAGED_CATALOGS:
            raise ValueError('Unsupported managed skill directory; use --catalog PATH.')
        catalog = Path(MANAGED_CATALOGS[skills_dir])
    catalog = Path(catalog)
    if not catalog.is_absolute() or not catalog.is_file():
        raise ValueError(f'An installed, readable absolute catalog path is required: {catalog}')
    path = Path(config) if config else board_root / 'dispatch.yaml'
    changed = update_config(path, 'skills', lambda mapping: register_catalog(mapping, catalog),
                            apply=apply, backup_dir=backup_dir,
                            validate=lambda candidate: doctor(board_bin, candidate, user, group))
    print(f'{"Configured" if apply else "Validated"} Board catalog {catalog}; other settings and session data preserved.')
    return changed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board-root', type=Path, default=Path('/opt/board'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--skills-dir', choices=list(MANAGED_CATALOGS), default='/opt/research-library/skills', help='Compatibility alias for the managed catalog path')
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--user', help='Actual dispatcher account for local doctor checks')
    parser.add_argument('--group', help='Dispatcher primary group, when configured')
    parser.add_argument('--check-native', action='store_true', help='Check Board support without registering a catalog')
    parser.add_argument('--backup-dir', type=Path, default=Path('/var/lib/stages/board-config-backups'))
    parser.add_argument('--board-bin', type=Path, help='Override the installed native Board executable')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        configure(args.board_root, args.apply, args.skills_dir, catalog=args.catalog,
                  config=args.config, user=args.user, group=args.group, check_native=args.check_native,
                  backup_dir=args.backup_dir, board_command=args.board_bin)
    except (OSError, ValueError) as exc:
        raise SystemExit(f'Cannot configure Board skills: {exc}') from exc
