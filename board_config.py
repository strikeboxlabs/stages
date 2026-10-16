"""Config-only migration helpers. Never edit Board application source or auth files."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def board_argv(command):
    return [str(part) for part in command] if isinstance(command, (list, tuple)) else [str(command)]


def require_native(board_bin, runner=subprocess.run):
    result = runner(board_argv(board_bin) + [ 'skills', 'doctor', '--help'],
                    text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('This Board build lacks native skill profiles. Upgrade Board first; old adapter patches and backups are preserved. No application source is changed.')


def doctor(board_bin, config, user=None, group=None, runner=subprocess.run):
    argv = board_argv(board_bin) + ['skills', 'doctor', '--config', str(config), '--json']
    if user:
        import pwd
        account = pwd.getpwnam(user)
        prefix = ['runuser', '-u', user]
        if group:
            prefix += ['-g', group]
        argv = prefix + ['--', 'env', '-u', 'PI_CODING_AGENT_DIR',
                         'HOME=' + account.pw_dir,
                         'XDG_CONFIG_HOME=' + account.pw_dir + '/.config',
                         'XDG_CACHE_HOME=' + account.pw_dir + '/.cache',
                         'XDG_DATA_HOME=' + account.pw_dir + '/.local/share',
                         'XDG_STATE_HOME=' + account.pw_dir + '/.local/state',
                         'PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'] + argv
    metadata = Path(config).stat()
    if user and hasattr(os, 'geteuid') and os.geteuid() == 0:
        # The config candidate may inherit root ownership from an old deployment.
        # Validate as the real worker, then restore ownership before publication.
        os.chown(config, account.pw_uid, account.pw_gid)
    try:
        result = runner(argv, text=True, capture_output=True, timeout=60, cwd=str(Path(config).parent))
    finally:
        if user and hasattr(os, 'geteuid') and os.geteuid() == 0:
            os.chown(config, metadata.st_uid, metadata.st_gid)
    if result.returncode:
        # Do not echo configuration/validation output: other config fields can contain credentials.
        raise ValueError('Board skill doctor rejected the proposed configuration or worker access. Run board skills doctor --config PATH --json as the dispatcher account for details. No config changed.')
    try:
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or any(not isinstance(report.get(field), list) for field in ['errors', 'warnings', 'profiles']):
            raise ValueError('Unexpected doctor report schema')
        if report['errors']:
            raise ValueError('Doctor returned errors')
        return report
    except (ValueError, TypeError) as exc:
        raise ValueError('Board skill doctor did not return JSON; verify the native Board installation.') from exc


def change_section(source, section, mutate):
    import yaml
    document = yaml.safe_load(source)
    tree = yaml.compose(source)
    if not isinstance(document, dict) or not isinstance(tree, yaml.MappingNode):
        raise ValueError('dispatch.yaml must contain a mapping.')
    keys = [key.value for key, _ in tree.value]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate top-level YAML keys must be resolved before migration.')
    old = document.get(section, {})
    if old is None:
        old = {}
    if not isinstance(old, dict):
        raise ValueError(f'{section} must be a mapping.')
    updated = deepcopy(old)
    mutate(updated)
    if updated == old:
        return source
    replacement = yaml.safe_dump({section: updated}, sort_keys=False, allow_unicode=True)
    lines = source.splitlines(keepends=True)
    node = next(((key, value) for key, value in tree.value if key.value == section), None)
    if node:
        key, value = node
        # YAML end marks can point to the next top-level line. Preserve that line.
        end = value.end_mark.line + (1 if value.end_mark.column else 0)
        # Composer end marks include comments before the next key; retain them.
        while end > key.start_mark.line + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith('#')):
            end -= 1
        result = ''.join(lines[:key.start_mark.line]) + replacement + ''.join(lines[end:])
    else:
        result = source.rstrip('\n') + '\n\n' + replacement
    parsed = yaml.safe_load(result)
    expected = deepcopy(document)
    expected[section] = updated
    if parsed != expected:
        raise ValueError('YAML migration would affect unrelated configuration; resolve aliases/formatting manually.')
    return result


def update_config(path, section, mutate, *, apply=False, validate=None, backup_dir=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f'Refusing a symlinked dispatch configuration: {path}')
    source = path.read_text(encoding='utf-8')
    updated = change_section(source, section, mutate)
    metadata = path.stat()
    # Validate a complete candidate before publishing or creating any backup.
    # The candidate stays beside the config, keeping relative path behavior unchanged.
    descriptor, name = tempfile.mkstemp(prefix='.dispatch-check-', suffix='.yaml', dir=path.parent)
    candidate = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as output:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
            if hasattr(os, 'fchmod'):
                os.fchmod(output.fileno(), 0o600)
            if hasattr(os, 'geteuid') and os.geteuid() == 0:
                os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
        if validate:
            validate(candidate)
        if source == updated or not apply:
            return source != updated
        if path.read_text(encoding='utf-8') != source:
            raise ValueError('dispatch.yaml changed during validation; refusing to overwrite concurrent edits.')
        backup_root = Path(backup_dir) if backup_dir else path.parent
        if backup_root.is_symlink():
            raise ValueError(f'Refusing a symlinked backup directory: {backup_root}')
        backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if backup_dir and os.name == 'posix' and backup_root.stat().st_mode & 0o077:
            raise ValueError(f'Configuration backup directory must be owner-only: {backup_root}')
        backup = backup_root / (path.name + '.before-native-skills-' + hashlib.sha256(source.encode()).hexdigest()[:12])
        if os.path.lexists(backup):
            if backup.is_symlink() or backup.read_text(encoding='utf-8') != source:
                raise ValueError(f'Refusing a conflicting configuration backup: {backup}')
        else:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as output:
                output.write(source)
                if hasattr(os, 'geteuid') and os.geteuid() == 0:
                    os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
        if hasattr(os, 'chmod'):
            candidate.chmod(metadata.st_mode & 0o777)
        os.replace(candidate, path)
        return True
    finally:
        if candidate.exists():
            candidate.unlink()
