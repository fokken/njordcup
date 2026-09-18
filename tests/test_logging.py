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
            for expected in ('Model request 1/20 started', 'received in', 'Request budget:', 'Using cached model response', '123 input / 45 output'):
                self.assertIn(expected, logs)
            for private in ('credential_marker', 'private_model_marker', 'private_prompt_marker', 'private_source_marker', 'http://localhost'):
                self.assertNotIn(private, logs)
