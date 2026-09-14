import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from njordcup.cli import main
from njordcup.flyover import flyover, make_payload, read_memory
from njordcup.provider import ReviewError

OVERVIEW = {"summary": "Small web app", "tech_stack": ["Python"], "dependencies": [],
            "areas": [{"title": "Request handling", "reason": "Accepts user input", "features": ["HTTP routes"],
                       "attack_surfaces": ["Untrusted request input"], "paths": ["app.py"]}], "unknowns": []}


class Provider:
    model = "test"
    base_url = "http://localhost:8000/v1"
    output_mode = "json_schema"

    def __init__(self):
        self.calls = 0

    def ask(self, instructions, payload, schema):
        self.calls += 1
        return OVERVIEW


class FlyoverTests(unittest.TestCase):
    def test_memory_reused_and_invalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            provider = Provider()
            sources = {"app.py": "route()"}
            _, reused = flyover(sources, ["app.py"], provider, path)
            self.assertFalse(reused)
            _, reused = flyover(sources, ["app.py"], provider, path)
            self.assertTrue(reused)
            self.assertEqual(provider.calls, 1)
            with self.assertRaises(ReviewError):
                read_memory(path, {"app.py": "changed()"}, ["app.py"], provider)
            flyover({"app.py": "changed()"}, ["app.py"], provider, path)
            self.assertEqual(provider.calls, 2)

    def test_payload_bounded_and_manifests_prioritized(self):
        sources = {f"file{i}.py": "x" * 10000 for i in range(100)}
        sources["package.json"] = '{"dependencies": {"express": "5"}}'
        payload = make_payload(sources, list(sources), char_budget=3000)
        self.assertEqual(payload["samples"][0]["path"], "package.json")
        self.assertLessEqual(sum(len(json.dumps(s)) for s in payload["samples"]), 3000)
        self.assertGreater(payload["coverage"]["omitted_files"], 0)

    @patch("njordcup.cli.OpenAIProvider", return_value=Provider())
    @patch("njordcup.cli.review")
    def test_flyover_stops_then_explicit_area_uses_memory(self, review, provider):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO) as out:
            root = Path(tmp)
            (root / "app.py").write_text("route()")
            (root / "other.py").write_text("other()")
            args = [tmp, "--model", "test", "--flyover-only"]
            self.assertEqual(main(args), 0)
            self.assertEqual(json.loads(out.getvalue())["status"], "awaiting_selection")
            review.assert_not_called()
            review.return_value = {"status": "complete", "findings": []}
            self.assertEqual(main([tmp, "--model", "test", "--area", "1"]), 0)
            self.assertEqual(review.call_args.args[1], ["app.py"])
            self.assertEqual(review.call_args.args[-1], OVERVIEW)
            (root / "app.py").write_text("changed()")
            review.reset_mock()
            with patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(main([tmp, "--model", "test", "--area", "1"]), 2)
            review.assert_not_called()

    @patch("njordcup.cli.OpenAIProvider", return_value=Provider())
    @patch("njordcup.cli.review")
    def test_interactive_decline_does_not_review(self, review, provider):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO), patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=""):
            (Path(tmp) / "app.py").write_text("route()")
            self.assertEqual(main([tmp, "--model", "test"]), 0)
            review.assert_not_called()

    @patch("njordcup.cli.OpenAIProvider", return_value=Provider())
    @patch("njordcup.cli.review")
    def test_noninteractive_default_never_reviews(self, review, provider):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO), patch("sys.stdin.isatty", return_value=False):
            (Path(tmp) / "app.py").write_text("route()")
            self.assertEqual(main([tmp, "--model", "test"]), 0)
            review.assert_not_called()
