from copy import deepcopy
import unittest

from njordcup.adjudication import adjudicate, current_assessment
from njordcup.index import CodeIndex, build_index
from test_scale import make_assessment
from test_review import FINDING


class AdjudicationTests(unittest.TestCase):
    def setUp(self):
        self.sources = {"app.py": "eval(user_input)\nsafe()\n", "guard.py": "validate(user_input)\n"}
        self.lookup = CodeIndex(build_index(self.sources, list(self.sources)), self.sources)
        self.loaded = set(self.lookup.by_path["app.py"])
        self.seed = {"id": "one", "rule_id": "python.eval", "cwes": ["CWE-95"], "path": "app.py", "line": 1, "end_line": 1}

    def assess(self, assessment, findings=(FINDING,)):
        return adjudicate([assessment], [self.seed], list(findings), self.lookup, self.loaded, [])[0]

    def test_exact_confirmation_is_accepted(self):
        result = self.assess(make_assessment(self.seed, "eval(user_input)", "confirmed", "CWE-95"))
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["validation_errors"], [])

    def test_wrong_rule_and_cwe_at_same_location_are_not_confirmation(self):
        for field, value in [("rule_id", "unrelated.rule"), ("finding_cwe", "CWE-89")]:
            assessment = make_assessment(self.seed, "eval(user_input)", "confirmed", "CWE-95")
            assessment[field] = value
            result = self.assess(assessment, [{**FINDING, "cwe": assessment["finding_cwe"]}])
            self.assertEqual(result["status"], "inconclusive")
            self.assertTrue(result["validation_errors"])

    def test_unrelated_finding_in_same_chunk_is_not_confirmation(self):
        assessment = make_assessment(self.seed, "eval(user_input)", "confirmed", "CWE-95")
        assessment["finding_line"] = 2
        assessment["evidence"][1].update(line=2, quote="safe()")
        result = self.assess(assessment, [{**FINDING, "line": 2, "evidence": "safe()"}])
        self.assertEqual(result["status"], "inconclusive")

    def test_dismissal_requires_cited_counterevidence(self):
        assessment = make_assessment(self.seed, "eval(user_input)")
        assessment["evidence"] = assessment["evidence"][:1]
        self.assertEqual(self.assess(assessment, [])["status"], "inconclusive")

    def test_citations_must_be_exact_and_supplied(self):
        for citation in [{"path": "guard.py", "line": 1, "quote": "validate(user_input)"},
                         {"path": "app.py", "line": 1, "quote": "validate(user_input)"},
                         {"path": "../guard.py", "line": 1, "quote": "validate(user_input)"}]:
            assessment = make_assessment(self.seed, "eval(user_input)")
            assessment["evidence"][1].update(citation)
            self.assertEqual(self.assess(assessment, [])["status"], "inconclusive")

    def test_missing_context_prevents_dismissal(self):
        assessment = make_assessment(self.seed, "eval(user_input)")
        assessment["missing_context"] = ["Need caller validation"]
        self.assertEqual(self.assess(assessment, [])["status"], "inconclusive")

    def test_supplied_counterevidence_is_accepted(self):
        assessment = make_assessment(self.seed, "eval(user_input)")
        self.loaded.update(self.lookup.by_path["guard.py"])
        assessment["evidence"][1].update(path="guard.py", line=1, quote="validate(user_input)")
        self.assertEqual(self.assess(assessment, [])["status"], "not_confirmed")

    def test_legacy_decisions_require_readjudication(self):
        self.assertEqual(current_assessment({"status": "confirmed"})["status"], "inconclusive")
