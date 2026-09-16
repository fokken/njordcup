from copy import deepcopy
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from test_scale import AutomaticProvider
from test_review import FINDING


class AutomaticReportingTests(unittest.TestCase):
    def test_automatic_covers_all_components_and_resumes_without_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('one', 'two'):
                (root / name).mkdir()
                (root / name / 'app.txt').write_text('safe()')
            args = [tmp, '--model', 'test', '--automatic', '--max-calls', '3']
            for provider, expected_exit in ((AutomaticProvider(3), 2), (AutomaticProvider(), 0), (AutomaticProvider(), 0)):
                with patch('njordcup.cli.OpenAIProvider', return_value=provider), patch('builtins.input') as prompt, patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(main(args), expected_exit)
                    prompt.assert_not_called()
                result = json.loads(out.getvalue())
                self.assertEqual(len(result['area_progress']), 2)
                if expected_exit == 0:
                    self.assertTrue(all(a['status'] == 'complete' for a in result['area_progress']))
            self.assertEqual(provider.calls, 0)

    def test_notifications_follow_persistence_and_are_deduplicated(self):
        class FindingProvider(AutomaticProvider):
            def ask(self, instructions, payload, schema):
                if 'areas' in schema['properties']:
                    return super().ask(instructions, payload, schema)
                self.calls += 1
                return {'findings': [deepcopy(FINDING), deepcopy(FINDING)], 'context_paths': []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('eval(user_input)')
            class SavedStream(io.StringIO):
                def write(stream, value):
                    if value.startswith('Potential issue saved:'):
                        memory = json.loads((root / '.njordcup/memory.json').read_text())
                        self.assertTrue(memory['reviews'][-1]['report']['findings'])
                    return super().write(value)
            stderr = SavedStream()
            with patch('njordcup.cli.OpenAIProvider', return_value=FindingProvider()), patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', stderr):
                self.assertEqual(main([tmp, '--model', 'test', '--automatic']), 1)
            self.assertEqual(stderr.getvalue().count('Potential issue saved:'), 1)

    def test_report_is_offline_escaped_and_does_not_modify_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            with patch('njordcup.cli.OpenAIProvider', return_value=AutomaticProvider()), patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--model', 'test', '--automatic']), 0)
            memory_path = root / '.njordcup/memory.json'
            memory = json.loads(memory_path.read_text())
            injection = '</pre><script>alert(1)</script><img src=x onerror=alert(2)>'
            memory['reviews'][-1]['report']['findings'] = [{'id': 'one', **FINDING, 'title': injection, 'evidence': injection}]
            memory_path.write_text(json.dumps(memory))
            before = memory_path.read_bytes()
            with patch('njordcup.cli.OpenAIProvider') as provider, patch('njordcup.cli.discover') as discover, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--report']), 0)
            provider.assert_not_called()
            discover.assert_not_called()
            self.assertEqual(memory_path.read_bytes(), before)
            html = (root / '.njordcup/memory.report.html').read_text()
            self.assertIn('&lt;script&gt;', html)
            self.assertNotIn(injection, html)
            self.assertIn('Remediation', html)
            self.assertIn('Review coverage', html)
            class Tags(HTMLParser):
                def handle_starttag(parser, tag, attrs):
                    self.assertNotIn(tag, ('script', 'img', 'iframe'))
                    self.assertFalse(any(key.startswith('on') for key, value in attrs))
            Tags().feed(html)

    def test_report_cannot_overwrite_memory_or_index(self):
        with tempfile.TemporaryDirectory() as tmp, patch('sys.stderr', new_callable=io.StringIO):
            root = Path(tmp)
            for name in ('memory.json', 'memory.index.json'):
                self.assertEqual(main([tmp, '--report', '--output', str(root / '.njordcup' / name)]), 2)

    def test_conflicting_modes_are_rejected(self):
        with patch('sys.stderr', new_callable=io.StringIO):
            for args in (['--automatic', '--report'], ['--automatic', '--model', 'test', '--sarif', 'r.sarif'], ['--report', '--summary']):
                with self.subTest(args=args), self.assertRaises(SystemExit) as exc:
                    main(args)
                self.assertEqual(exc.exception.code, 2)
