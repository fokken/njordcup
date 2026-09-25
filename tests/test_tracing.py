import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from njordcup.agent import SCHEMA
from njordcup.cli import main
from njordcup.errors import ReviewError
from njordcup.provider import OpenAIProvider
from njordcup.tracing import TraceLog


def response():
    return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
        'content': json.dumps({'findings': [], 'context_paths': []})}}]}).encode())


class TracingTests(unittest.TestCase):
    def test_retry_bodies_correlate_without_authorization_headers(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener, patch.dict('os.environ', {'TRACE_KEY': 'secret_header_value'}):
            path = Path(tmp) / 'trace.jsonl'
            opener.return_value.open.side_effect = [urllib.error.HTTPError('http://localhost/v1', 429, 'busy', {}, io.BytesIO(b'busy body')), response()]
            provider = OpenAIProvider('test', base_url='http://localhost/v1', api_key_env='TRACE_KEY', trace_file=path)
            with patch.object(provider.control, 'wait'):
                provider.ask('full instructions', {'source': 'full source'}, SCHEMA)
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([e['event'] for e in events], ['session_start', 'request', 'response', 'request', 'response'])
            self.assertEqual(events[1]['request_id'], events[2]['request_id'])
            self.assertEqual(events[3]['request_id'], events[4]['request_id'])
            self.assertNotEqual(events[1]['request_id'], events[3]['request_id'])
            self.assertEqual(events[2]['status'], 429)
            self.assertEqual(events[2]['body'], 'busy body')
            self.assertIn('full source', path.read_text())
            self.assertNotIn('secret_header_value', path.read_text())
            self.assertNotIn('Authorization', path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_json_and_cached_responses_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            path = Path(tmp) / 'trace.jsonl'
            opener.return_value.open.return_value = io.BytesIO(b'not JSON response')
            provider = OpenAIProvider('test', base_url='http://localhost/v1', trace_file=path, cache=Path(tmp) / 'cache')
            with self.assertRaises(ReviewError):
                provider.ask('review', {}, SCHEMA)
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['body'], 'not JSON response')
            opener.return_value.open.return_value = response()
            provider.ask('review', {}, SCHEMA)
            provider.ask('review', {}, SCHEMA)
            event = json.loads(path.read_text().splitlines()[-1])
            self.assertEqual(event['event'], 'cache_hit')
            self.assertIn('messages', event['request'])
            self.assertEqual(event['response'], {'findings': [], 'context_paths': []})
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_append_sessions_and_refuse_unrelated_files_or_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'trace.jsonl'
            TraceLog(path)
            TraceLog(path)
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(events), 2)
            self.assertNotEqual(events[0]['session_id'], events[1]['session_id'])
            source = root / 'app.py'
            source.write_text('safe()')
            with self.assertRaises(ReviewError):
                TraceLog(source)
            self.assertEqual(source.read_text(), 'safe()')
            link = root / 'link'
            link.symlink_to(path)
            with self.assertRaises(ReviewError):
                TraceLog(link)

    def test_trace_excluded_from_discovery_and_collisions_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            path = root / 'trace.jsonl'
            TraceLog(path)
            with patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--dry-run', '--trace-file', str(path)]), 0)
            self.assertEqual(json.loads(out.getvalue())['targets'], ['app.py'])
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--dry-run', '--trace-file', str(root / '.njordcup/memory.json')]), 2)
