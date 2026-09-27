import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.flyover import fingerprint
from njordcup.memory import summarize
from test_narrative import envelope


class QuickAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = {'README.md': 'Project docs', 'auth.py': 'authorize(user)', 'upload.py': 'store(input)'}
        for name, source in self.sources.items():
            (self.root / name).write_text(source)
        (self.root / '.njordcup').mkdir()
        self.rundown = self.root / '.njordcup/memory.quick-implementation.json'
        self.save_rundown()
        self.memory = self.root / '.njordcup/quick-audit.json'

    def save_rundown(self):
        self.rundown.write_text(json.dumps({'version': 1, 'kind': 'quick_implementation_analysis',
            'status': 'complete', 'fingerprint': fingerprint(self.sources, list(self.sources)),
            'text': 'ARCHITECTURE_MARKER: web service with authentication and uploads.',
            'selected_files': ['README.md']}))

    def run_audit(self, *extra, invalid=False, structured=False):
        calls = []
        def send(request, **kwargs):
            body = json.loads(request.data)
            data = json.loads(body['messages'][1]['content'])
            calls.append(data)
            if 'inventory' in data:
                self.assertIn('ARCHITECTURE_MARKER', data['codebase_rundown']['text'])
                self.assertNotIn('response_format', body)
                text = 'FILE 999' if invalid else '\n'.join(
                    f'FILE {entry["id"]} - sensitive entry point <script>' for entry in
                    [e for e in data['inventory'] if e['path'] != 'README.md'][:data['selection_limit']])
            elif structured:
                self.assertIn('response_format', body)
                text = json.dumps({'findings': [], 'context_paths': []})
            else:
                text = 'Security review completed.'
            return io.BytesIO(json.dumps(envelope(text)).encode())
        with patch('urllib.request.build_opener') as opener, patch('sys.stdout', new_callable=io.StringIO) as output:
            opener.return_value.open.side_effect = send
            code = main([str(self.root), '--quick-audit', '--model', 'local', '--base-url', 'http://localhost/v1',
                         '--output-mode', 'json_schema' if structured else 'prompt', *extra])
        return code, json.loads(output.getvalue()) if output.getvalue().strip() else None, calls

    def test_selection_uses_rundown_but_can_choose_other_files_and_keeps_scope(self):
        self.sources['auth.py'] = 'authorize(user)\n' * 180
        (self.root / 'auth.py').write_text(self.sources['auth.py'])
        self.save_rundown()
        normal_memory = self.root / '.njordcup/memory.json'
        normal_memory.write_text('{"untouched":true}')
        code, result, calls = self.run_audit('--quick-max-files', '2', '--workers', '2', '--batch-chars', '1600')
        self.assertEqual(code, 0)
        self.assertEqual(result['quick_audit']['selected_files'], ['auth.py', 'upload.py'])
        self.assertEqual(result['quick_audit']['not_selected_files'], 1)
        self.assertEqual(normal_memory.read_text(), '{"untouched":true}')
        self.assertFalse(any('samples' in p for p in calls))
        targets = {path for p in calls for path in p.get('target_paths', [])}
        self.assertEqual(targets, {'auth.py', 'upload.py'})
        saved = json.loads(self.memory.read_text())
        summary = summarize(saved)
        self.assertEqual(summary['audit_status'], 'complete')
        self.assertEqual(sum(c['lines_reviewed'] for c in summary['area_coverage'].values()), 181)
        self.assertEqual(summary['quick_audit']['available_files'], 3)
        with patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main([str(self.root), '--report', '--memory', str(self.memory)]), 0)
        html = self.memory.with_name('quick-audit.report.html').read_text()
        self.assertIn('prioritized subset', html)
        self.assertIn('1 files outside this audit scope', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)

    def test_shared_budget_resume_and_unchanged_selection(self):
        code, first, calls = self.run_audit('--quick-max-files', '2', '--max-calls', '1')
        self.assertEqual(code, 2)
        self.assertEqual(first['stop_reason'], 'call_budget')
        self.assertEqual(len(calls), 1)
        code, resumed, calls = self.run_audit('--quick-max-files', '2', '--workers', '2')
        self.assertEqual(code, 0)
        self.assertFalse(any('inventory' in p for p in calls))
        self.assertEqual(len(calls), 2)
        code, _, calls = self.run_audit('--quick-max-files', '2')
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])

    def test_limit_changes_reselect_and_archive_previous_scope(self):
        self.run_audit('--quick-max-files', '1')
        code, result, calls = self.run_audit('--quick-max-files', '2')
        self.assertEqual(code, 0)
        self.assertTrue(any('inventory' in p for p in calls))
        self.assertEqual(len(result['file_analysis']), 2)
        self.assertTrue(any(a.get('reviews') for a in json.loads(self.memory.read_text())['archives']))

    def test_invalid_choices_do_not_start_security_review(self):
        code, result, calls = self.run_audit(invalid=True)
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result['quick_audit']['selection_complete'])
        self.assertEqual(json.loads(self.memory.read_text())['reviews'], [])
        code, _, calls = self.run_audit('--quick-max-files', '50')
        self.assertEqual(code, 0)
        self.assertEqual(calls[0]['selection_attempt'], 2)

    def test_missing_stale_rundown_and_existing_full_memory_are_rejected(self):
        with patch('sys.stderr', new_callable=io.StringIO):
            code, _, calls = self.run_audit('--quick-implementation-file', str(self.root / 'missing.json'))
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])
            (self.root / 'auth.py').write_text('changed()')
            code, _, calls = self.run_audit()
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])
            (self.root / 'auth.py').write_text(self.sources['auth.py'])
            normal = self.root / '.njordcup/memory.json'
            normal.write_text('{"reviews":[]}')
            code, _, calls = self.run_audit('--memory', str(normal))
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])
            self.assertEqual(normal.read_text(), '{"reviews":[]}')
            code, _, calls = self.run_audit('--output', str(normal))
            self.assertEqual(code, 2)
            self.assertEqual(calls, [])
            self.assertEqual(normal.read_text(), '{"reviews":[]}')

    def test_structured_audit_preserves_selector_prose_and_shared_usage(self):
        code, result, calls = self.run_audit('--quick-max-files', '1', structured=True)
        self.assertEqual(code, 0)
        self.assertEqual(result['usage']['calls'], len(calls))
        self.assertEqual(result['performance']['phases']['audit_selection']['attempts'], 1)
        self.assertGreater(result['performance']['phases']['review']['attempts'], 0)

    def test_report_cannot_overwrite_rundown(self):
        self.run_audit('--quick-max-files', '1')
        before = self.rundown.read_bytes()
        with patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(main([str(self.root), '--report', '--memory', str(self.memory),
                                   '--output', str(self.rundown)]), 2)
        self.assertEqual(self.rundown.read_bytes(), before)
