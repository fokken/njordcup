import json
from pathlib import Path
import unittest

from njordcup.evaluate import evaluate
from test_scale import AutomaticProvider


class EvaluationTests(unittest.TestCase):
    def test_missing_known_issues_are_counted_as_false_negatives(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / "evals" / "cases.json").read_text())
        result = evaluate(cases, AutomaticProvider())
        self.assertEqual(result["false_negative"], 2)
        self.assertEqual(result["recall"], 0)
        self.assertIsNone(result["precision"])
        self.assertTrue(result["complete"])
