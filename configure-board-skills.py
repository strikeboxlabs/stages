#!/usr/bin/env python3
"""Add the shared skills to Board's local Pi launches without changing other flags."""
import argparse
import ast
import hashlib
import os
from pathlib import Path
import tempfile


# Exact known _local_argv methods, before/after the project-session integration.
WITHOUT_SKILLS = {
    'b3b42476a2ca60f1ff7c2ba9f7df1eac0a5b6c805ede87e8f10f963c7f7802c8',
    '35afc65114db64316ada2dbf150cecdceaedc03525d89ae5e654dcabacfaf4a2',
}
WITH_SKILLS = {
    'b9a0a36be4ad183178b4755e5d2bc73a5ca84e2ba1ad24d44871d9eaeca3db8a',
    '71ca9680994ba2a3f8e6be030bb9e2780608bffa4477587465e31a6831d06b58',
}


def patch_source(source):
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PiDriver')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_local_argv')
    lines = source.splitlines(keepends=True)
    original = ''.join(lines[method.lineno - 1:method.end_lineno])
    digest = hashlib.sha256(original.encode()).hexdigest()
    if digest in WITH_SKILLS:
        return source
    if digest not in WITHOUT_SKILLS:
        raise ValueError('Board PiDriver._local_argv changed; review the skill integration before applying it. No files changed.')
    anchor = '            "--no-skills",\n'
    assert original.count(anchor) == 1
    replacement = original.replace(anchor, anchor + '            "--skill",\n            "/opt/research-library/skills",\n')
    result = ''.join(lines[:method.lineno - 1]) + replacement + ''.join(lines[method.end_lineno:])
    ast.parse(result)
    return result


def configure(board_root, apply=False):
    path = board_root / 'board/src/board/dispatcher/workers/adapters/pi.py'
    if path.is_symlink():
        raise ValueError(f'Refusing symlinked adapter: {path}')
    source = path.read_text()
    updated = patch_source(source)
    if updated == source:
        print('Board local Pi research skills already configured.')
        return False
    if not apply:
        print('Pi adapter is compatible; would explicitly load /opt/research-library/skills.')
        return True
    metadata = path.stat()
    backup = path.with_name(path.name + '.before-research-skills-' + hashlib.sha256(source.encode()).hexdigest()[:12])
    if os.path.lexists(backup):
        if backup.is_symlink() or backup.read_text() != source:
            raise ValueError(f'Refusing conflicting adapter backup: {backup}')
    else:
        with backup.open('x') as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(source)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), metadata.st_mode & 0o777)
            if os.geteuid() == 0:
                os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f'Board local Pi skills configured; backup: {backup}')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board-root', type=Path, default=Path('/opt/board'))
    parser.add_argument('--apply', action='store_true', help='Apply with a backup; otherwise check only')
    args = parser.parse_args()
    try:
        configure(args.board_root, args.apply)
    except (ValueError, OSError, StopIteration, SyntaxError) as exc:
        raise SystemExit(f'Cannot configure Board research skills: {exc}') from exc
