from copy import deepcopy
from html import escape
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.agent import review, SCHEMA
from njordcup.cli import main
from njordcup.errors import ReviewError
from njordcup.implementation import analyze_implementation
from njordcup.index import build_index
from njordcup.mapping import hierarchical_flyover
from njordcup.narrative import Narrative
from njordcup.provider import OpenAIProvider

TEXT = '## Analysis\n\nPotential injection in app.py.\n```not-json\n<script>alert(1)</script>\n```\nDetails: café — 未验证\n'


def envelope(text=TEXT, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': text}}]}


def provider(**kwargs):
    return OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt', **kwargs)


class NarrativeTests(unittest.TestCase):
    def test_json_envelope_with_arbitrary_content_is_preserved_and_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = provider(cache=tmp)
            with patch.object(p, '_request', return_value=envelope()) as request:
                first = p.ask('old JSON instructions', {}, SCHEMA)
                second = p.ask('old JSON instructions', {}, SCHEMA)
            self.assertEqual(first, TEXT)
            self.assertEqual(second, TEXT)
            self.assertIsInstance(second, Narrative)
            self.assertTrue(second.complete)
            request.assert_called_once()
            body = json.loads(request.call_args.args[0].data)
            self.assertNotIn('response_format', body)
            self.assertNotIn('Return JSON matching', body['messages'][0]['content'])
            self.assertNotIn('properties', body['messages'][0]['content'])

    def test_json_looking_content_is_not_interpreted(self):
        p = provider()
        with patch.object(p, '_request', return_value=envelope('{"findings": "anything"}')):
            result = review({'app.py': 'safe()'}, ['app.py'], [], p)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['narrative_analysis'][0]['text'], '{"findings": "anything"}')

    def test_truncated_text_checkpoint_survives_failed_resumption(self):
        p = provider()
        snapshots = []
        with patch.object(p, '_request', return_value=envelope(finish='length')):
            result = review({'app.py': 'safe()'}, ['app.py'], [], p, checkpoint=lambda r: snapshots.append(deepcopy(r)))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(snapshots[-1]['narrative_analysis'][0]['text'], TEXT)
        self.assertEqual(result['coverage']['chunks_reviewed'], 0)
        with patch.object(p, '_request', side_effect=ReviewError('offline')):
            resumed = review({'app.py': 'safe()'}, ['app.py'], [], p, previous=result)
        self.assertEqual(resumed['narrative_analysis'][0]['text'], TEXT)

    def test_sarif_prose_does_not_become_a_confirmed_disposition(self):
        p = provider()
        with patch.object(p, '_request', return_value=envelope('Confirmed critical issue!')):
            result = review({'app.py': 'safe()'}, ['app.py'], [], p,
                            seeds=[{'id': 'one', 'path': 'app.py', 'line': 1, 'related_locations': []}])
        self.assertEqual(result['sarif_assessments'][0]['status'], 'inconclusive')
        self.assertEqual(result['findings'], [])
        self.assertTrue(result['narrative_analysis'])

    def test_cli_implementation_review_and_both_html_reports(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            root = Path(tmp)
            (root / 'app.py').write_text('safe()')
            opener.return_value.open.side_effect = lambda *a, **k: io.BytesIO(json.dumps(envelope()).encode())
            args = [tmp, '--model', 'local', '--base-url', 'http://localhost/v1', '--output-mode', 'prompt']
            with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([*args, '--implementation-analysis']), 0)
            impl = json.loads((root / '.njordcup/memory.implementation.json').read_text())
            self.assertEqual(next(iter(impl['pages'].values()))['narrative']['text'], TEXT)
            with patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO) as err:
                self.assertEqual(main([*args, '--automatic']), 0)
            report = json.loads(out.getvalue())
            self.assertEqual(report['narrative_analysis'][0]['text'], TEXT)
            self.assertTrue(report['requires_manual_review'])
            self.assertIn('Model analysis saved (unstructured)', err.getvalue())
            before = opener.return_value.open.call_count
            with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([*args, '--automatic']), 0)
                self.assertEqual(main([tmp, '--report']), 0)
                self.assertEqual(main([tmp, '--implementation-report']), 0)
            self.assertEqual(opener.return_value.open.call_count, before)
            security_html = (root / '.njordcup/memory.report.html').read_text()
            self.assertNotIn('Structured findings', security_html)
            self.assertNotIn('<th>Issues</th>', security_html)
            self.assertNotIn('No structured findings', security_html)
            self.assertIn('Saved security analyses', security_html)
            self.assertIn('Source files: app.py', security_html)
            for path in ('memory.report.html', 'memory.implementation.html'):
                html = (root / '.njordcup' / path).read_text()
                self.assertIn(escape(TEXT, quote=True), html)
                self.assertNotIn('<script>', html)

    def test_narrative_review_processes_every_indexed_chunk(self):
        sources = {f'module{i}/app.txt': 'process(input)\n' * 60 for i in range(3)}
        index = build_index(sources, list(sources), chunk_chars=400)
        p = provider()
        transmitted = []
        def respond(request, *args):
            body = json.loads(request.data)
            payload = json.loads(body['messages'][1]['content'])
            transmitted.extend(payload['target_chunks'])
            for heading in ('Title', 'Description', 'Impact', 'Remediation'):
                self.assertIn(heading, body['messages'][0]['content'])
            return envelope('Analysis for batch ' + str(len(transmitted)))
        with patch.object(p, '_request', side_effect=respond):
            result = review(sources, list(sources), [], p, repository_index=index, batch_chars=1800)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(set(transmitted), set(result['chunk_results']))
        self.assertEqual(len(transmitted), result['coverage']['chunks_total'])
        self.assertEqual(result['coverage']['chunks_reviewed'], result['coverage']['chunks_total'])
        self.assertGreater(len(result['narrative_analysis']), 1)
        self.assertEqual({path for a in result['narrative_analysis'] for path in a['paths']}, set(sources))

    def test_automatic_has_no_default_twenty_call_cap(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            root = Path(tmp)
            for i in range(24):
                module = root / f'module{i}'
                module.mkdir()
                (module / 'app.txt').write_text('process(input)')
            opener.return_value.open.side_effect = lambda *a, **k: io.BytesIO(json.dumps(envelope()).encode())
            with patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO):
                result = main([tmp, '--automatic', '--model', 'local', '--base-url', 'http://localhost/v1',
                               '--output-mode', 'prompt'])
            self.assertEqual(result, 0)
            self.assertGreater(opener.return_value.open.call_count, 20)
            report = json.loads(out.getvalue())
            self.assertEqual(report['status'], 'complete')
            self.assertTrue(all(a['status'] == 'complete' for a in report['area_progress']))

    def test_partial_mapping_and_implementation_are_kept_on_disk(self):
        sources = {'app.py': 'safe()'}
        index = build_index(sources, list(sources))
        with tempfile.TemporaryDirectory() as tmp:
            p = provider()
            with patch.object(p, '_request', return_value=envelope(finish='length')):
                mapped, _ = hierarchical_flyover(sources, list(sources), p, Path(tmp) / 'memory.json', index)
                analyzed = analyze_implementation(sources, list(sources), p, Path(tmp) / 'impl.json', index)
            self.assertEqual(mapped['narrative_analysis'][0]['text'], TEXT)
            self.assertEqual(mapped['coverage']['pages_pending'], 1)
            self.assertEqual(analyzed['status'], 'incomplete')
            self.assertEqual(next(iter(analyzed['pages'].values()))['narrative']['text'], TEXT)
            with patch.object(p, '_request', side_effect=ReviewError('offline')):
                mapped, _ = hierarchical_flyover(sources, list(sources), p, Path(tmp) / 'memory.json', index)
                analyzed = analyze_implementation(sources, list(sources), p, Path(tmp) / 'impl.json', index)
            self.assertEqual(mapped['narrative_analysis'][0]['text'], TEXT)
            self.assertEqual(next(iter(analyzed['pages'].values()))['narrative']['text'], TEXT)
