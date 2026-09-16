import json
from pathlib import Path
import unittest

from njordcup.evaluate import evaluate
from test_scale import AutomaticProvider


class EvaluationTests(unittest.TestCase):
    def test_missing_known_issues_are_counted_as_false_negatives(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "evals" / "cases.json").read_text())
        result = evaluate(cases, AutomaticProvider())
        self.assertEqual(result["false_negative"], sum(len(set(c["expected_cwes"])) for c in cases))
        self.assertEqual(result["recall"], 0)
        self.assertIsNone(result["precision"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["incomplete_cases"], 0)
        self.assertEqual({row["language"] for row in result["cases"]}, {"python", "javascript", "go", "ruby"})

    def test_cross_file_case_keeps_reference_code_outside_target_scope(self):
        from test_review import FakeProvider, answer
        provider = FakeProvider(answer(context=['routes.js']), answer())
        case = {'id': 'cross_file', 'language': 'javascript', 'expected_cwes': [],
                'sources': {'sink.js': 'use(value);', 'routes.js': 'use(request.query.value);'},
                'targets': ['sink.js']}
        result = evaluate([case], provider)
        self.assertTrue(result['complete'])
        self.assertEqual(provider.payloads[-1]['target_paths'], ['sink.js'])
        self.assertEqual({e['path'] for e in provider.payloads[-1]['files']}, {'sink.js', 'routes.js'})
