import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.memory import summarize
from test_narrative import envelope


class DirectAuditTests(unittest.TestCase):
    def run_audit(self, root, *extra):
        requests = []
        def send(request, **kwargs):
            payload = json.loads(json.loads(request.data)['messages'][1]['content'])
            requests.append(payload)
            return io.BytesIO(json.dumps(envelope('Security analysis saved')).encode())
        with patch('urllib.request.build_opener') as opener, patch('sys.stdout', new_callable=io.StringIO) as output:
            opener.return_value.open.side_effect = send
            code = main([str(root), '--model', 'local', '--base-url', 'http://localhost/v1',
                         '--output-mode', 'prompt', *extra])
        return code, json.loads(output.getvalue()), requests

    def test_direct_audit_skips_flyover_and_resumes_without_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.txt', 'b.txt'):
                (root / name).write_text('safe()')
            code, result, requests = self.run_audit(root, '--audit-only', '--workers', '2')
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(len(requests), 2)
            self.assertTrue(all('target_chunks' in r for r in requests))
            memory = json.loads((root / '.njordcup/memory.json').read_text())
            self.assertGreater(memory['coverage']['pages_pending'], 0)
            self.assertEqual(summarize(memory)['audit_status'], 'complete')
            self.assertEqual(summarize(memory)['audit_mode'], 'direct')
            code, result, requests = self.run_audit(root, '--audit-only')
            self.assertEqual(code, 0)
            self.assertEqual(requests, [])
            self.assertEqual(len(result['file_analysis']), 2)
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--report']), 0)
            self.assertIn('Security analysis saved', (root / '.njordcup/memory.report.html').read_text())

    def test_later_automatic_mode_adds_mapping_without_repeating_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.txt').write_text('safe()')
            self.run_audit(root, '--audit-only')
            code, result, requests = self.run_audit(root, '--automatic')
            self.assertEqual(code, 0)
            self.assertEqual(len(requests), 1)
            self.assertIn('samples', requests[0])
            self.assertEqual(result['audit_mode'], 'mapped')
            self.assertEqual(len(result['file_analysis']), 1)

    def test_scope_and_call_limit_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.txt', 'b.txt', 'ignored.py'):
                (root / name).write_text('safe()')
            options = ('--audit-only', '--include', '*.txt', '--max-calls', '1')
            code, result, requests = self.run_audit(root, *options)
            self.assertEqual(code, 2)
            self.assertEqual(len(requests), 1)
            self.assertEqual(result['stop_reason'], 'call_budget')
            code, result, requests = self.run_audit(root, *options)
            self.assertEqual(code, 0)
            self.assertEqual(len(requests), 1)
            self.assertEqual({f['path'] for f in result['file_analysis']}, {'a.txt', 'b.txt'})

    def test_conflicting_actions_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for action in ('--flyover-only', '--implementation-analysis', '--report', '--investigate-all'):
                with self.subTest(action=action), patch('sys.stderr', new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as stopped:
                        main([tmp, '--model', 'local', '--audit-only', action])
                    self.assertEqual(stopped.exception.code, 2)
