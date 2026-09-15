from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.agent import review
from njordcup.cli import main
from njordcup.sarif import load_sarif
from test_scale import AutomaticProvider, make_assessment
from test_review import FINDING


def location(uri="app.py", line=1):
    return {"physicalLocation": {"artifactLocation": {"uri": uri}, "region": {"startLine": line}}}


def document(results):
    return {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "Semgrep"}}, "results": results}]}


class SarifTests(unittest.TestCase):
    def test_rule_metadata_and_unresolved_flow_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "r.sarif"
            data = document([{"ruleId": "eval", "locations": [location()], "relatedLocations": [location("missing.py")]}])
            data["runs"][0]["tool"]["driver"]["rules"] = [{"id": "eval", "properties": {"tags": ["CWE-95: Eval Injection"]},
                                                            "shortDescription": {"text": "User-controlled evaluation"}}]
            path.write_text(json.dumps(data))
            candidate = load_sarif(path, root, {"app.py": "eval(user_input)"})["candidates"][0]
            self.assertEqual(candidate["cwes"], ["CWE-95"])
            self.assertEqual(candidate["rule_description"], "User-controlled evaluation")
            self.assertEqual(candidate["unresolved_related_locations"], 1)

    def test_legacy_completed_group_is_reinvestigated_by_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("safe()\n")
            sarif = root / "r.sarif"
            sarif.write_text(json.dumps(document([{"ruleId": "test", "locations": [location()]}])))
            args = [tmp, "--model", "test", "--sarif", str(sarif)]
            with patch("njordcup.cli.OpenAIProvider", return_value=AutomaticProvider()), patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(main([*args, "--flyover-only"]), 0)
            path = root / ".njordcup" / "memory.json"
            memory = json.loads(path.read_text())
            scan = memory["sarif_scans"][memory["active_sarif_scan"]]
            memory["reviews"] = [{"id": "legacy", "area_id": scan["area_ids"][0], "saved_at": "old",
                                   "report": {"status": "complete", "findings": [], "sarif_assessments": [
                                       {"result_id": scan["candidates"][0]["id"], "status": "confirmed"}]}}]
            path.write_text(json.dumps(memory))
            provider = AutomaticProvider()
            with patch("njordcup.cli.OpenAIProvider", return_value=provider), patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main([*args, "--investigate-all"]), 0)
                self.assertEqual(json.loads(out.getvalue())["sarif"]["counts"]["not_confirmed"], 1)
            self.assertEqual(provider.calls, 2)

    def test_all_unresolved_findings_are_reported_even_with_no_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.sarif"
            path.write_text(json.dumps(document([{"locations": [location("missing.py")]}])))
            with patch("njordcup.cli.OpenAIProvider", return_value=AutomaticProvider()), patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(main([tmp, "--model", "test", "--sarif", str(path), "--investigate-all"]), 2)
            self.assertEqual(json.loads(out.getvalue())["sarif"]["counts"]["inconclusive"], 1)

    def test_many_findings_at_one_location_are_bounded_but_all_preserved(self):
        from njordcup.index import build_index
        from njordcup.sarif import import_scan
        candidates = [{"id": str(i), "rule_id": "rule", "path": "app.py", "line": 1, "location_status": "resolved"} for i in range(25)]
        memory = {"overview": {"areas": []}}
        import_scan(memory, {"id": "scan", "candidates": candidates}, build_index({"app.py": "safe()"}, ["app.py"]))
        groups = memory["overview"]["areas"]
        self.assertEqual([len(a["sarif_candidates"]) for a in groups], [10, 10, 5])

    def test_import_includes_suppressed_and_unresolved_results_and_flows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "results.sarif"
            path.write_text(json.dumps(document([
                {"ruleId": "test", "message": {"text": "untrusted"}, "level": "note", "suppressions": [{"kind": "inSource"}],
                 "locations": [location()], "codeFlows": [{"threadFlows": [{"locations": [{"location": location("auth.py")}]}]}]},
                {"locations": [location("../outside.py")]}, {"locations": [location("https://example.com/code.py")]}])))
            scan = load_sarif(path, root, {"app.py": "safe()", "auth.py": "check()"})
            self.assertEqual(len(scan["candidates"]), 3)
            self.assertTrue(scan["candidates"][0]["suppressed"])
            self.assertEqual(scan["candidates"][0]["related_locations"][0]["path"], "auth.py")
            self.assertEqual(scan["candidates"][1]["location_status"], "unresolved")

    def test_artifact_index_base_uri_and_percent_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "r.sarif"
            data = document([{"locations": [{"physicalLocation": {"artifactLocation": {"index": 0}, "region": {"startLine": 1}}}]}])
            data["runs"][0].update(artifacts=[{"location": {"uri": "a%20b.py", "uriBaseId": "ROOT"}}],
                                   originalUriBaseIds={"ROOT": {"uri": root.as_uri() + "/"}})
            path.write_text(json.dumps(data))
            self.assertEqual(load_sarif(path, root, {"a b.py": "safe()"})["candidates"][0]["path"], "a b.py")

    def test_confirmed_assessment_requires_verified_finding(self):
        seed = {"id": "one", "path": "app.py", "line": 1, "related_locations": []}
        class ConfirmProvider(AutomaticProvider):
            def ask(self, instructions, payload, schema):
                return {"findings": [FINDING], "context_paths": [], "assessments": [make_assessment(seed, "eval(user_input)", "confirmed", "CWE-95")]}
        result = review({"app.py": "safe()"}, ["app.py"], [], ConfirmProvider(), seeds=[seed])
        self.assertEqual(result["sarif_assessments"][0]["status"], "inconclusive")
        valid = review({"app.py": "eval(user_input)"}, ["app.py"], [], ConfirmProvider(), seeds=[seed])
        self.assertEqual(valid["sarif_assessments"][0]["status"], "confirmed")

    def test_all_findings_resume_without_prompts_or_severity_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("safe()\n")
            path = root / "results.sarif"
            path.write_text(json.dumps(document([{"ruleId": f"rule{i}", "level": level, "locations": [location()]}
                                                for i, level in enumerate(["note", "warning", "error"])])))
            args = [tmp, "--model", "test", "--sarif", str(path), "--investigate-all", "--max-calls", "2"]
            with patch("njordcup.cli.OpenAIProvider", return_value=AutomaticProvider(2)), patch("builtins.input") as prompt, patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main(args), 2)
                partial = json.loads(out.getvalue())["sarif"]
                self.assertEqual(partial["total"], 3)
                self.assertEqual(partial["counts"]["not_confirmed"], 1)
                prompt.assert_not_called()
            provider = AutomaticProvider()
            with patch("njordcup.cli.OpenAIProvider", return_value=provider), patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main([*args[:-2], "--max-calls", "20"]), 0)
                self.assertEqual(json.loads(out.getvalue())["sarif"]["counts"]["not_confirmed"], 3)
            self.assertEqual(provider.calls, 4)
