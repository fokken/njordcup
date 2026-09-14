from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.flyover import flyover
from njordcup.memory import area_progress, record_review, review_context, summarize
from test_flyover import OVERVIEW, Provider
from test_review import FINDING


def result(status="complete", findings=()):
    return {"status": status, "findings": list(findings), "reviewed": ["app.py"],
            "unreviewed": [], "limitations": [], "errors": [],
            "usage": {"calls": 2, "input_tokens": 100}}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / ".njordcup" / "memory.json"
        self.sources = {"app.py": "route()"}
        (self.root / "app.py").write_text("route()")
        self.memory, _ = flyover(self.sources, ["app.py"], Provider(), self.path)

    def test_attempts_persist_and_latest_attempt_sets_progress(self):
        record_review(self.path, self.memory, 1, result("incomplete", [FINDING]))
        self.assertEqual(area_progress(self.memory)[0]["status"], "incomplete")
        record_review(self.path, self.memory, 1, result())
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved["reviews"]), 2)
        self.assertEqual(saved["reviews"][0]["report"]["findings"], [FINDING])
        self.assertEqual(area_progress(saved)[0], {"id": 1, "title": "Request handling", "status": "complete", "attempts": 2, "findings": 0})
        self.assertEqual(summarize(saved)["findings"], [])
        self.assertTrue(review_context(saved)["previous_reviews"])

    def test_refresh_archives_history_and_resets_progress(self):
        record_review(self.path, self.memory, 1, result(findings=[FINDING]))
        refreshed, _ = flyover(self.sources, ["app.py"], Provider(), self.path, refresh=True)
        self.assertEqual(refreshed["reviews"], [])
        self.assertEqual(refreshed["archives"][0]["reviews"][0]["report"]["findings"], [FINDING])
        self.assertEqual(area_progress(refreshed)[0]["status"], "unreviewed")

    def test_summary_deduplicates_shared_findings_and_reports_remaining(self):
        self.memory["overview"]["areas"].append(deepcopy(OVERVIEW["areas"][0]))
        record_review(self.path, self.memory, 1, result(findings=[FINDING]))
        record_review(self.path, self.memory, 2, result("incomplete", [FINDING]))
        summary = summarize(self.memory)
        self.assertEqual(summary["findings_by_severity"]["high"], 1)
        self.assertEqual(summary["findings"][0]["area_ids"], [1, 2])
        self.assertEqual(summary["remaining_areas"], [2])
        self.assertEqual(summary["audit_status"], "in_progress")
        self.assertEqual(summary["review_usage_all_attempts"]["calls"], 4)

    @patch("njordcup.cli.OpenAIProvider")
    @patch("njordcup.cli.discover")
    def test_summary_requires_neither_model_nor_source_scan(self, discover, provider):
        record_review(self.path, self.memory, 1, result())
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(main([str(self.root), "--summary"]), 0)
        summary = json.loads(out.getvalue())
        self.assertEqual(summary["area_counts"]["complete"], 1)
        provider.assert_not_called()
        discover.assert_not_called()

    def test_summary_cannot_overwrite_memory(self):
        before = self.path.read_text()
        with patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main([str(self.root), "--summary", "--output", str(self.path)]), 2)
        self.assertEqual(self.path.read_text(), before)

    @patch("njordcup.cli.OpenAIProvider", return_value=Provider())
    @patch("njordcup.cli.review")
    def test_interactive_multiple_areas_saved_before_next_prompt(self, review, provider):
        overview = deepcopy(OVERVIEW)
        overview["areas"].append({**overview["areas"][0], "title": "Second area"})
        self.memory["overview"] = overview
        self.path.write_text(json.dumps(self.memory))
        review.side_effect = [result(), result("incomplete")]
        choices = iter(["1", "2", ""])
        def choose():
            choice = next(choices)
            persisted = json.loads(self.path.read_text())
            self.assertEqual(len(persisted.get("reviews", [])), {"1": 0, "2": 1, "": 2}[choice])
            return choice
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=choose), patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main([str(self.root), "--model", "test"]), 2)
        self.assertEqual(len(json.loads(out.getvalue())["reviews"]), 2)
        self.assertIn("previous_reviews", review.call_args_list[1].args[-1])
        self.assertEqual(len(json.loads(self.path.read_text())["reviews"]), 2)
