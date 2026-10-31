#!/usr/bin/python3
# Managed by stages SSH setup.
import argparse
import json
import shlex
import subprocess


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def configure(ports, *, hermes=False):
    ports = sorted({22, *ports, *([8642, 9119] if hermes else [])})
    if not all(1 <= port <= 65535 for port in ports):
        raise SystemExit('Invalid TCP port')
    # Retain the original tag so reruns replace rules from earlier stage versions.
    tag = 'stage4-ssh-port' if hermes else 'stage1-ssh-port'
    ruleset = json.loads(run('/usr/sbin/nft', '-j', '-a', 'list', 'ruleset',
                             capture_output=True).stdout)['nftables']
    commands = []
    # Accept in every IPv4 input base chain: an accept in one base chain does
    # not bypass a later base chain's drop policy. Never flush unrelated rules.
    for entry in ruleset:
        rule = entry.get('rule', {})
        if rule.get('comment') == tag and rule.get('family') in ('ip', 'inet'):
            commands.append({'delete': {'rule': {key: rule[key] for key in
                             ('family', 'table', 'chain', 'handle')}}})
    for entry in ruleset:
        chain = entry.get('chain', {})
        if chain.get('hook') != 'input' or chain.get('family') not in ('ip', 'inet'):
            continue
        rule = {key: chain[key] for key in ('family', 'table')}
        rule.update(chain=chain['name'], comment=tag, expr=[
            {'match': {'op': '==', 'left': {'meta': {'key': 'nfproto'}}, 'right': 'ipv4'}},
            {'match': {'op': '==', 'left': {'payload': {'protocol': 'tcp', 'field': 'dport'}},
                       'right': {'set': ports}}},
            {'accept': None},
        ])
        commands.append({'insert': {'rule': rule}})
    if commands:
        run('/usr/sbin/nft', '-j', '-f', '-', input=json.dumps({'nftables': commands}))

    # Legacy iptables can coexist with nftables. Remove only our own earlier
    # allowance (including a previous port), then insert the current allowance.
    legacy = '/usr/sbin/iptables-legacy'
    for line in run(legacy, '-w', '-S', 'INPUT', capture_output=True).stdout.splitlines():
        args = shlex.split(line)
        if '--comment' in args and args[args.index('--comment') + 1] == tag:
            run(legacy, '-w', '-D', *args[1:])
    for port in ports:
        run(legacy, '-w', '-I', 'INPUT', '1', '-p', 'tcp', '--dport', str(port),
            '-m', 'comment', '--comment', tag, '-j', 'ACCEPT')
    stage = 'stage4' if hermes else 'stage1'
    print(f'[{stage}] Local IPv4 input firewall allows TCP ports {ports}.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Allow stage SSH ports without flushing unrelated firewall rules.')
    parser.add_argument('--hermes', action='store_true', help='Also allow Hermes ports; preserve stage-1 rules.')
    parser.add_argument('ports', type=int, nargs='*')
    args = parser.parse_args()
    configure(args.ports, hermes=args.hermes)
