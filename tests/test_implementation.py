from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.implementation import analyze_implementation, load_implementation, review_implementation_context
from njordcup.index import build_index
from test_scale import AutomaticProvider


class ImplementationProvider(AutomaticProvider):
    def ask(self, instructions, payload, schema):
        result = super().ask(instructions, payload, schema)
        if 'implementation_details' in schema['properties']:
            result.update(languages=['Python'], implementation_details=['app.py handles requests and calls shared authorization.'],
                          data_flows=['HTTP input flows through the handler to the shared service.'])
        return result


class ImplementationTests(unittest.TestCase):
    def test_separate_results_resume_and_save_partial_progress(self):
        sources = {'one/app.py': 'safe()', 'two/app.py': 'safe()'}
        index = build_index(sources, list(sources))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'implementation.json'
            partial = analyze_implementation(sources, list(sources), ImplementationProvider(1), path, index)
            self.assertEqual(partial['status'], 'incomplete')
            self.assertEqual(partial['coverage']['pages_complete'], 1)
            provider = ImplementationProvider()
            complete = analyze_implementation(sources, list(sources), provider, path, index)
            self.assertEqual(complete['status'], 'complete')
            self.assertEqual(provider.calls, 1)
            self.assertEqual(complete['languages'], ['Python'])
            self.assertIn('data_flows', next(iter(complete['pages'].values()))['analysis'])

    def test_stale_and_different_scope_results_are_ignored(self):
        sources = {'app.py': 'safe()', 'other.py': 'ok()'}
        index = build_index(sources, list(sources))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'implementation.json'
            analyze_implementation(sources, list(sources), ImplementationProvider(), path, index)
            self.assertIsNotNone(load_implementation(path, sources, list(sources), index))
            changed = {**sources, 'app.py': 'changed()'}
            self.assertIsNone(load_implementation(path, changed, list(changed), build_index(changed, list(changed))))
            self.assertIsNone(load_implementation(path, sources, ['app.py'], index))
            self.assertIsNone(load_implementation(path, sources, list(sources), build_index(sources, list(sources), index_mode='text')))

    def test_security_reuses_analysis_and_still_reviews_all_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            args = [tmp, '--model', 'test']
            with patch('njordcup.cli.OpenAIProvider', return_value=ImplementationProvider()), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([*args, '--implementation-analysis']), 0)
            memory_path = root / '.njordcup/memory.json'
            self.assertFalse(memory_path.exists())
            result_path = root / '.njordcup/memory.implementation.json'
            self.assertTrue(result_path.is_file())
            provider = ImplementationProvider()
            with patch('njordcup.cli.OpenAIProvider', return_value=provider), patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([*args, '--automatic']), 0)
            self.assertEqual(provider.calls, 1)  # Security review only; no flyover call.
            self.assertTrue(json.loads(out.getvalue())['implementation_analysis']['reused'])
            self.assertIn('implementation_analysis', provider.payloads[0]['architectural_memory'])
            self.assertEqual(provider.payloads[0]['target_paths'], ['app.py'])
            before = memory_path.read_bytes()
            with patch('njordcup.cli.OpenAIProvider', return_value=ImplementationProvider()), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([*args, '--implementation-analysis', '--rerun']), 0)
            self.assertEqual(memory_path.read_bytes(), before)

    def test_custom_result_and_collision_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            path = root / 'descriptions.json'
            with patch('njordcup.cli.OpenAIProvider', return_value=ImplementationProvider()), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--model', 'test', '--implementation-analysis', '--implementation-file', str(path)]), 0)
            self.assertTrue(path.is_file())
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--model', 'test', '--implementation-analysis', '--implementation-file', str(root / '.njordcup/memory.json')]), 2)
                self.assertEqual(main([tmp, '--report', '--implementation-file', str(path), '--output', str(path)]), 2)

    def test_context_only_contains_relevant_pages(self):
        sources = {'one/app.py': 'safe()', 'two/app.py': 'ok()'}
        index = build_index(sources, list(sources))
        with tempfile.TemporaryDirectory() as tmp:
            report = analyze_implementation(sources, list(sources), ImplementationProvider(), Path(tmp) / 'analysis.json', index)
            context = review_implementation_context(report, ['one/app.py'])
            self.assertEqual([p['component'] for p in context['pages']], ['one'])
            self.assertLessEqual(len(json.dumps(context)), 12000)

    def test_implementation_html_is_separate_offline_and_escapes_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            with patch('njordcup.cli.OpenAIProvider', return_value=ImplementationProvider()), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--model', 'test', '--implementation-analysis']), 0)
            path = root / '.njordcup/memory.implementation.json'
            result = json.loads(path.read_text())
            page = next(iter(result['pages'].values()))
            page['analysis']['implementation_details'] = ['<script>unsafe()</script>']
            result['pages']['pending:0'] = {'component': 'pending', 'paths': ['pending.txt'], 'status': 'pending'}
            result['status'] = 'incomplete'
            path.write_text(json.dumps(result))
            before = path.read_bytes()
            with patch('njordcup.cli.OpenAIProvider') as provider, patch('njordcup.cli.discover') as discover, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--implementation-report']), 0)
            provider.assert_not_called()
            discover.assert_not_called()
            self.assertFalse((root / '.njordcup/memory.json').exists())
            self.assertEqual(path.read_bytes(), before)
            html_path = root / '.njordcup/memory.implementation.html'
            html = html_path.read_text()
            self.assertIn('Implementation report', html)
            self.assertIn('Data and control flows', html)
            self.assertIn('Implementation description pending', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertNotIn('<script>', html)
            self.assertEqual(html_path.stat().st_mode & 0o777, 0o600)
            with patch('sys.stdout', new_callable=io.StringIO):
                custom = root / 'custom.html'
                self.assertEqual(main([tmp, '--implementation-report', '--output', str(custom)]), 0)
                self.assertTrue(custom.is_file())

    def test_implementation_report_rejects_missing_or_malformed_results(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(main([tmp, '--implementation-report']), 2)
            path = Path(tmp) / 'bad.json'
            path.write_text('[]')
            self.assertEqual(main([tmp, '--implementation-report', '--implementation-file', str(path)]), 2)
