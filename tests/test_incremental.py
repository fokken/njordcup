import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.index import build_index, affected_files
from njordcup.incremental import reusable_checkpoint
from test_narrative import envelope


class IncrementalTests(unittest.TestCase):
    def test_only_changed_file_is_reviewed_within_same_component(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.build_opener') as opener:
            root = Path(tmp)
            (root / 'a.py').write_text('def first():\n    return 1\n')
            (root / 'b.py').write_text('def second():\n    return 2\n')
            sent = []
            def respond(request, **kwargs):
                payload = json.loads(json.loads(request.data)['messages'][1]['content'])
                if payload.get('stage') == 'discover':
                    sent.extend(payload['target_paths'])
                return io.BytesIO(json.dumps(envelope('Saved analysis')).encode())
            opener.return_value.open.side_effect = respond
            args = [tmp, '--automatic', '--model', 'local', '--base-url', 'http://localhost/v1', '--output-mode', 'prompt']
            def run(extra=()):
                with patch('sys.stdout', new_callable=io.StringIO) as out, patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(main([*args, *extra]), 0)
                return json.loads(out.getvalue())
            run()
            self.assertEqual(sent, ['a.py', 'b.py'])
            sent.clear()
            (root / 'a.py').write_text('def first():\n    return 3\n')
            with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([a if a != '--automatic' else '--flyover-only' for a in args]), 0)
            self.assertEqual(sent, [])
            interim = json.loads((root / '.njordcup/memory.json').read_text())
            self.assertEqual(interim['reviews'], [])
            self.assertTrue(interim['resume_reviews'])
            result = run()
            self.assertEqual(sent, ['a.py'])
            self.assertEqual(result['reuse']['files'], ['b.py'])
            self.assertEqual({a['path'] for a in result['file_analysis']}, {'a.py', 'b.py'})
            self.assertTrue(all(a['complete'] for a in result['file_analysis']))
            self.assertNotIn('flyover', result['performance']['phases'])
            self.assertIn('review', result['performance']['phases'])
            with patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([tmp, '--report']), 0)
            self.assertIn('Review execution', (root / '.njordcup/memory.report.html').read_text())
            before = opener.return_value.open.call_count
            run()
            self.assertEqual(opener.return_value.open.call_count, before)
            sent.clear()
            run(['--rerun'])
            self.assertEqual(sent, ['a.py', 'b.py'])

    def test_dependency_changes_removals_and_added_targets(self):
        sources = {'app.py': 'from helper import clean\nclean(input)\n',
                   'helper.py': 'def clean(value):\n    return value\n', 'other.py': 'unrelated = 1\n'}
        old = build_index(sources, list(sources))
        records = {c['id']: {**c, 'status': 'complete', 'findings': [], 'dependencies': {p: old['files'][p]['hash']}}
                   for p in sources for c in old['files'][p]['chunks']}
        previous = {'chunk_results': records, 'review_signature': 'sig', 'findings': [{'title': 'stale'}]}
        changed = {**sources, 'helper.py': 'def clean(value):\n    return str(value)\n', 'new.py': 'new = 1\n'}
        current = build_index(changed, list(changed), old)
        carry = reusable_checkpoint(previous, list(changed), current, affected_files(old, current))
        self.assertEqual({c['path'] for c in carry['chunk_results'].values()}, {'other.py'})
        self.assertNotIn('findings', carry)
        # An explicitly supplied reference need not be a statically known dependency.
        record = next(iter(carry['chunk_results'].values()))
        record['dependencies']['helper.py'] = old['files']['helper.py']['hash']
        self.assertIsNone(reusable_checkpoint(carry, list(changed), current, set()))
        self.assertIsNone(reusable_checkpoint(previous, ['new.py'], current, set()))
