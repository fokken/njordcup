from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from njordcup.agent import review
from njordcup.context import compact_memory
from njordcup.index import build_index, CodeIndex
from njordcup.provider import OpenAIProvider
from test_narrative import envelope


class ContextTests(unittest.TestCase):
    def test_memory_is_bounded_relevant_and_does_not_mutate_saved_analysis(self):
        memory = {'summary': 'Architecture ' * 500, 'tech_stack': ['Python'],
                  'selected_area': {'title': 'API', 'reason': 'Trust boundary ' * 100,
                                    'attack_surfaces': ['User input']},
                  'previous_reviews': [
                      {'findings': [{'path': 'other.py', 'title': 'UNRELATED'}],
                       'narrative_excerpts': [{'paths': ['other.py'], 'text': 'UNRELATED'}]},
                      {'findings': [{'path': 'app.py', 'title': 'Relevant issue'}],
                       'narrative_excerpts': [{'paths': ['app.py'], 'text': 'Relevant history'}]}],
                  'implementation_analysis': {'pages': [
                      {'component': 'api', 'paths': ['app.py'], 'summary': 'Relevant implementation'},
                      {'component': 'other', 'paths': ['other.py'], 'summary': 'UNRELATED'}]}}
        before = deepcopy(memory)
        result = compact_memory(memory, ['app.py'])
        encoded = json.dumps(result)
        self.assertLessEqual(len(encoded), 3000)
        self.assertNotIn('UNRELATED', encoded)
        self.assertIn('Relevant history', encoded)
        self.assertIn('Relevant implementation', encoded)
        self.assertEqual(memory, before)

    def test_reference_selection_reaches_late_definition_and_respects_budget(self):
        sources = {'app.py': 'from helper import validate_user\nvalidate_user(user_input)\n',
                   'helper.py': '# irrelevant introduction\n' * 200 +
                   'def validate_user(value):\n    return bool(value)\n' + '# trailing notes\n' * 100}
        index = build_index(sources, ['app.py'], chunk_chars=1800)
        lookup = CodeIndex(index, sources)
        target = lookup.by_path['app.py']
        ranked = lookup.reference_chunks(target)
        selected = lookup.chunks[ranked[0]]
        self.assertTrue(selected['start'] <= 201 <= selected['end'])
        entry = lookup.reference_entry(ranked[0], target, 500)
        self.assertIn('def validate_user', entry['lines'])
        self.assertLessEqual(len(json.dumps(entry)), 500)
        self.assertTrue(entry['excerpt'])
        for numbered in entry['lines'].splitlines():
            number, text = numbered.split(': ', 1)
            self.assertEqual(text, sources['helper.py'].splitlines()[int(number) - 1])

    def test_narrative_payload_savings_and_unchanged_target_code(self):
        sources = {'app.py': 'from helper import validate_user\nvalidate_user(user_input)\n',
                   'helper.py': '# unrelated header\n' * 300 + 'def validate_user(value):\n    return bool(value)\n'}
        index = build_index(sources, ['app.py'], chunk_chars=2400)
        lookup = CodeIndex(index, sources)
        overview = {'summary': 'Repository architecture ' * 400,
                    'previous_reviews': [{'findings': [{'path': 'other.py', 'title': 'Noise ' * 400}]}]}
        p = OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt')
        with patch.object(p, '_request', return_value=envelope('No supported issue; reviewed supplied app.py lines.')) as request:
            result = review(sources, ['app.py'], [], p, repository_index=index, overview=overview)
        payload = json.loads(json.loads(request.call_args.args[0].data)['messages'][1]['content'])
        target_ids = payload['target_chunks']
        targets = [e for e in payload['files'] if e['id'] in target_ids]
        self.assertEqual(targets, [lookup.entry(c) for c in target_ids])
        self.assertEqual(result['coverage']['lines_reviewed'], 2)
        self.assertEqual(result['status'], 'complete')
        self.assertNotIn('related_files', payload)
        self.assertIn('def validate_user', json.dumps(payload['files']))
        old = {**payload, 'architectural_memory': overview, 'related_files': lookup.related(['app.py']),
               'files': targets + [lookup.entry(c) for r in lookup.related(['app.py']) for c in lookup.by_path[r['path']][:2]]}
        old_size, new_size = len(json.dumps(old)), len(json.dumps(payload))
        self.assertLess(new_size, old_size // 2)

    def test_explicit_scanner_flow_context_is_preserved_before_optional_references(self):
        sources = {'app.py': 'run(input)\n', 'flow.py': 'def check(value):\n    return value\n'}
        index = build_index(sources, ['app.py'])
        p = OpenAIProvider('local', base_url='http://localhost/v1', output_mode='prompt')
        seed = {'id': 'scan', 'path': 'app.py', 'line': 1,
                'related_locations': [{'path': 'flow.py', 'line': 2}]}
        with patch.object(p, '_request', return_value=envelope('Scanner assessment')) as request:
            review(sources, ['app.py'], [], p, repository_index=index, seeds=[seed])
        payload = json.loads(json.loads(request.call_args.args[0].data)['messages'][1]['content'])
        flow = next(e for e in payload['files'] if e['path'] == 'flow.py')
        self.assertEqual(flow, CodeIndex(index, sources).entry(index['files']['flow.py']['chunks'][0]['id']))
