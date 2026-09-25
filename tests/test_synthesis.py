import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.errors import ReviewError, RunStopped
from njordcup.provider import OpenAIProvider
from njordcup.synthesis import saved_synthesis, synthesize


def envelope(text='Combined observations <script>alert(1)</script>', finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': text}}]}


def provider(**kwargs):
    return OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt', **kwargs)


class SynthesisTests(unittest.TestCase):
    def test_bounded_batches_resume_reuse_and_invalidation(self):
        report = {'narrative_analysis': 'Source details ' * 900}
        saved, checkpoints = {}, []
        p = provider(max_input_chars=3500, max_calls=1)
        with patch.object(p, '_request', side_effect=[envelope('Short summary'), ReviewError('Call budget exhausted')]):
            state = synthesize(report, saved, p, lambda: checkpoints.append(json.dumps(saved)))
        self.assertEqual(state['status'], 'incomplete')
        self.assertEqual(sum(n['complete'] for n in state['nodes'].values()), 1)
        first_key = next(iter(state['nodes']))
        p = provider(max_input_chars=3500, max_calls=50)
        with patch.object(p, '_request', return_value=envelope('Short summary')) as request:
            state = synthesize(report, saved, p, lambda: None)
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(request.call_count, len(state['nodes']) - 1)
        self.assertIn(first_key, state['nodes'])
        self.assertLessEqual(p.max_request_chars, 3500)
        with patch.object(p, '_request') as request:
            synthesize(report, saved, p, lambda: None)
        request.assert_not_called()
        self.assertIs(saved_synthesis(report, saved), state)
        self.assertIsNone(saved_synthesis({'narrative_analysis': 'changed'}, saved))
        self.assertGreater(len(checkpoints), 1)

    def test_partial_output_is_saved_but_not_presented_as_complete(self):
        saved = {}
        p = provider()
        with patch.object(p, '_request', return_value=envelope('Truncated thought', 'length')):
            state = synthesize({'summary': 'analysis'}, saved, p, lambda: None)
        self.assertEqual(state['status'], 'incomplete')
        self.assertNotIn('text', state)
        self.assertEqual(next(iter(state['nodes'].values()))['text'], 'Truncated thought')
        with patch.object(p, '_request', return_value=envelope('Finished')):
            state = synthesize({'summary': 'analysis'}, saved, p, lambda: None)
        self.assertEqual(state['text'], 'Finished')

    def test_cli_both_reports_offline_reuse_and_stale_suppression(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            opener.return_value.open.side_effect = lambda *a, **k: io.BytesIO(json.dumps(envelope()).encode())
            args = [tmp, '--model', 'local', '--base-url', 'http://localhost/v1', '--output-mode', 'prompt']
            with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([*args, '--implementation-analysis']), 0)
                self.assertEqual(main([*args, '--automatic']), 0)
                for flag, name, html in [('--report', 'memory.json', 'memory.report.html'),
                                         ('--implementation-report', 'memory.implementation.json', 'memory.implementation.html')]:
                    path = root / '.njordcup' / name
                    original = json.loads(path.read_text())
                    # Synthesis always uses plain model text, even with the default output mode.
                    self.assertEqual(main([*args, '--output-mode', 'json_schema', flag, '--synthesize']), 0)
                    saved = json.loads(path.read_text())
                    self.assertEqual(saved.pop('report_synthesis')['status'], 'complete')
                    self.assertEqual(saved, original)
                    rendered = (path.parent / html).read_text()
                    self.assertIn('AI executive summary', rendered)
                    self.assertIn('&lt;script&gt;', rendered)
                    self.assertNotIn('<script>', rendered)
                    calls = opener.return_value.open.call_count
                    self.assertEqual(main([tmp, flag]), 0)
                    self.assertEqual(opener.return_value.open.call_count, calls)
                    self.assertIn('AI executive summary', (path.parent / html).read_text())
                    saved = json.loads(path.read_text())
                    if flag == '--report':
                        saved['overview']['summary'] = 'changed'
                    else:
                        saved['summary'] = 'changed'
                    path.write_text(json.dumps(saved))
                    self.assertEqual(main([tmp, flag]), 0)
                    self.assertNotIn('AI executive summary', (path.parent / html).read_text())

    def test_interrupted_synthesis_preserves_existing_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / '.njordcup'
            directory.mkdir()
            (directory / 'memory.implementation.json').write_text(json.dumps({'kind': 'implementation_analysis', 'version': 1, 'pages': {}, 'summary': 'Empty', 'status': 'incomplete', 'fingerprint': 'snapshot'}))
            destination = directory / 'memory.implementation.html'
            destination.write_text('previous report')
            with patch('njordcup.provider.OpenAIProvider.ask', side_effect=RunStopped('cancelled', 'stopped')), \
                 patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--implementation-report', '--synthesize', '--model', 'local',
                                       '--base-url', 'http://localhost/v1', '--output', str(destination)]), 130)
            self.assertEqual(destination.read_text(), 'previous report')
            saved = json.loads((directory / 'memory.implementation.json').read_text())
            self.assertEqual(saved['report_synthesis']['status'], 'incomplete')
