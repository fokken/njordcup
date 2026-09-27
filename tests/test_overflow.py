import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from njordcup.agent import review, SCHEMA
from njordcup.errors import ContextBudgetExceeded, RunStopped
from njordcup.overflow import is_context_overflow
from njordcup.provider import OpenAIProvider
from njordcup.synthesis import synthesize
from test_narrative import envelope


def overflow(message='Input length exceeds the context length'):
    return urllib.error.HTTPError('http://localhost/v1', 400, 'Bad Request', {},
                                  io.BytesIO(json.dumps({'error': {'message': message}}).encode()))


def response():
    return io.BytesIO(json.dumps(envelope('Analysis complete.')).encode())


def provider(**kwargs):
    return OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt', **kwargs)


class OverflowTests(unittest.TestCase):
    def test_recognition_requires_explicit_context_error(self):
        for body in ({'error': {'code': 'context_length_exceeded'}},
                     {'error': {'type': 'exceed_context_size_error'}},
                     {'error': 'request exceeds the available context size'},
                     {'error': {'message': "This model's maximum context length is 4096 tokens. However, you requested 8000 tokens."}}):
            self.assertTrue(is_context_overflow(400, json.dumps(body).encode()))
        for status, body in [(401, {'error': 'input exceeds context length'}),
                             (400, {'error': 'CUDA out of memory'}), (413, {'error': 'request too large'}),
                             (400, {'error': 'max_tokens must be positive'}),
                             (400, {'error': {'code': [], 'type': {}}})]:
            self.assertFalse(is_context_overflow(status, json.dumps(body).encode()))

    def test_optional_memory_is_shrunk_and_same_target_is_retried(self):
        p = provider(max_retries=0)
        payload = {'target_chunks': ['a@1-1'], 'files': [{'id': 'a@1-1', 'lines': '1: check()'}],
                   'architectural_memory': {'summary': 'detail ' * 1000}}
        requests = []
        def send(request, **kwargs):
            body = json.loads(request.data)
            requests.append(json.loads(body['messages'][1]['content']))
            if len(requests) == 1:
                raise overflow('Input exceeds context length. PRIVATE SOURCE SHOULD NOT BE LOGGED')
            return response()
        with patch('urllib.request.build_opener') as opener, self.assertLogs('njordcup', level='WARNING') as logs:
            opener.return_value.open.side_effect = send
            result = p.ask('', payload, SCHEMA)
        self.assertEqual(str(result), 'Analysis complete.')
        self.assertEqual(p.calls, 2)
        self.assertEqual(p.context_overflows, 1)
        self.assertNotIn('architectural_memory', requests[1])
        self.assertEqual(requests[0]['files'], requests[1]['files'])
        self.assertNotIn('PRIVATE SOURCE', '\n'.join(logs.output))

    def test_review_splits_rejected_targets_without_losing_lines(self):
        p = provider()
        sources = {'app.txt': 'process(input)\n' * 220}
        accepted = []
        def send(request, **kwargs):
            body = json.loads(request.data)
            chars, _ = p.request_size(body)
            if chars > 4000:
                raise overflow()
            payload = json.loads(body['messages'][1]['content'])
            accepted.extend(payload.get('target_chunks', []))
            return response()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = send
            result = review(sources, ['app.txt'], [], p, batch_chars=6000)
        self.assertEqual(result['status'], 'complete')
        self.assertGreater(result['batch_splits'], 0)
        self.assertGreater(p.context_overflows, 0)
        self.assertEqual(result['coverage']['lines_reviewed'], 220)
        self.assertEqual(set(accepted), set(result['chunk_results']))
        self.assertEqual(result['file_analysis'][0]['consolidation_status'], 'complete')

    def test_single_unfit_chunk_stays_incomplete(self):
        p = provider()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: (_ for _ in ()).throw(overflow())
            result = review({'app.txt': 'check()'}, ['app.txt'], [], p)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['coverage']['chunks_reviewed'], 0)
        self.assertEqual(p.calls, 1)

    def test_recovery_respects_call_budget_and_unrelated_errors(self):
        payload = {'architectural_memory': 'large ' * 1000}
        p = provider(max_calls=1)
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: (_ for _ in ()).throw(overflow())
            with self.assertRaises(RunStopped) as stopped:
                p.ask('', payload, SCHEMA)
        self.assertEqual(stopped.exception.reason, 'call_budget')
        self.assertEqual(p.calls, 1)
        p = provider()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = urllib.error.HTTPError('http://localhost/v1', 400, 'Bad', {},
                                                                         io.BytesIO(b'{"error":"invalid model"}'))
            with self.assertRaises(RunStopped) as stopped:
                p.ask('', {}, SCHEMA)
        self.assertEqual(stopped.exception.reason, 'provider_error')
        self.assertEqual(p.context_overflows, 0)
        self.assertEqual(p.calls, 1)

    def test_synthesis_repartitions_after_remote_overflow(self):
        p = provider()
        saved = {}
        def send(request, **kwargs):
            if p.request_size(json.loads(request.data))[0] > 3500:
                raise overflow()
            return response()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = send
            state = synthesize({'summary': 'Saved analysis. ' * 300}, saved, p, lambda: None)
        self.assertEqual(state['status'], 'complete')
        self.assertGreater(p.context_overflows, 0)
        self.assertGreater(len(state['nodes']), 1)
