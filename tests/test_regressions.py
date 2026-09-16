"""Regression cases found during the code and documentation review."""
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from njordcup.agent import review
from njordcup.budget import shrink_payload
from njordcup.cli import main
from njordcup.errors import ReviewError
from njordcup.flyover import flyover, make_payload
from njordcup.index import build_index, CodeIndex
from njordcup.mapping import hierarchical_flyover
from njordcup.repository import discover
from njordcup.sarif import import_scan, load_sarif
from test_review import FINDING
from test_sarif import document, location
from test_scale import AutomaticProvider, make_assessment


class RegressionTests(unittest.TestCase):
    def test_summary_cannot_overwrite_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            with patch('njordcup.cli.OpenAIProvider', return_value=AutomaticProvider()), patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--model', 'test', '--flyover-only']), 0)
            index = root / '.njordcup/memory.index.json'
            before = index.read_bytes()
            with patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--summary', '--output', str(index)]), 2)
            self.assertEqual(index.read_bytes(), before)

    def test_completed_scan_resume_retains_findings_and_exit_code(self):
        class ConfirmProvider(AutomaticProvider):
            def ask(self, instructions, payload, schema):
                self.calls += 1
                return {'findings': [deepcopy(FINDING)], 'context_paths': [], 'assessments': [
                    make_assessment(s, 'eval(user_input)', 'confirmed', 'CWE-95') for s in payload['sarif_candidates']]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('eval(user_input)')
            sarif = root / 'r.sarif'
            sarif.write_text(json.dumps(document([{'ruleId': 'eval', 'locations': [location()]}])))
            args = [tmp, '--model', 'test', '--sarif', str(sarif), '--investigate-all']
            for expected_calls in (2, 0):
                provider = ConfirmProvider()
                with patch('njordcup.cli.OpenAIProvider', return_value=provider), patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(main(args), 1)
                self.assertEqual(len(json.loads(out.getvalue())['findings']), 1)
                self.assertEqual(provider.calls, expected_calls)

    def test_base_with_repository_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                subprocess.run(['git', '-C', tmp, *args], check=True, capture_output=True)
            git('init')
            module = root / 'module'
            module.mkdir()
            (module / 'app.py').write_text('old()')
            (root / 'other.py').write_text('old()')
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'initial')
            (module / 'app.py').write_text('new()')
            (root / 'other.py').write_text('new()')
            _, targets, _ = discover(module, base='HEAD')
            self.assertEqual(targets, ['app.py'])

    def test_trimmed_context_can_be_requested_again_without_double_charging(self):
        sources = {'app.txt': 'check(user)', 'auth.txt': 'authorize(user)'}
        index = build_index(sources, list(sources))
        lookup = CodeIndex(index, sources)
        cost = len(json.dumps(lookup.entry(lookup.by_path['auth.txt'][0])))
        class TrimProvider(AutomaticProvider):
            def ask(self, instructions, payload, schema):
                self.calls += 1
                if self.calls == 2:
                    payload['files'] = [f for f in payload['files'] if f['path'] != 'auth.txt']
                    payload['budget_notes'] = ['Requested source context omitted to fit request budget']
                self.payloads.append(deepcopy(payload))
                return {'findings': [], 'context_paths': ['auth.txt'] if self.calls < 3 else []}
        provider = TrimProvider()
        result = review(sources, ['app.txt'], [], provider, context_chars=cost, repository_index=index)
        self.assertEqual({f['path'] for f in provider.payloads[-1]['files']}, set(sources))
        self.assertNotIn('Unavailable requested context: auth.txt', result['limitations'])

    def test_failed_mapping_returns_incomplete_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a', 'b'):
                (root / name).mkdir()
                (root / name / 'app.txt').write_text('safe()')
            with patch('njordcup.cli.OpenAIProvider', return_value=AutomaticProvider(0)), patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(main([tmp, '--model', 'test', '--flyover-only']), 2)
            result = json.loads(out.getvalue())
            self.assertEqual(result['status'], 'incomplete')
            self.assertTrue(result['errors'])

    def test_flyover_shrinking_keeps_a_selected_target(self):
        payload = make_payload({'package.json': '{}', 'z.txt': 'target'}, ['z.txt'])
        while shrink_payload(payload):
            pass
        self.assertEqual(payload['target_paths'], ['z.txt'])
        self.assertIn('z.txt', payload['inventory'])

    def test_mapping_coverage_does_not_double_count_sarif_areas(self):
        sources = {'app.py': 'safe()'}
        index = build_index(sources, list(sources))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'memory.json'
            memory, _ = hierarchical_flyover(sources, list(sources), AutomaticProvider(), path, index, analyze=False)
            import_scan(memory, {'id': 'scan', 'candidates': [{'id': 'one', 'rule_id': 'rule', 'path': 'app.py', 'line': 1, 'location_status': 'resolved'}]}, index)
            path.write_text(json.dumps(memory))
            updated, _ = hierarchical_flyover(sources, list(sources), AutomaticProvider(), path, index)
            self.assertEqual(updated['coverage']['mapped_target_files'], 1)

    def test_malformed_sarif_is_a_controlled_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'r.sarif'
            for data in ([], {'version': '2.1.0', 'runs': [None]}, document([None]), document([{'message': None}])):
                with self.subTest(data=data):
                    path.write_text(json.dumps(data))
                    with self.assertRaises(ReviewError):
                        load_sarif(path, root, {})

    def test_small_sarif_map_can_finish_architectural_flyover(self):
        sources = {'app.py': 'safe()'}
        index = build_index(sources, list(sources))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'memory.json'
            hierarchical_flyover(sources, list(sources), AutomaticProvider(), path, index, analyze=False)
            provider = AutomaticProvider()
            memory, reused = flyover(sources, list(sources), provider, path, repository_index=index)
            self.assertFalse(reused)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(memory['coverage']['pages_pending'], 0)
