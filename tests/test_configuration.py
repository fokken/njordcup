import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import build_parser, main
from njordcup.configuration import parse_configuration


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root),
                                          'REVIEW_MODEL': 'environment-model'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.path = self.root / 'njordcup' / 'config.toml'
        self.path.parent.mkdir()

    def parse(self, *args):
        return parse_configuration(build_parser(), list(args))

    def test_precedence_profiles_and_cli_overrides(self):
        self.path.write_text('''default_profile = "local"
[settings]
model = "configured"
workers = 2
[profiles.local]
model = "profile-model"
max_tokens = 4096
[profiles.other]
model = "other-model"
''')
        self.assertEqual(self.parse().model, 'profile-model')
        self.assertEqual(self.parse().workers, 2)
        self.assertEqual(self.parse().max_tokens, 4096)
        self.assertEqual(self.parse('--profile', 'other').model, 'other-model')
        self.assertEqual(self.parse('--model', 'explicit', '--workers', '3').workers, 3)
        self.assertEqual(self.parse('--model', 'explicit').model, 'explicit')
        self.assertEqual(self.parse('--no-config').model, 'environment-model')
        self.path.write_text('[settings]\nmodel = "configured"\n')
        self.assertEqual(self.parse().model, 'configured')

    def test_explicit_config_replaces_user_config_and_resolves_paths(self):
        self.path.write_text('[settings]\nworkers = 4\n')
        explicit = self.root / 'custom.toml'
        explicit.write_text('[settings]\ncache = "responses"\nlog_file = "run.log"\n')
        args = self.parse('--config', str(explicit))
        self.assertEqual(args.workers, 1)
        self.assertEqual(args.cache, self.root / 'responses')
        self.assertEqual(args.log_file, self.root / 'run.log')
        self.assertEqual(self.parse('--config', str(explicit), '--cache', 'cli-cache').cache, Path('cli-cache'))

    def test_invalid_settings_fail_before_execution(self):
        cases = ['[settings]\nworkers = 0', '[settings]\nworkers = true',
                 '[settings]\nworkers = 1.5', '[settings]\nmax_tokens = "4096"',
                 '[settings]\noutput_mode = "invalid"', '[settings]\nmodel = 12',
                 '[settings]\nmax_seconds = nan', '[settings]\nautomatic = true',
                 '[settings]\nunknown = 1', 'default_profile = "missing"',
                 'profiles = 1', '[profiles.bad]\nworkers = -1', 'invalid = 1',
                 'settings = "bad"', 'not valid toml']
        for value in cases:
            with self.subTest(value=value):
                self.path.write_text(value)
                with patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit) as error:
                    self.parse()
                self.assertEqual(error.exception.code, 2)

    def test_missing_config_profiles_and_conflicts(self):
        for args in [('--config', str(self.root / 'missing')), ('--profile', 'absent'),
                     ('--no-config', '--profile', 'x'), ('--no-config', '--config', 'x')]:
            with self.subTest(args=args), patch('sys.stderr', new_callable=io.StringIO), self.assertRaises(SystemExit):
                self.parse(*args)

    def test_help_is_compact_and_works_with_broken_config(self):
        self.path.write_text('invalid toml')
        for flag, advanced in [('--help', False), ('--help-all', True)]:
            with patch('sys.stdout', new_callable=io.StringIO) as out, self.assertRaises(SystemExit) as error:
                self.parse(flag)
            self.assertEqual(error.exception.code, 0)
            self.assertEqual('--max-input-chars' in out.getvalue(), advanced)
            self.assertIn('--audit-only', out.getvalue())

    def test_repository_config_is_not_loaded(self):
        repo = self.root / 'repo'
        repo.mkdir()
        (repo / 'config.toml').write_text('[settings]\nmodel = "untrusted"')
        (repo / '.njordcup').mkdir()
        (repo / '.njordcup' / 'config.toml').write_text('invalid toml')
        with patch('pathlib.Path.cwd', return_value=repo):
            self.assertEqual(self.parse(str(repo)).model, 'environment-model')

    def test_relative_xdg_path_cannot_select_repository_configuration(self):
        home = self.root / 'home'
        user_file = home / '.config' / 'njordcup' / 'config.toml'
        user_file.parent.mkdir(parents=True)
        user_file.write_text('[settings]\nmodel = "user-model"')
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '.'}), patch('pathlib.Path.home', return_value=home):
            self.assertEqual(self.parse().model, 'user-model')

    def test_cli_index_with_configuration_needs_no_model_calls(self):
        repo = self.root / 'repo'
        repo.mkdir()
        (repo / 'app.py').write_text('print(1)')
        self.path.write_text('[settings]\nindex_mode = "text"\nworkers = 2')
        with patch('sys.stdout', new_callable=io.StringIO), patch('urllib.request.build_opener') as opener:
            self.assertEqual(main([str(repo), '--index-only']), 0)
            opener.assert_not_called()
