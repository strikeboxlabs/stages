"""SSH bootstrap checks using temporary configs; never start a daemon or change the host firewall."""
import contextlib
import io
import json
import os
from pathlib import Path
import pty
import runpy
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / 'lib/ssh-bootstrap.sh'
FIREWALL = runpy.run_path(str(ROOT / 'configure-ssh-firewall.py'))
SSHD = shutil.which('sshd') or '/usr/sbin/sshd'


class BootstrapPlanning(unittest.TestCase):
    @unittest.skipUnless(os.geteuid() == 0, 'Stage 1 requires root')
    def test_stage1_sets_up_ssh_before_any_disk_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'actions'
            guard = Path(tmp) / 'guard.sh'
            guard.write_text('''
bash() { printf 'ssh-bootstrap %s\\n' "$*" >> "$ACTIONS"; }
findmnt() { echo disk-check >> "$ACTIONS"; return 91; }
apt-get() { echo disk-dependency-check >> "$ACTIONS"; return 91; }
''')
            result = subprocess.run(['bash', str(ROOT / 'stage1.sh')],
                                    env={**os.environ, 'BASH_ENV': str(guard), 'ACTIONS': str(log)},
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)  # Deliberate disk/preflight failure.
            actions = log.read_text().splitlines()
            self.assertEqual(actions[0], f'ssh-bootstrap {BOOTSTRAP}')
            self.assertIn(actions[1], ['disk-check', 'disk-dependency-check'])

    def test_dry_run_needs_no_agents_and_runs_no_system_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'mutations'
            guard = Path(tmp) / 'guard.sh'
            guard.write_text(''.join(
                name + '() { printf forbidden >> "$MUTATIONS"; return 91; }\n'
                for name in ('apt-get', 'passwd', 'ssh-keygen', 'sshd', 'systemctl',
                             'install', 'nft', 'iptables-legacy', 'pi', 'hermes')))
            result = subprocess.run(['bash', str(BOOTSTRAP), '--dry-run'],
                                    env={**os.environ, 'BASH_ENV': str(guard), 'MUTATIONS': str(log)},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('root password login', result.stdout)
            self.assertFalse(log.exists())

    def test_existing_root_password_is_preserved_and_locked_root_requires_console(self):
        for state in ('P', 'L', 'NP'):
            with self.subTest(state=state):
                fixture = '''
source "$BOOTSTRAP"
passwd() {
    [[ "$*" == '-S root' ]] || { echo PASSWORD_CHANGED; return 91; }
    printf 'root %s 2026-01-01 0 99999 7 -1\n' "$PASSWORD_STATE"
}
ensure_root_password
'''
                result = subprocess.run(['bash', '-c', fixture],
                                        env={**os.environ, 'BOOTSTRAP': str(BOOTSTRAP), 'PASSWORD_STATE': state},
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
                self.assertNotIn('PASSWORD_CHANGED', result.stdout)
                if state == 'P':
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('passwd root in the VM console', result.stderr)

    def test_locked_root_prompts_in_console_and_verifies_new_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            changed = Path(tmp) / 'password-set'
            fixture = '''
source "$BOOTSTRAP"
passwd() {
    if [[ "$*" == '-S root' ]]; then
        if [[ -f "$CHANGED" ]]; then printf 'root P\\n'; else printf 'root L\\n'; fi
    elif [[ "$*" == root ]]; then
        touch "$CHANGED"
    else
        return 91
    fi
}
ensure_root_password
'''
            master, slave = pty.openpty()
            try:
                result = subprocess.run(['bash', '-c', fixture], stdin=slave,
                                        env={**os.environ, 'BOOTSTRAP': str(BOOTSTRAP), 'CHANGED': str(changed)},
                                        capture_output=True, text=True, timeout=10)
            finally:
                os.close(master)
                os.close(slave)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(changed.exists())
            self.assertIn('Set the root password', result.stdout)


@unittest.skipUnless(Path(SSHD).is_file() and shutil.which('ssh-keygen') and
                     os.geteuid() == 0 and Path('/run/sshd').is_dir(),
                     'Real sshd config validation needs installed OpenSSH and its runtime directory')
class RootLoginConfiguration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dropins = self.root / 'sshd_config.d'
        self.dropins.mkdir()
        self.dropin = self.dropins / '00-stage1-root-login.conf'
        self.key = self.root / 'hostkey'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key)], check=True)
        self.config = self.root / 'sshd_config'
        self.config.write_text(f'Include {self.dropins}/*.conf\nHostKey {self.key}\n'
                               'Port 2222\nUsePAM no\nPermitRootLogin prohibit-password\n'
                               'PasswordAuthentication no\nSubsystem sftp internal-sftp\n')

    def configure(self):
        return subprocess.run(['bash', '-c', '''
source "$BOOTSTRAP"
sshd() { "$SSHD" -f "$SSH_CONFIG" "$@"; }
configure_root_password_login "$SSH_DROPINS"
'''], env={**os.environ, 'BOOTSTRAP': str(BOOTSTRAP), 'SSHD': SSHD,
           'SSH_CONFIG': str(self.config), 'SSH_DROPINS': str(self.dropins)},
            capture_output=True, text=True, timeout=10)

    def effective(self, user):
        result = subprocess.run([SSHD, '-T', '-f', str(self.config), '-C',
                                 f'user={user},host=workstation,addr=192.0.2.10'],
                                check=True, capture_output=True, text=True)
        return dict(line.split(' ', 1) for line in result.stdout.lower().splitlines())

    def test_root_password_login_preserves_other_accounts_and_custom_port_on_reruns(self):
        original = self.config.read_bytes()
        for _ in range(2):
            result = self.configure()
            self.assertEqual(result.returncode, 0, result.stderr)
            root = self.effective('root')
            self.assertEqual(root['permitrootlogin'], 'yes')
            self.assertEqual(root['passwordauthentication'], 'yes')
            self.assertEqual(root['port'], '2222')
            self.assertEqual(self.effective('nobody')['passwordauthentication'], 'no')
            self.assertEqual(self.config.read_bytes(), original)

    def test_rejected_config_restores_previous_managed_dropin(self):
        self.dropin.write_text('# Managed by stage1.sh\n# Previous configuration\n')
        self.dropin.chmod(0o640)
        original = self.dropin.read_bytes()
        (self.dropins / '00-aaa.conf').write_text('Match User root\nPermitRootLogin no\nMatch all\n')
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('previous drop-in was restored', result.stderr)
        self.assertEqual(self.dropin.read_bytes(), original)
        self.assertEqual(self.dropin.stat().st_mode & 0o777, 0o640)

    def test_missing_include_or_multi_factor_policy_does_not_leave_new_dropin(self):
        original = self.config.read_text()
        for text in (original.split('\n', 1)[1], original + 'AuthenticationMethods publickey,password\n'):
            with self.subTest(config=text):
                self.config.write_text(text)
                result = self.configure()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.dropin.exists())

    def test_unmanaged_and_symlinked_dropins_are_preserved(self):
        self.dropin.write_text('# Administrator configuration\n')
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unmanaged file', result.stderr)
        self.assertEqual(self.dropin.read_text(), '# Administrator configuration\n')
        self.dropin.unlink()
        target = self.root / 'outside'
        target.write_text('# Managed by stage1.sh\n')
        self.dropin.symlink_to(target)
        result = self.configure()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(target.read_text(), '# Managed by stage1.sh\n')
        self.assertTrue(self.dropin.is_symlink())


