#!/usr/bin/env python3
"""Prepare the default public SAST checkout or validate an explicitly selected tree."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

REPOSITORY = 'https://github.com/strikeboxlabs/sast.git'
TOOLS = {'semgrep', 'codeql', 'bandit', 'gosec', 'brakeman', 'cppcheck', 'trivy'}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate catalog JSON key: {key}')
        result[key] = value
    return result


def validate(checkout, *, existing=False):
    if not existing:
        if checkout.is_symlink() or not (checkout / '.git').is_dir():
            raise ValueError(f'Refusing an unmanaged SAST checkout: {checkout}')
        origin = subprocess.check_output(['git', '-C', str(checkout), 'remote', 'get-url', 'origin'], text=True).strip()
        if origin.removesuffix('.git') != REPOSITORY.removesuffix('.git'):
            raise ValueError(f'Refusing a different Git origin at {checkout}')
    if not (checkout / 'catalog.json').is_file() or not any((checkout / 'skills').glob('*/SKILL.md')):
        raise ValueError(f'Expected catalog.json and skills/*/SKILL.md in {checkout}')
    data = json.loads((checkout / 'catalog.json').read_text(encoding='utf-8-sig'), object_pairs_hook=unique_pairs)
    if (not isinstance(data, dict) or set(data) != {'schema_version', 'library_id', 'skills'}
            or type(data.get('schema_version')) is not int or data['schema_version'] != 1
            or data.get('library_id') != 'sast' or not isinstance(data.get('skills'), list)):
        raise ValueError('Expected a schema-version-1 SAST catalog object.')
    names = []
    for entry in data['skills']:
        if not isinstance(entry, dict) or set(entry) != {'id', 'path', 'tags', 'requires'}:
            raise ValueError('Invalid SAST catalog entry.')
        name = entry['id']
        if not isinstance(name, str) or name not in TOOLS or entry['path'] != 'skills/' + name:
            raise ValueError('SAST catalog IDs and paths must match the seven supported tools.')
        names.append(name)
        for field in ['tags', 'requires']:
            values = entry[field]
            if (not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f'Invalid unique string array: {field}')
        package = (checkout / entry['path']).resolve()
        if not package.is_relative_to(checkout.resolve()) or not (package / 'SKILL.md').is_file():
            raise ValueError(f'Missing or escaping SAST skill package: {name}')
    if len(names) != len(TOOLS) or set(names) != TOOLS:
        raise ValueError('SAST catalog must contain each supported tool exactly once.')


def prepare(checkout, *, existing=False, apply=False):
    checkout = Path(checkout)
    if not checkout.is_absolute():
        raise ValueError('The SAST checkout path must be absolute.')
    if os.path.lexists(checkout):
        validate(checkout, existing=existing)
        print(f'Reusing {checkout}; revision and local edits are preserved.')
        return
    if existing:
        raise ValueError(f'The explicitly selected SAST tree does not exist: {checkout}')
    if not apply:
        print(f'Would clone {REPOSITORY} into {checkout}; full catalog preflight follows the clone.')
        return
    with tempfile.TemporaryDirectory(prefix='.sast-checkout-', dir=checkout.parent) as temporary:
        clone = Path(temporary) / 'checkout'
        subprocess.run(['git', 'clone', '--', REPOSITORY, str(clone)], check=True, umask=0o022)
        validate(clone)
        if os.path.lexists(checkout):
            raise ValueError(f'SAST destination appeared during clone; refusing replacement: {checkout}')
        clone.rename(checkout)
    print(f'Prepared {checkout}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, default=Path('/opt/sast'))
    parser.add_argument('--existing', action='store_true', help='Trust a selected existing tree; Git metadata is optional')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        prepare(args.checkout, existing=args.existing, apply=args.apply)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Cannot prepare SAST checkout: {exc}') from exc
