import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from njordcup.agent import review, SCHEMA
from njordcup.cli import main
from njordcup.provider import OpenAIProvider, ReviewError
from njordcup.repository import discover


FINDING = {"path": "app.py", "line": 1, "title": "Unsafe evaluation", "cwe": "CWE-95",
           "severity": "high", "evidence": "eval(user_input)", "attack_scenario": "Untrusted input reaches eval",
           "remediation": "Parse data without evaluating code"}


class FakeProvider:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.payloads = []

    def ask(self, instructions, payload, schema):
        self.payloads.append(json.loads(json.dumps(payload)))
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return answer


def answer(findings=(), context=()):
    return {"findings": list(findings), "context_paths": list(context)}


class ReviewTests(unittest.TestCase):
    def test_verification_and_dedup(self):
        provider = FakeProvider(answer([FINDING, FINDING]), answer([FINDING, FINDING]))
        result = review({"app.py": "eval(user_input)"}, ["app.py"], [], provider)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(provider.payloads[-1]["stage"], "verify")

    def test_verifier_can_reject(self):
        result = review({"app.py": "eval(user_input)"}, ["app.py"], [], FakeProvider(answer([FINDING]), answer()))
        self.assertEqual(result["findings"], [])

    def test_hallucinated_evidence_is_not_reported(self):
        result = review({"app.py": "safe()"}, ["app.py"], [], FakeProvider(answer([FINDING]), answer([FINDING])))
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["status"], "incomplete")

    def test_context_retrieval_is_bounded(self):
        provider = FakeProvider(answer(context=["auth.py", "../secret.py"]), answer())
        result = review({"app.py": "route()", "auth.py": "check_user()"}, ["app.py"], [], provider)
        self.assertEqual([f["path"] for f in provider.payloads[-1]["files"]], ["app.py", "auth.py"])
        self.assertEqual(result["status"], "incomplete")

    def test_exhaustion_preserves_incomplete_scope(self):
        result = review({"app.py": "safe()"}, ["app.py"], [], FakeProvider(ReviewError("budget")))
        self.assertEqual(result["unreviewed"], ["app.py"])
        self.assertEqual(result["status"], "incomplete")

    def test_oversized_file_is_explicit(self):
        result = review({"app.py": "x" * 100}, ["app.py"], [], FakeProvider(), batch_chars=10)
        self.assertEqual(result["unreviewed"], ["app.py"])

    def test_source_discovery_excludes_secrets_symlinks_and_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("safe()")
            (root / ".env.py").write_text("secret")
            (root / "linked.py").symlink_to(root / "app.py")
            (root / "large.py").write_text("x" * 100)
            sources, targets, skipped = discover(root, max_bytes=20)
            self.assertEqual(targets, ["app.py"])
            self.assertEqual(len(skipped), 3)

    def test_git_base_selects_changed_and_untracked_and_honors_ignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def git(*args):
                subprocess.run(["git", "-C", tmp, *args], check=True, capture_output=True)
            git("init")
            (root / "app.py").write_text("old()")
            (root / "other.py").write_text("safe()")
            (root / ".gitignore").write_text("ignored.py\n")
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
            (root / "app.py").write_text("new()")
            (root / "new.py").write_text("new()")
            (root / "ignored.py").write_text("secret()")
            sources, targets, _ = discover(root, "HEAD")
            self.assertEqual(set(targets), {"app.py", "new.py"})
            self.assertIn("other.py", sources)
            self.assertNotIn("ignored.py", sources)

    def test_dry_run_needs_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch("sys.stdout", new_callable=io.StringIO) as out:
            (Path(tmp) / "app.py").write_text("safe()")
            self.assertEqual(main([tmp, "--dry-run"]), 0)
            self.assertEqual(json.loads(out.getvalue())["targets"], ["app.py"])


class ProviderTests(unittest.TestCase):
    @patch("urllib.request.build_opener")
    def test_input_budget_rejects_request_before_network(self, opener):
        provider = OpenAIProvider("local", base_url="http://localhost:8000/v1", max_input_chars=100)
        with self.assertRaisesRegex(ReviewError, "character budget"):
            provider.ask("review", {"source": "x" * 1000}, SCHEMA)
        self.assertEqual(provider.calls, 0)
        opener.assert_not_called()

    def response(self, finish="stop", content=None):
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": finish, "message": {"content": json.dumps(answer()) if content is None else content}}],
                                     "usage": {"prompt_tokens": 12, "completion_tokens": 8}}).encode())

    @patch.dict(os.environ, {}, clear=True)
    @patch("urllib.request.build_opener")
    def test_custom_endpoint_modes_and_no_auth(self, opener):
        for mode in ["json_schema", "json_object", "prompt"]:
            opener.return_value.open.return_value = self.response()
            provider = OpenAIProvider("local-model", base_url="http://localhost:11434/v1/", output_mode=mode)
            self.assertEqual(provider.ask("review", {}, SCHEMA), answer())
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://localhost:11434/v1/chat/completions")
            self.assertIsNone(request.get_header("Authorization"))
            body = json.loads(request.data)
            self.assertEqual(body["model"], "local-model")
            self.assertEqual("response_format" in body, mode != "prompt")

    @patch("urllib.request.build_opener")
    def test_truncation_and_invalid_schema_fail(self, opener):
        for response in [self.response("length"), self.response(content='{"findings": []}')]:
            opener.return_value.open.return_value = response
            with self.assertRaises(ReviewError):
                OpenAIProvider("local", base_url="http://localhost:8000/v1").ask("review", {}, SCHEMA)

    @patch("urllib.request.build_opener")
    def test_cache_reuses_exact_requests_and_separates_endpoints(self, opener):
        with tempfile.TemporaryDirectory() as tmp:
            opener.return_value.open.side_effect = [self.response(), self.response()]
            first = OpenAIProvider("local", cache=tmp, base_url="http://localhost:8000/v1")
            first.ask("review", {}, SCHEMA)
            first.ask("review", {}, SCHEMA)
            self.assertEqual(first.calls, 1)
            self.assertEqual(first.cache_hits, 1)
            other = OpenAIProvider("local", cache=tmp, base_url="http://localhost:11434/v1")
            other.ask("review", {}, SCHEMA)
            self.assertEqual(other.calls, 1)


if __name__ == "__main__":
    unittest.main()