class SshFirewall(unittest.TestCase):
    def test_each_stage_replaces_only_its_own_rules_and_uses_its_own_ports(self):
        ruleset = [
            {'chain': {'family': family, 'table': 'filter', 'name': chain, 'hook': hook}}
            for family, chain, hook in [('ip', 'input', 'input'), ('inet', 'input', 'input'),
                                         ('ip6', 'input', 'input'), ('ip', 'output', 'output')]]
        ruleset += [
            {'rule': {'family': 'inet', 'table': 'filter', 'chain': 'input', 'handle': handle, 'comment': tag}}
            for handle, tag in [(1, 'stage1-ssh-port'), (2, 'stage4-ssh-port'), (3, 'administrator')]]
        legacy_rules = ''.join(f'-A INPUT -p tcp --dport 2200 -m comment --comment {tag} -j ACCEPT\n'
                               for tag in ['stage1-ssh-port', 'stage4-ssh-port', 'administrator'])
        for hermes, tag, ports, handle in [(False, 'stage1-ssh-port', [22, 2222], 1),
                                         (True, 'stage4-ssh-port', [22, 2222, 8642, 9119], 2)]:
            with self.subTest(hermes=hermes):
                calls = []

                def run(*args, **kwargs):
                    calls.append((args, kwargs))
                    output = json.dumps({'nftables': ruleset}) if args[0].endswith('/nft') else legacy_rules
                    return SimpleNamespace(stdout=output)

                configure = FIREWALL['configure']
                with patch.dict(configure.__globals__, run=run), contextlib.redirect_stdout(io.StringIO()):
                    configure([2222], hermes=hermes)
                transaction = next(json.loads(kwargs['input'])['nftables'] for _, kwargs in calls if 'input' in kwargs)
                deleted = [command['delete']['rule']['handle'] for command in transaction if 'delete' in command]
                self.assertEqual(deleted, [handle])
                added = [command['insert']['rule'] for command in transaction if 'insert' in command]
                self.assertEqual(len(added), 2)
                for rule in added:
                    self.assertEqual(rule['comment'], tag)
                    self.assertEqual(rule['expr'][1]['match']['right']['set'], ports)
                legacy_delete = [args for args, _ in calls if '-D' in args]
                self.assertEqual(len(legacy_delete), 1)
                self.assertIn(tag, legacy_delete[0])
                legacy_add = [args for args, _ in calls if '-I' in args]
                self.assertEqual([int(args[args.index('--dport') + 1]) for args in legacy_add], ports)
                self.assertFalse(any('-F' in args or 'flush' in args for args, _ in calls))

    def test_invalid_ports_fail_before_touching_firewall(self):
        configure = FIREWALL['configure']
        for port in [0, -1, 65536]:
            with self.subTest(port=port), patch.dict(configure.__globals__, run=lambda *args, **kwargs: self.fail('Unexpected command')):
                with self.assertRaisesRegex(SystemExit, 'Invalid TCP port'):
                    configure([port])


if __name__ == '__main__':
    unittest.main()
