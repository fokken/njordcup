import io
import json
import unittest
from unittest.mock import patch

from njordcup.agent import SCHEMA
from njordcup.provider import OpenAIProvider
from njordcup.performance import summarize
from test_narrative import envelope
from test_runtime import http_error


class PerformanceTests(unittest.TestCase):
    def test_requests_failures_usage_and_retry_metrics(self):
        p = OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt')
        response = {**envelope('Analysis'), 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
        with patch('urllib.request.build_opener') as opener, patch.object(p.control, 'wait'):
            opener.return_value.open.side_effect = [http_error(503), io.BytesIO(json.dumps(response).encode())]
            p.ask('', {}, SCHEMA)
        row = summarize(p.performance)['phases']['review']
        self.assertEqual(row['attempts'], 2)
        self.assertEqual(row['requests'], 1)
        self.assertEqual(row['retries'], 1)
        self.assertEqual(row['input_tokens'], 100)
        self.assertEqual(row['output_tokens'], 20)
        self.assertEqual(row['responses_with_usage'], 1)
        self.assertGreater(row['http_seconds'], 0)
        self.assertAlmostEqual(row['average_attempt_seconds'], row['http_seconds'] / 2)
        self.assertAlmostEqual(row['output_tokens_per_second'], 20 / row['elapsed_seconds'])

    def test_missing_usage_is_not_reported_as_zero_throughput(self):
        p = OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt')
        with patch.object(p, '_request', return_value=envelope('Analysis')):
            p.ask('', {}, SCHEMA)
        row = summarize(p.performance)['phases']['review']
        self.assertEqual(row['responses_without_usage'], 1)
        self.assertIsNone(row['output_tokens_per_second'])

    def test_cache_hits_have_no_network_attempts_or_duplicate_tokens(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt', cache=tmp)
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps(envelope('Analysis')).encode())
                p.ask('', {}, SCHEMA)
                p.ask('', {}, SCHEMA)
            row = summarize(p.performance)['phases']['review']
            self.assertEqual(row['requests'], 2)
            self.assertEqual(row['attempts'], 1)
            self.assertEqual(row['cache_hits'], 1)

    def test_phases_are_separate_and_failed_requests_are_accounted(self):
        from njordcup.errors import ReviewError
        from njordcup.performance import snapshot
        p = OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt')
        with patch.object(p, '_request', return_value=envelope('Description')):
            p.ask('', {}, {'properties': {'areas': {}}})
            before = snapshot(p)
            p.ask('', {}, {'properties': {'file_synthesis': {}}})
        with patch.object(p, '_request', side_effect=ReviewError('Malformed reply')):
            with self.assertRaises(ReviewError):
                p.ask('', {}, SCHEMA)
        self.assertEqual(set(p.performance), {'flyover', 'file_consolidation', 'review'})
        self.assertEqual(p.performance['review']['failures'], 1)
        self.assertEqual(p.performance['file_consolidation']['failures'], 0)
        self.assertNotIn('flyover', summarize(p.performance, before)['phases'])
