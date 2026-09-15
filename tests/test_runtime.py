from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from njordcup.agent import review, SCHEMA
from njordcup.cli import main
from njordcup.errors import ReviewError, RunStopped
from njordcup.provider import OpenAIProvider, retry_delay
from njordcup.runtime import RunControl, handle_signals
from test_scale import AutomaticProvider


def response(content=None):
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
        "content": content or json.dumps({"findings": [], "context_paths": []})}}]}).encode())


def http_error(code, retry_after=None):
    return urllib.error.HTTPError("http://localhost/v1", code, "error",
                                  {"Retry-After": retry_after} if retry_after else {}, io.BytesIO())


class RetryTests(unittest.TestCase):
    @patch("urllib.request.build_opener")
    def test_transient_retries_use_configured_timeout_and_count_attempts(self, opener):
        control = RunControl()
        opener.return_value.open.side_effect = [http_error(429, "5"), TimeoutError(), response()]
        provider = OpenAIProvider("local", base_url="http://localhost/v1", request_timeout=9, control=control)
        with patch.object(control, "wait") as wait:
            provider.ask("review", {}, SCHEMA)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(provider.retries, 2)
        self.assertEqual(wait.call_args_list[0].args[0], 5)
        self.assertTrue(all(c.kwargs["timeout"] == 9 for c in opener.return_value.open.call_args_list))

    @patch("urllib.request.build_opener")
    def test_retry_attempts_cannot_exceed_call_budget(self, opener):
        opener.return_value.open.side_effect = [http_error(503), response()]
        provider = OpenAIProvider("local", max_calls=1, base_url="http://localhost/v1", max_retries=10)
        with patch.object(provider.control, "wait") as wait, self.assertRaises(RunStopped) as caught:
            provider.ask("review", {}, SCHEMA)
        self.assertEqual(caught.exception.reason, "call_budget")
        self.assertEqual(provider.calls, 1)
        wait.assert_not_called()

    @patch("urllib.request.build_opener")
    def test_retries_exhaust_and_do_not_cache_failures(self, opener):
        opener.return_value.open.side_effect = [http_error(503), http_error(503)]
        with tempfile.TemporaryDirectory() as tmp:
            provider = OpenAIProvider("local", base_url="http://localhost/v1", max_retries=1, cache=tmp)
            with patch.object(provider.control, "wait"), self.assertRaises(RunStopped) as caught:
                provider.ask("review", {}, SCHEMA)
            self.assertEqual(caught.exception.reason, "retry_exhausted")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    @patch("urllib.request.build_opener")
    def test_permanent_http_and_invalid_json_are_not_retried(self, opener):
        for item in [http_error(401), response("not-json"), response('{"wrong": 1}')]:
            opener.return_value.open.side_effect = None
            opener.return_value.open.return_value = item
            if isinstance(item, Exception):
                opener.return_value.open.side_effect = item
            provider = OpenAIProvider("local", base_url="http://localhost/v1")
            with patch.object(provider.control, "wait") as wait, self.assertRaises(ReviewError):
                provider.ask("review", {}, SCHEMA)
            self.assertEqual(provider.calls, 1)
            wait.assert_not_called()

    def test_retry_after_dates_invalid_values_and_clamping(self):
        with patch("njordcup.provider.random.uniform", return_value=1):
            self.assertEqual(retry_delay(0, "10000", 1, 30), 30)
            self.assertEqual(retry_delay(0, "nonsense", 1, 30), 1)
            self.assertEqual(retry_delay(0, "nan", 1, 30), 1)
            future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10))
            self.assertGreater(retry_delay(0, future, 1, 30), 8)

    @patch("urllib.request.build_opener")
    def test_cancel_during_backoff_prevents_next_attempt(self, opener):
        opener.return_value.open.side_effect = http_error(503)
        control = RunControl()
        provider = OpenAIProvider("local", base_url="http://localhost/v1", control=control, on_retry=lambda e: control.cancel())
        with self.assertRaises(RunStopped) as caught:
            provider.ask("review", {}, SCHEMA)
        self.assertEqual(caught.exception.reason, "cancelled")
        self.assertEqual(provider.calls, 1)

    @patch("urllib.request.build_opener")
    def test_expired_deadline_makes_no_request(self, opener):
        control = RunControl(max_seconds=1)
        control.deadline = 0
        provider = OpenAIProvider("local", base_url="http://localhost/v1", control=control)
        with self.assertRaises(RunStopped) as caught:
            provider.ask("review", {}, SCHEMA)
        self.assertEqual(caught.exception.reason, "deadline")
        opener.assert_not_called()


class CancellationTests(unittest.TestCase):
    def test_real_sigterm_interrupts_backoff(self):
        script = """from njordcup.runtime import RunControl, handle_signals
from njordcup.errors import RunStopped
control = RunControl()
with handle_signals(control):
    print('ready', flush=True)
    try:
        control.wait(30)
    except RunStopped as exc:
        print(exc.reason, flush=True)
        raise SystemExit(143)
"""
        process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual(process.returncode, 143, stderr)
            self.assertIn("sigterm", stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_signal_handlers_restore_and_set_cancellation(self):
        previous = signal.getsignal(signal.SIGTERM)
        control = RunControl()
        with handle_signals(control):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            with self.assertRaises(RunStopped) as caught:
                control.check()
            self.assertEqual(caught.exception.reason, "sigterm")
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_mid_review_cancellation_preserves_and_resumes_completed_chunks(self):
        class CancelProvider(AutomaticProvider):
            def ask(self, *args):
                if self.calls == 1:
                    raise RunStopped("cancelled", "Cancelled")
                return super().ask(*args)
        sources = {"app.py": "safe()\n" * 300}
        checkpoints = []
        partial = review(sources, ["app.py"], [], CancelProvider(), batch_chars=600,
                         checkpoint=lambda r: checkpoints.append(deepcopy(r)))
        self.assertEqual(partial["stop_reason"], "cancelled")
        self.assertGreater(partial["coverage"]["chunks_reviewed"], 0)
        self.assertEqual(checkpoints[-1]["status"], "incomplete")
        resumed = review(sources, ["app.py"], [], AutomaticProvider(), batch_chars=600, previous=partial)
        self.assertEqual(resumed["status"], "complete")

    def test_cli_cancellation_returns_report_and_stops_remaining_sarif_groups(self):
        from test_sarif import document, location
        class CancelProvider(AutomaticProvider):
            def ask(self, *args):
                self.calls += 1
                raise RunStopped("cancelled", "Cancelled")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("safe()\n")
            sarif = root / "scan.sarif"
            sarif.write_text(json.dumps(document([{"ruleId": str(i), "locations": [location()]} for i in range(3)])))
            provider = CancelProvider()
            with patch("njordcup.cli.OpenAIProvider", return_value=provider), patch("sys.stdout", new_callable=io.StringIO) as out:
                code = main([tmp, "--model", "local", "--sarif", str(sarif), "--investigate-all"])
            self.assertEqual(code, 130)
            self.assertEqual(json.loads(out.getvalue())["sarif"]["total"], 3)
            self.assertEqual(provider.calls, 1)
            saved = json.loads((root / ".njordcup" / "memory.json").read_text())
            self.assertEqual(saved["reviews"][-1]["report"]["stop_reason"], "cancelled")
