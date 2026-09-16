import json
import unittest

from njordcup.agent import review, SCHEMA
from njordcup.errors import ContextBudgetExceeded
from njordcup.flyover import make_payload, PROMPT, OVERVIEW
from njordcup.index import build_index, CodeIndex
from njordcup.provider import OpenAIProvider


class RecordingProvider(OpenAIProvider):
    def __init__(self, **kwargs):
        super().__init__('test', base_url='http://localhost:8000/v1', **kwargs)
        self.sent = []

    def _request(self, request):
        body = json.loads(request.data)
        self.sent.append(body)
        self.calls += 1
        return {'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps({'findings': [], 'context_paths': []})}}]}


class BudgetTests(unittest.TestCase):
    def test_token_reservation_counts_schema_and_unicode(self):
        provider = RecordingProvider(max_tokens=100, token_margin=50, context_window=2000)
        payload = {'source': '权限' * 200}
        with self.assertRaises(ContextBudgetExceeded):
            provider.ask('review', payload, SCHEMA)
        self.assertEqual(provider.sent, [])
        provider.context_window = 10000
        provider.ask('review', payload, SCHEMA)
        chars, tokens = provider.request_size(provider.sent[0])
        self.assertGreater(tokens, len(payload['source']))
        self.assertLessEqual(tokens + 150, provider.context_window)
        self.assertEqual(chars, provider.max_request_chars)

    def test_invalid_window_and_estimator(self):
        for options in ({'context_window': 6000}, {'bytes_per_token': 0},
                        {'bytes_per_token': float('nan')}, {'token_margin': -1}):
            with self.assertRaises(ValueError):
                RecordingProvider(**options)

    def test_batch_splitting_preserves_all_target_lines_and_resumes(self):
        sources = {f'{i}.txt': ('operation(data)\n' * 120) for i in range(4)}
        provider = RecordingProvider(max_input_chars=7000)
        saved = []
        report = review(sources, list(sources), [], provider, checkpoint=lambda r: saved.append(json.loads(json.dumps(r))))
        self.assertEqual(report['status'], 'complete', report['errors'])
        self.assertEqual(report['coverage']['lines_reviewed'], 480)
        self.assertGreater(len(provider.sent), 1)
        self.assertTrue(all(provider.request_size(b)[0] <= 7000 for b in provider.sent))
        before = len(provider.sent)
        resumed = review(sources, list(sources), [], provider, previous=saved[-1])
        self.assertEqual(resumed['status'], 'complete')
        self.assertEqual(len(provider.sent), before)

    def test_optional_source_removal_is_visible_and_targets_untouched(self):
        provider = RecordingProvider(max_input_chars=4000)
        payload = {'target_chunks': ['a'], 'files': [
            {'id': 'a', 'lines': '1: safe()'}, {'id': 'b', 'lines': 'x' * 8000}]}
        provider.ask('review', payload, SCHEMA)
        self.assertEqual([e['id'] for e in payload['files']], ['a'])
        self.assertIn('Requested source context omitted to fit request budget', payload['budget_notes'])

    def test_unsplittable_chunk_stays_unreviewed(self):
        provider = RecordingProvider(max_input_chars=4000)
        report = review({'big.txt': 'x' * 5000, 'small.txt': 'ok()'}, ['big.txt', 'small.txt'], [], provider)
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('big.txt', report['unreviewed'])
        self.assertIn('small.txt', report['reviewed'])

    def test_omitted_requested_context_prevents_complete_review(self):
        class ContextProvider(RecordingProvider):
            def _request(self, request):
                response = super()._request(request)
                if self.calls == 1:
                    response['choices'][0]['message']['content'] = json.dumps({
                        'findings': [], 'context_paths': ['auth.txt']})
                return response
        provider = ContextProvider(max_input_chars=5000)
        report = review({'app.txt': 'check(user)', 'auth.txt': 'x' * 8000}, ['app.txt'], [], provider)
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('Requested source context omitted to fit request budget', report['limitations'])
        last = json.loads(provider.sent[-1]['messages'][1]['content'])
        self.assertEqual([e['path'] for e in last['files']], ['app.txt'])

    def test_flyover_reduction_updates_coverage(self):
        provider = RecordingProvider(max_input_chars=5000)
        payload = make_payload({f'{i}.txt': 'x' * 5000 for i in range(20)}, ['0.txt'])
        provider.fit_payload(PROMPT, payload, OVERVIEW)
        self.assertTrue(provider.fits(PROMPT, payload, OVERVIEW))
        self.assertEqual(payload['coverage']['omitted_files'], 20 - len(payload['samples']))
        self.assertTrue(payload['budget_notes'])


class RetrievalTests(unittest.TestCase):
    def test_fragments_paths_exact_strings_and_unread_results(self):
        sources = {'handlers/check.rb': 'validateAccessToken(user)\n',
                   'shared/access.ex': 'validate_access_token(user)\n',
                   'messages.txt': 'access denied: tenant mismatch\n',
                   'other.txt': 'access allowed\n'}
        index = build_index(sources, list(sources), index_mode='text')
        lookup = CodeIndex(index, sources)
        paths = lambda ids: [lookup.chunks[c]['path'] for c in ids]
        self.assertEqual(set(paths(lookup.search('validate token'))), {'handlers/check.rb', 'shared/access.ex'})
        self.assertEqual(paths(lookup.search('shared/access')), ['shared/access.ex', 'messages.txt', 'other.txt', 'handlers/check.rb'])
        self.assertEqual(paths(lookup.search('"access denied: tenant mismatch"')), ['messages.txt'])
        first = lookup.search('access', limit=1)
        self.assertNotIn(first[0], lookup.search('access', exclude=first))
        self.assertEqual(lookup.search('"not present"'), [])
        self.assertEqual(lookup.search(''), [])
