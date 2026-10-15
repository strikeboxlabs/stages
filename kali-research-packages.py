#!/usr/bin/python3
"""Resolve research skill command names against Kali APT package/Contents indexes.

Reads documentation as data; never executes commands from the library. No downloads
or writes. Run with /usr/bin/python3 (python3-apt), after apt-file update.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

# Skill names that differ from Kali's package or executable names. Exact package
# hints also cover suites/GUI tools without a command matching their skill name.
HINTS = {
    'awk': 'gawk', 'convert': 'imagemagick', 'identify': 'imagemagick',
    'jar': 'default-jdk', 'javac': 'default-jdk', 'jdb': 'default-jdk',
    'keytool': 'default-jre-headless', 'klist': 'krb5-user',
    'peda': 'gdb-peda', 'which': 'gnu-which',
    'systemctl': 'systemd', 'snap': 'snapd', 'kinit': 'krb5-user',
    'sendmail': 'postfix', 'mail': 'bsd-mailx', 'java': 'default-jre-headless',
    'derby-ij': 'derby-tools', 'ipython': 'ipython3', 'iptables-save': 'iptables',
    'jarsigner': 'default-jdk', 'kdestroy': 'krb5-user', 'ksu': 'krb5-user',
    'memcached-tool': 'memcached', 'mogrify': 'imagemagick', 'mosquitto-sub': 'mosquitto-clients',
    'neo4j-admin': 'neo4j', 'vimdiff': 'vim',
    '7z': '7zip', 'ansible-galaxy': 'ansible-core', 'ansible-playbook': 'ansible-core',
    'ansible-vault': 'ansible-core', 'aws': 'awscli', 'az': 'azure-cli',
    'bloodhound-python': 'bloodhound.py', 'certipy': 'certipy-ad',
    'chromedriver': 'chromium-driver', 'docker': 'docker.io',
    'ebook-convert': 'calibre', 'exiftool': 'libimage-exiftool-perl',
    'gem': 'ruby', 'go': 'golang-go', 'grpc-tools-protoc': 'python3-grpc-tools',
    'httpie': 'httpie', 'ip': 'iproute2', 'jwt-tool': 'jwt-tool',
    'latex': 'texlive-latex-base', 'linpeas': 'peass', 'magick': 'imagemagick',
    'mdbtools': 'mdbtools', 'mingw-w64-gcc': 'gcc-mingw-w64',
    'mount-cifs': 'cifs-utils', 'mount-nfs': 'nfs-common',
    'msf-pattern': 'metasploit-framework', 'msfconsole': 'metasploit-framework',
    'msfvenom': 'metasploit-framework', 'mvn': 'maven', 'nc': 'netcat-openbsd',
    'netexec': 'netexec', 'node': 'nodejs', 'pdflatex': 'texlive-latex-base',
    'pip': 'python3-pip', 'powershell': 'powershell', 'pwsh': 'powershell',
    'pwntools': 'python3-pwntools', 'python-http-server': 'python3',
    'r2': 'radare2', 'rabin2': 'radare2', 'scapy': 'python3-scapy',
    'searchsploit': 'exploitdb', 'setuptools': 'python3-setuptools',
    'strongswan-ipsec': 'strongswan-starter', 'umask': 'bash',
    'wpa-supplicant': 'wpasupplicant', 'xfreerdp': 'freerdp3-x11',
    'xml-pp': 'xml-twig-tools',
}
ALIASES = {
    'msf-pattern': ['msf-pattern_create', 'msf-pattern_offset'],
    'netexec': ['nxc'], 'pwntools': ['pwn'], 'mount-cifs': ['mount.cifs'],
    'mount-nfs': ['mount.nfs'], 'lsb-release': ['lsb_release'],
    'mingw-w64-gcc': ['x86_64-w64-mingw32-gcc'], 'qemu-system-x86-64': ['qemu-system-x86_64'],
    'wpa-supplicant': ['wpa_supplicant'], 'xfreerdp': ['xfreerdp3'],
}
TOKEN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.+/-]*$')


def inventory(repo):
    """Include skill entrypoints plus command tokens in backticks and shell blocks."""
    result = defaultdict(set)
    skills = sorted((repo / 'skills').glob('*/SKILL.md'))
    primary = {skill.parent.name for skill in skills}
    for skill in skills:
        result[skill.parent.name].add(str(skill.relative_to(repo)))
    for skill in sorted((repo / 'skills').rglob('*.md')):
        text = skill.read_text()
        snippets = re.findall(r'(?:~~~|```)\s*(?:bash|sh|shell)\s*\n(.*?)(?:~~~|```)', text, re.S)
        for snippet in snippets:
            # Collect command positions, including pipelines and semicolon lists.
            for segment in re.split(r'[\n;|&]+', snippet):
                try:
                    words = shlex.split(segment, comments=True)
                except ValueError:
                    continue
                while words and (words[0] in ('sudo', 'command', 'exec', 'env') or '=' in words[0]):
                    words.pop(0)
                if words and TOKEN.fullmatch(words[0]) and not words[0].startswith(('-', './')):
                    name = Path(words[0]).name
                    if name in primary or (name == name.lower() and len(name) > 1
                                           and any(word.startswith('-') for word in words[1:])):
                        result[name].add(str(skill.relative_to(repo)))
    if not result:
        raise ValueError('No research skill entrypoints found.')
    return result


def resolve(repo, cache, contents):
    names = inventory(repo)
    primary = {p.parent.name for p in (repo / 'skills').glob('*/SKILL.md')}
    commands = defaultdict(set)
    for name in names:
        for command in [name, name + '.py', name + '.rb', *ALIASES.get(name, [])]:
            commands[command.casefold()].add(name)
    matches = defaultdict(set)
    paths = defaultdict(set)

    def candidate(package):
        if package not in cache or cache[package].candidate is None:
            return False
        return any(origin.origin == 'Kali' for origin in cache[package].candidate.origins)

    if not contents:
        raise ValueError('Missing APT Contents indexes; install apt-file and run apt-file update.')
    for index in contents:
        process = subprocess.Popen(['/usr/lib/apt/apt-helper', 'cat-file', str(index)],
                                   stdout=subprocess.PIPE, text=True)
        try:
            for line in process.stdout:
                fields = line.rstrip().rsplit(None, 1)
                if len(fields) != 2:
                    continue
                path, owners = fields
                basename = path.rsplit('/', 1)[-1].casefold()
                if basename not in commands:
                    continue
                # CLI entrypoints and packaged tool scripts; exclude documentation,
                # test fixtures and source/examples that merely share a basename.
                if not re.match(r'^(?:usr/)?s?bin/', path):
                    continue
                for owner in owners.split(','):
                    package = owner.rsplit('/', 1)[-1]
                    if candidate(package):
                        for name in commands[basename]:
                            matches[name].add(package)
                            paths[(name, package)].add('/' + path)
        finally:
            process.stdout.close()
        if process.wait():
            raise ValueError(f'Could not read Contents index: {index}')
    resolved, missing = {}, []
    for name in sorted(names):
        # Homonymous Kali packages are unrelated to these skills (Foundry / Arch).
        if name in ('cast', 'forge', 'pacman'):
            if name in primary:
                missing.append(name)
            continue
        options = matches[name]
        hint = HINTS.get(name, name.casefold())
        if candidate(hint):
            package = hint
        elif options:
            # Prefer an already installed provider, then an exact package name.
            package = min(options, key=lambda p: (not cache[p].is_installed,
                                                  p != name.casefold(), len(p), p))
        else:
            if name in primary:
                missing.append(name)
            continue
        resolved[name] = {'package': package, 'paths': sorted(paths[(name, package)]),
                          'skills': sorted(names[name])}
    return {'packages': sorted({entry['package'] for entry in resolved.values()}),
            'resolved': resolved, 'deferred': missing}


def main():
    import apt
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('/opt/research-library'))
    parser.add_argument('--packages-only', action='store_true')
    args = parser.parse_args()
    plan = resolve(args.repo, apt.Cache(), sorted(Path('/var/lib/apt/lists').glob('*Contents*')))
    if args.packages_only:
        print('\n'.join(plan['packages']))
    else:
        print(json.dumps(plan, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, ImportError) as exc:
        sys.exit(f'Cannot resolve Kali research packages: {exc}')
