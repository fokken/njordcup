import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.implementation import load_implementation
from njordcup.index import build_index
from njordcup.quick_implementation import analyze, render_html
from test_narrative import envelope, provider
from test_overflow import overflow


def response(text):
    return io.BytesIO(json.dumps(envelope(text)).encode())


class QuickImplementationTests(unittest.TestCase):
    def responder(self, recorded, select=None):
        def send(request, **kwargs):
            body = json.loads(request.data)
            self.assertNotIn('response_format', body)
            payload = json.loads(body['messages'][1]['content'])
            recorded.append(payload)
            if 'inventory' in payload:
                chosen = (select(payload) if select else payload['inventory'][-payload['selection_limit']:])
                return response('\n'.join('FILE ' + str(entry['id']) for entry in chosen))
            if 'source_samples' in payload:
                return response('Observations: ' + ', '.join(s['path'] for s in payload['source_samples']))
            self.assertIn('saved_analysis_fragments', payload)
            self.assertIn('EVERY selected file', body['messages'][0]['content'])
            return response('Collective rundown <script>unsafe()</script>')
        return send

    def test_model_selection_global_cap_and_collective_input(self):
        sources = {f'file{i:03d}.py': 'def run(): pass' for i in range(60)}
        calls = []
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = self.responder(calls)
            result = analyze(sources, list(sources), provider(), Path(tmp) / 'quick.json')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['selected_files'], sorted(sources)[-50:])
        inspected = {s['path'] for p in calls for s in p.get('source_samples', [])}
        self.assertEqual(inspected, set(result['selected_files']))
        self.assertEqual(result['coverage']['files_described'], 50)
        final = json.loads(calls[-1]['saved_analysis_fragments'][0])
        self.assertEqual(final['selected_files'], result['selected_files'])
        self.assertEqual({p for n in final['observations'] for p in n['paths']}, inspected)
        html = render_html(result)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertIn('file059.py', html)

    def test_large_inventory_is_paged_and_narrowed_before_reading_source(self):
        sources = {f'module{i:03d}/handler.py': 'handle()' for i in range(240)}
        calls = []
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = self.responder(calls)
            result = analyze(sources, list(sources), provider(max_input_chars=3200), Path(tmp) / 'quick.json', max_files=5)
        self.assertEqual(result['status'], 'complete')
        self.assertGreater(sum('inventory' in p for p in calls), 1)
        shown = {entry['path'] for p in calls for entry in p.get('inventory', [])}
        self.assertEqual(shown, set(sources))
        self.assertLessEqual(result['coverage']['files_inspected'], 5)
        self.assertEqual(result['coverage']['inventory_files_shown'], 240)

    def test_invalid_selection_is_saved_and_does_not_read_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'quick.json'
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = lambda *a, **k: response('FILE 999\n../../private')
                first = analyze({'app.py': 'run()'}, ['app.py'], provider(), path)
            self.assertEqual(first['status'], 'incomplete')
            self.assertEqual(first['coverage']['files_inspected'], 0)
            self.assertIn('FILE 999', next(iter(first['selection_nodes'].values()))['text'])
            calls = []
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = self.responder(calls)
                resumed = analyze({'app.py': 'run()'}, ['app.py'], provider(), path)
            self.assertEqual(resumed['status'], 'complete')
            self.assertEqual(calls[0]['selection_attempt'], 2)

    def test_budget_resume_only_finishes_collective_summary(self):
        sources = {'app.py': 'run()', 'README.md': 'Example application'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'quick.json'
            calls = []
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = self.responder(calls)
                first = analyze(sources, list(sources), provider(max_calls=2), path)
            self.assertEqual(first['stop_reason'], 'call_budget')
            self.assertEqual(first['coverage']['files_described'], 2)
            calls = []
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = self.responder(calls)
                resumed = analyze(sources, list(sources), provider(), path)
            self.assertEqual(resumed['status'], 'complete')
            self.assertEqual(len(calls), 1)
            self.assertIn('saved_analysis_fragments', calls[0])
            with patch('urllib.request.build_opener') as opener:
                unchanged = analyze(sources, list(sources), provider(), path)
                opener.assert_not_called()
            self.assertEqual(unchanged['status'], 'complete')

    def test_source_prefixes_and_overflow_keep_selection_fixed(self):
        sources = {'a.py': 'call()\n' * 2000, 'b.py': 'other()\n' * 2000}
        p = provider()
        calls = []
        normal = self.responder(calls)
        def send(request, **kwargs):
            body = json.loads(request.data)
            if p.request_size(body)[0] > 5000:
                raise overflow()
            return normal(request, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = send
            result = analyze(sources, list(sources), p, Path(tmp) / 'quick.json')
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['coverage']['files_described'], 2)
        self.assertGreater(p.context_overflows, 0)
        self.assertTrue(all(s['truncated'] for n in result['inspection_nodes'].values() for s in n['samples']))

    def test_cli_standalone_artifacts_report_and_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('run()')
            artifacts = root / '.njordcup'
            artifacts.mkdir()
            originals = [artifacts / name for name in ('memory.json', 'memory.index.json', 'memory.implementation.json')]
            for path in originals:
                path.write_text('{"untouched":true}')
            quick = root / 'rundown.json'
            args = [tmp, '--quick-implementation-analysis', '--quick-implementation-file', str(quick),
                    '--model', 'local', '--base-url', 'http://localhost/v1', '--output-mode', 'json_schema']
            calls = []
            with patch('urllib.request.build_opener') as opener, patch('sys.stdout', new_callable=io.StringIO):
                opener.return_value.open.side_effect = self.responder(calls)
                self.assertEqual(main(args), 0)
            for path in originals:
                self.assertEqual(path.read_text(), '{"untouched":true}')
            self.assertTrue(quick.with_suffix('.html').is_file())
            index = build_index({'app.py': 'run()'}, ['app.py'])
            self.assertIsNone(load_implementation(quick, {'app.py': 'run()'}, ['app.py'], index))
            with patch('urllib.request.build_opener') as opener, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(args), 0)
                opener.assert_not_called()
                self.assertEqual(main([tmp, '--quick-implementation-report', '--quick-implementation-file', str(quick)]), 0)
                opener.assert_not_called()

    def test_artifact_collisions_and_action_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--quick-implementation-analysis', '--model', 'local',
                                       '--quick-implementation-file', str(root / '.njordcup/memory.json')]), 2)
                with self.assertRaises(SystemExit):
                    main([tmp, '--quick-implementation-analysis', '--automatic', '--model', 'local'])

    def test_changed_source_invalidates_previous_rundown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'quick.json'
            calls = []
            with patch('urllib.request.build_opener') as opener:
                opener.return_value.open.side_effect = self.responder(calls)
                analyze({'app.py': 'old()'}, ['app.py'], provider(), path)
                calls.clear()
                updated = analyze({'app.py': 'new()'}, ['app.py'], provider(), path)
            self.assertEqual(updated['status'], 'complete')
            self.assertTrue(any('inventory' in p for p in calls))
            self.assertEqual(next(p for p in calls if 'source_samples' in p)['source_samples'][0]['source'], 'new()')
