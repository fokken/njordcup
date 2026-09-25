import json
import unittest
from unittest.mock import patch

from njordcup.agent import SCHEMA
from njordcup.errors import ReviewError
from njordcup.provider import OpenAIProvider, validate
from test_review import FINDING


class DiagnosticsTests(unittest.TestCase):
    def test_schema_errors_identify_location_without_returned_values(self):
        with self.assertRaisesRegex(ReviewError, r'\$\.findings\[0\]\.line: expected integer') as error:
            validate({'findings': [{**FINDING, 'line': 'private_marker'}], 'context_paths': []}, SCHEMA)
        self.assertNotIn('private_marker', str(error.exception))
        with self.assertRaisesRegex(ReviewError, 'missing: context_paths; unexpected field count: 1') as error:
            validate({'findings': [], 'private_key_marker': 'private_value'}, SCHEMA)
        self.assertNotIn('private_key_marker', str(error.exception))

    def test_completion_failures_are_distinguishable_without_raw_response(self):
        cases = [('length', '{}', 'truncated'), ('content_filter', '{}', 'filtered'),
                 ('stop', '```json\nprivate_marker\n```', 'valid JSON at line'),
                 ('stop', ['private_marker'], 'not JSON text'),
                 (None, 'private_marker', 'unsupported or missing finish_reason')]
        provider = OpenAIProvider('local', base_url='http://localhost:8000/v1')
        for finish, content, expected in cases:
            with self.subTest(finish=finish, expected=expected):
                result = {'choices': [{'finish_reason': finish, 'message': {'content': content}}]}
                with patch.object(provider, '_request', return_value=result), self.assertRaisesRegex(ReviewError, expected) as error:
                    provider.ask('review', {}, SCHEMA)
                self.assertNotIn('private_marker', str(error.exception))
