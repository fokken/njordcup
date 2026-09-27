"""Concurrent transport, coordinated persistence and shared budget regressions."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

from njordcup.agent import review
from njordcup.cli import main
from njordcup.provider import OpenAIProvider
from test_narrative import envelope
from test_overflow import overflow


def provider(**kwargs):
    return OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt', **kwargs)


def response(text='Title: Saved analysis'):
    value = envelope(text)
    value['usage'] = {'prompt_tokens': 10, 'completion_tokens': 5}
    return io.BytesIO(json.dumps(value).encode())


class WorkerTests(unittest.TestCase):
    def test_overlapping_requests_single_writer_consolidation_and_trace(self):
        sources = {name + '.txt': 'process(input)\n' * 120 for name in ('a', 'b', 'c')}
        barrier, lock = threading.Barrier(2), threading.Lock()
        sent = 0
        callback_threads = set()
        snapshots = []
        def checkpoint(report):
            callback_threads.add(threading.get_ident())
            snapshots.append(deepcopy(report))
        def send(request, **kwargs):
            nonlocal sent
            with lock:
                sent += 1
                ordinal = sent
            if ordinal <= 2:
                barrier.wait(timeout=5)
            payload = json.loads(json.loads(request.data)['messages'][1]['content'])
            return response('Combined analysis' if 'saved_analysis_fragments' in payload else 'Original analysis')
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / 'trace.jsonl'
            p = provider(trace_file=trace)
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = send
                result = review(sources, list(sources), [], p, batch_chars=1200, workers=2, checkpoint=checkpoint)
            records = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(callback_threads, {threading.get_ident()})
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['coverage']['lines_reviewed'], 360)
        self.assertEqual(result['coverage']['chunks_reviewed'], result['coverage']['chunks_total'])
        self.assertTrue(all(f['consolidation_status'] == 'complete' for f in result['file_analysis']))
        self.assertTrue(all(f['text'] == 'Combined analysis' for f in result['file_analysis']))
        self.assertTrue(any(r['coverage']['chunks_reviewed'] < result['coverage']['chunks_total'] for r in snapshots))
        self.assertEqual(p.calls, sent)
        self.assertEqual(p.input_tokens, 10 * sent)
        self.assertEqual(p.output_tokens, 5 * sent)
        self.assertEqual(sum(r['attempts'] for r in p.performance.values()), sent)
        requests = [r for r in records if r['event'] == 'request']
        responses = [r for r in records if r['event'] == 'response']
        self.assertEqual(sorted(r['call'] for r in requests), list(range(1, sent + 1)))
        self.assertEqual({r['request_id'] for r in requests}, {r['request_id'] for r in responses})

    def test_shared_call_limit_and_resume_with_one_worker(self):
        sources = {f'{i}.txt': 'safe()' for i in range(4)}
        barrier = threading.Barrier(2)
        p = provider(max_calls=2)
        def send(*args, **kwargs):
            barrier.wait(timeout=5)
            return response()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = send
            first = review(sources, list(sources), [], p, workers=2)
        self.assertEqual(p.calls, 2)
        self.assertEqual(first['stop_reason'], 'call_budget')
        self.assertEqual(first['coverage']['chunks_reviewed'], 2)
        self.assertEqual(first['status'], 'incomplete')
        p = provider()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: response()
            resumed = review(sources, list(sources), [], p, workers=1, previous=first)
        self.assertEqual(resumed['status'], 'complete')
        self.assertEqual(p.calls, 2)
        self.assertEqual(resumed['reuse']['chunks'], 2)

    def test_retries_share_the_same_limit(self):
        p = provider(max_calls=3, max_retries=5)
        barrier, lock = threading.Barrier(2), threading.Lock()
        sent = 0
        def send(*args, **kwargs):
            nonlocal sent
            with lock:
                sent += 1
                ordinal = sent
            if ordinal <= 2:
                barrier.wait(timeout=5)
            raise urllib.error.HTTPError('http://localhost', 503, 'Unavailable', {}, io.BytesIO(b'{}'))
        with patch('urllib.request.build_opener') as opener, patch.object(p.control, 'wait'):
            opener.return_value.open.side_effect = send
            result = review({'a.txt': 'a()', 'b.txt': 'b()'}, ['a.txt', 'b.txt'], [], p, workers=2)
        self.assertEqual(sent, 3)
        self.assertEqual(p.calls, 3)
        self.assertEqual(p.retries, 1)
        self.assertEqual(result['stop_reason'], 'call_budget')
        self.assertEqual(result['coverage']['chunks_reviewed'], 0)

    def test_failed_consolidation_resumes_without_repeating_source(self):
        sources = {name + '.txt': 'process(input)\n' * 120 for name in ('a', 'b')}
        barrier = threading.Barrier(2)
        def send(request, **kwargs):
            payload = json.loads(json.loads(request.data)['messages'][1]['content'])
            if 'saved_analysis_fragments' in payload:
                barrier.wait(timeout=5)
                raise urllib.error.HTTPError('http://localhost', 400, 'Bad', {}, io.BytesIO(b'{}'))
            return response()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = send
            first = review(sources, list(sources), [], provider(), workers=2, batch_chars=1200)
        self.assertEqual(first['stop_reason'], 'provider_error')
        self.assertEqual(first['coverage']['chunks_reviewed'], first['coverage']['chunks_total'])
        self.assertEqual(first['status'], 'incomplete')
        def finish(request, **kwargs):
            payload = json.loads(json.loads(request.data)['messages'][1]['content'])
            self.assertIn('saved_analysis_fragments', payload)
            return response('Combined')
        p = provider()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = finish
            resumed = review(sources, list(sources), [], p, workers=2, batch_chars=1200, previous=first)
        self.assertEqual(resumed['status'], 'complete')
        self.assertEqual(p.calls, 2)

    def test_deadline_prevents_scheduling_and_preserves_pending_scope(self):
        p = provider()
        p.control.deadline = 0
        with patch('urllib.request.build_opener') as opener:
            result = review({'a.txt': 'a()', 'b.txt': 'b()'}, ['a.txt', 'b.txt'], [], p, workers=2)
            opener.assert_not_called()
        self.assertEqual(result['stop_reason'], 'deadline')
        self.assertEqual(result['coverage']['chunks_reviewed'], 0)
        self.assertEqual(result['unreviewed'], ['a.txt', 'b.txt'])

    def test_cancellation_retains_completed_checkpoints(self):
        sources = {f'{i}.txt': 'safe()' for i in range(5)}
        p = provider()
        saved = []
        def checkpoint(report):
            saved.append(deepcopy(report))
            if report['coverage']['chunks_reviewed']:
                p.control.cancel()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: response()
            result = review(sources, list(sources), [], p, workers=2, checkpoint=checkpoint)
        self.assertEqual(result['stop_reason'], 'cancelled')
        self.assertGreaterEqual(saved[-1]['coverage']['chunks_reviewed'], 1)
        self.assertLessEqual(p.calls, 2)
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: response()
            resumed = review(sources, list(sources), [], provider(), workers=2, previous=saved[-1])
        self.assertEqual(resumed['status'], 'complete')

    def test_failed_checkpoint_stops_and_joins_workers(self):
        def checkpoint(report):
            if report['coverage']['chunks_reviewed']:
                raise OSError('Cannot save checkpoint')
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: response()
            with self.assertRaisesRegex(OSError, 'Cannot save checkpoint'):
                review({'a.txt': 'a()', 'b.txt': 'b()'}, ['a.txt', 'b.txt'], [], provider(),
                       workers=2, checkpoint=checkpoint)
        self.assertFalse(any(t.name.startswith('njordcup-file') for t in threading.enumerate()))

    def test_context_recovery_keeps_all_source_coverage(self):
        sources = {name + '.txt': 'process(input)\n' * 220 for name in ('a', 'b')}
        p = provider()
        def send(request, **kwargs):
            if p.request_size(json.loads(request.data))[0] > 4000:
                raise overflow()
            return response()
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = send
            result = review(sources, list(sources), [], p, batch_chars=6000, workers=2)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['coverage']['lines_reviewed'], 440)
        self.assertGreater(p.context_overflows, 0)
        self.assertIsNotNone(p.server_input_chars)
        self.assertGreater(result['batch_splits'], 0)

    def test_structured_reviews_and_completed_cache_reuse(self):
        sources = {'a.txt': 'first()', 'b.txt': 'second()'}
        def send(*args, **kwargs):
            return response(json.dumps({'findings': [], 'context_paths': []}))
        with tempfile.TemporaryDirectory() as tmp:
            p = OpenAIProvider('local', base_url='http://localhost/v1', cache=tmp)
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = send
                result = review(sources, list(sources), [], p, workers=2)
            self.assertEqual(result['status'], 'complete')
            p = OpenAIProvider('local', base_url='http://localhost/v1', cache=tmp)
            with patch('urllib.request.build_opener') as opener:
                cached = review(sources, list(sources), [], p, workers=2)
                opener.assert_not_called()
            self.assertEqual(cached['status'], 'complete')
            self.assertEqual(p.calls, 0)
            self.assertGreater(p.cache_hits, 0)
            self.assertEqual(p.input_tokens, 0)

    def test_cli_automatic_workers_and_offline_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.txt', 'b.txt'):
                (root / name).write_text('safe()')
            with patch('urllib.request.build_opener') as opener, patch('sys.stdout', new_callable=io.StringIO) as out:
                opener.return_value.open.side_effect = lambda *a, **k: response()
                self.assertEqual(main([tmp, '--automatic', '--workers', '2', '--model', 'local',
                                       '--base-url', 'http://localhost/v1', '--output-mode', 'prompt']), 0)
            result = json.loads(out.getvalue())
            self.assertEqual(len(result['file_analysis']), 2)
            memory = json.loads((root / '.njordcup/memory.json').read_text())
            self.assertEqual(memory['reviews'][-1]['report']['workers'], 2)
            with patch('urllib.request.build_opener') as opener, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--report']), 0)
                opener.assert_not_called()
            self.assertIn('Saved analysis', (root / '.njordcup/memory.report.html').read_text())
