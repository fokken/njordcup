import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.logging_setup import logging_session
from njordcup.provider import OpenAIProvider
from njordcup.agent import SCHEMA
from test_scale import AutomaticProvider


class LoggingTests(unittest.TestCase):
    def test_default_progress_keeps_stdout_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'app.py').write_text('private_source_marker()')
            with patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO) as err:
                self.assertEqual(main([tmp, '--dry-run']), 0)
            self.assertEqual(json.loads(out.getvalue())['status'], 'dry_run')
            self.assertIn('Discovery complete:', err.getvalue())
            self.assertIn('Index ready:', err.getvalue())
            self.assertIn('Execution finished', err.getvalue())
            self.assertNotIn('private_source_marker', err.getvalue())

    def test_verbose_checkpoints_and_quiet_progress(self):
        for flag in ('--verbose', '--quiet'):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / 'app.py').write_text('safe()')
                with patch('njordcup.cli.OpenAIProvider', return_value=AutomaticProvider()), patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO) as err:
                    self.assertEqual(main([tmp, '--model', 'test', '--automatic', flag]), 0)
                self.assertEqual(json.loads(out.getvalue())['status'], 'complete')
                if flag == '--verbose':
                    self.assertIn('Checkpoint saved for area', err.getvalue())
                    self.assertIn('Model stage: discover', err.getvalue())
                else:
                    self.assertEqual(err.getvalue(), '')

    def test_logger_state_is_restored_between_invocations(self):
        logger = logging.getLogger('njordcup')
        before = (logger.handlers[:], logger.level, logger.propagate)
        with patch('sys.stderr', new_callable=io.StringIO) as err:
            for _ in range(2):
                with logging_session():
                    logger.info('one message')
            self.assertEqual(err.getvalue().count('one message'), 2)
        self.assertEqual((logger.handlers, logger.level, logger.propagate), before)

    def test_model_logs_timing_and_cache_without_payload_or_credentials(self):
        envelope = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'findings': [], 'context_paths': []})}}],
                    'usage': {'prompt_tokens': 123, 'completion_tokens': 45}}
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener, patch.dict('os.environ', {'TEST_LOG_KEY': 'credential_marker'}), patch('sys.stderr', new_callable=io.StringIO) as err:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(envelope).encode())
            with logging_session():
                logging.getLogger('njordcup').setLevel(logging.DEBUG)
                provider = OpenAIProvider('private_model_marker', base_url='http://localhost:8000/v1', api_key_env='TEST_LOG_KEY', cache=tmp)
                provider.ask('private_prompt_marker', {'source': 'private_source_marker'}, SCHEMA)
                provider.ask('private_prompt_marker', {'source': 'private_source_marker'}, SCHEMA)
            logs = err.getvalue()
            for expected in ('Model request 1/unlimited started', 'received in', 'Request budget:', 'Using cached model response', '123 input / 45 output'):
                self.assertIn(expected, logs)
            for private in ('credential_marker', 'private_model_marker', 'private_prompt_marker', 'private_source_marker', 'http://localhost'):
                self.assertNotIn(private, logs)

    def test_review_error_reason_is_visible_and_saved(self):
        from njordcup.agent import review
        from njordcup.errors import ReviewError
        from test_review import FakeProvider
        from copy import deepcopy
        saved = []
        reason = 'Model response truncated (finish_reason=length)'
        with patch('sys.stderr', new_callable=io.StringIO) as err, logging_session():
            report = review({'app.py': 'safe()'}, ['app.py'], [], FakeProvider(ReviewError(reason)),
                            checkpoint=lambda result: saved.append(deepcopy(result)))
        self.assertIn(reason, err.getvalue())
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(saved[-1]['errors'], [reason])
        self.assertEqual(saved[-1]['unreviewed'], ['app.py'])

    def test_log_file_tees_progress_appends_and_excludes_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            path = root / 'runtime.log'
            for _ in range(2):
                with patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO) as err:
                    self.assertEqual(main([tmp, '--dry-run', '--log-file', str(path)]), 0)
                self.assertEqual(json.loads(out.getvalue())['targets'], ['app.py'])
                self.assertIn('Discovery complete', err.getvalue())
            self.assertEqual(path.read_text().count('Discovery complete'), 2)
            self.assertEqual(path.read_text().count('Execution finished'), 2)
            self.assertNotIn('"status": "dry_run"', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_log_file_includes_plain_notifications_and_restores_stderr(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stderr', new_callable=io.StringIO) as err:
            path = Path(tmp) / 'runtime.log'
            with logging_session() as attach:
                attach(path)
                print('Potential issue saved: example', file=sys.stderr)
                logging.getLogger('njordcup').warning('Example warning')
            self.assertIs(sys.stderr, err)
            self.assertEqual(path.read_text(), err.getvalue())
            self.assertIn('Potential issue saved', path.read_text())
            with logging_session():
                logging.getLogger('njordcup').info('outside file session')
            self.assertNotIn('outside file session', path.read_text())

    def test_log_file_rejects_collisions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stderr', new_callable=io.StringIO):
            root = Path(tmp)
            trace = root / 'trace.jsonl'
            for extra in (['--log-file', str(root / '.njordcup/memory.json')],
                          ['--log-file', str(trace), '--trace-file', str(trace)]):
                self.assertEqual(main([tmp, '--dry-run', *extra]), 2)
            target = root / 'existing.log'
            target.write_text('untouched')
            link = root / 'link.log'
            link.symlink_to(target)
            self.assertEqual(main([tmp, '--dry-run', '--log-file', str(link)]), 2)
            self.assertEqual(target.read_text(), 'untouched')
