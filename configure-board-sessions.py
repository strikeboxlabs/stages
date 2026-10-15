#!/usr/bin/env python3
"""Install the local Pi session-directory integration without replacing other Board changes."""
import argparse
import ast
import hashlib
import os
from pathlib import Path
import tempfile


ORIGINAL_METHOD_SHA256 = 'b3b42476a2ca60f1ff7c2ba9f7df1eac0a5b6c805ede87e8f10f963c7f7802c8'
PATCHED_METHOD_SHA256 = '35afc65114db64316ada2dbf150cecdceaedc03525d89ae5e654dcabacfaf4a2'
# The same two known methods, with the explicit research-library skill path.
ORIGINAL_WITH_SKILLS_METHOD_SHA256 = 'b9a0a36be4ad183178b4755e5d2bc73a5ca84e2ba1ad24d44871d9eaeca3db8a'
PATCHED_WITH_SKILLS_METHOD_SHA256 = '71ca9680994ba2a3f8e6be030bb9e2780608bffa4477587465e31a6831d06b58'
OLD_SCRIPT = '''        script = 'sdir="$1"\\nshift\\nmkdir -p "$sdir"\\nexec pi "$@"\\n'
'''
NEW_SCRIPT = '''        # Stage 5 project-local Pi transcripts, v1. Resolve at process startup:
        # BOARD_PROJECT_DIR is supplied by the backend after argv construction.
        script = (
            'sdir="$1"\\nshift\\n'
            'if [ -n "${BOARD_PROJECT_DIR:-}" ]; then\\n'
            '  sdir="$BOARD_PROJECT_DIR/sessions/pi"\\n'
            'fi\\n'
            'umask 077\\n'
            'mkdir -p "$sdir" || exit 1\\n'
            'exec pi --session-dir "$sdir" "$@"\\n'
        )
'''


def patch_source(source):
    tree = ast.parse(source)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'PiDriver')
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '_local_argv')
    lines = source.splitlines(keepends=True)
    original = ''.join(lines[method.lineno - 1:method.end_lineno])
    sast_block = '            "--skill",\n            "/opt/sast-tools/skills",\n'
    if original.count(sast_block) > 1:
        raise ValueError('Duplicate SAST skill arguments.')
    digest = hashlib.sha256(original.replace(sast_block, '').encode()).hexdigest()
    if digest in (PATCHED_METHOD_SHA256, PATCHED_WITH_SKILLS_METHOD_SHA256):
        return source
    if digest not in (ORIGINAL_METHOD_SHA256, ORIGINAL_WITH_SKILLS_METHOD_SHA256):
        raise ValueError('Board PiDriver._local_argv changed; review the session integration before applying it. No files changed.')
    flags = '            "--session-dir",\n            session_dir,\n'
    assert original.count(flags) == original.count(OLD_SCRIPT) == 1
    replacement = original.replace(flags, '').replace(OLD_SCRIPT, NEW_SCRIPT)
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
        print('Pi project transcripts already configured: <project>/sessions/pi/')
        return False
    if not apply:
        print('Pi adapter is compatible; would store transcripts in <project>/sessions/pi/')
        return True
    metadata = path.stat()
    backup = path.with_name(path.name + '.before-project-sessions-' + hashlib.sha256(source.encode()).hexdigest()[:12])
    if not backup.exists():
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
    print(f'Pi transcripts configured: <project>/sessions/pi/; backup: {backup}')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--board-root', type=Path, default=Path('/opt/board'))
    parser.add_argument('--apply', action='store_true', help='Apply after backing up the adapter; otherwise check only')
    args = parser.parse_args()
    try:
        configure(args.board_root, args.apply)
    except (ValueError, OSError, StopIteration, SyntaxError) as exc:
        raise SystemExit(f'Cannot configure Pi project transcripts: {exc}') from exc
