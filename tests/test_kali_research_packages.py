"""Resolve fixture command names without network access or package installation."""
import io
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'kali-research-packages.py'))


def package(origin='Kali'):
    return SimpleNamespace(candidate=SimpleNamespace(origins=[SimpleNamespace(origin=origin)]),
                           is_installed=False)


class KaliPackages(unittest.TestCase):
    def test_resolves_aliases_dependencies_and_defers_missing_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tool in ['netexec', 'missing-tool', 'umask', 'private-tool']:
                path = root / 'skills' / tool
                path.mkdir(parents=True)
                (path / 'SKILL.md').write_text('~~~bash\ncurl --version\n~~~\n')
            cache = {'netexec': package(), 'bash': package(), 'curl': package(),
                     'private-tool': package('ThirdParty')}
            process = SimpleNamespace(stdout=io.StringIO('usr/bin/nxc net/netexec\nusr/bin/curl web/curl\n'),
                                      wait=lambda: 0)
            function = MODULE['resolve']
            with patch.object(function.__globals__['subprocess'], 'Popen', return_value=process):
                plan = function(root, cache, [Path('fixture-Contents')])
            self.assertEqual(plan['packages'], ['bash', 'curl', 'netexec'])
            self.assertEqual(plan['resolved']['netexec']['package'], 'netexec')
            self.assertEqual(plan['deferred'], ['missing-tool', 'private-tool'])

    def test_prose_backticks_are_not_treated_as_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'skills/example'
            path.mkdir(parents=True)
            (path / 'SKILL.md').write_text('Review `root` and `version` and `COPY`.\n')
            self.assertEqual(set(MODULE['inventory'](root)), {'example'})

    def test_missing_contents_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'skills/example'
            path.mkdir(parents=True)
            (path / 'SKILL.md').touch()
            with self.assertRaisesRegex(ValueError, 'Missing APT Contents'):
                MODULE['resolve'](root, {}, [])
