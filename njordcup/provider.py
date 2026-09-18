"""OpenAI-compatible Chat Completions adapter with bounded calls and caching."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import http.client
import random
import ssl
import math
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from .errors import ReviewError, RunStopped, ContextBudgetExceeded
from .runtime import RunControl

import logging
import time

log = logging.getLogger(__name__)


RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}


def retry_delay(attempt, retry_after, base, maximum):
    delay = min(maximum, base * (2 ** min(attempt, 20)))
    delay = random.uniform(delay / 2, delay)
    if retry_after:
        try:
            requested = float(retry_after)
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(retry_after)
                requested = (date - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                requested = 0
        if math.isfinite(requested):
            delay = max(delay, requested)
    return max(0, min(maximum, delay))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReviewError("Provider redirected the request; configure its final base URL explicitly")


def validate(value, schema):
    kind = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if type(value) is not types[kind]:
        raise ReviewError("Model response has an invalid field type")
    if "enum" in schema and value not in schema["enum"]:
        raise ReviewError("Model response has an invalid enum value")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ReviewError("Model response has missing or unexpected fields")
        for key, item in value.items():
            validate(item, schema["properties"][key])
    elif kind == "array":
        for item in value:
            validate(item, schema["items"])


class OpenAIProvider:
    def __init__(self, model, max_calls=20, cache=None, base_url="https://api.openai.com/v1",
                 api_key_env="OPENAI_API_KEY", output_mode="json_schema", max_tokens=6000, max_input_chars=80000,
                 request_timeout=120, max_retries=2, retry_base=1, retry_max_delay=30, control=None, on_retry=None,
                 context_window=None, bytes_per_token=1, token_margin=1024):
        self.model, self.max_calls = model, max_calls
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query or fragment")
        self.base_url, self.api_key_env = base_url.rstrip("/"), api_key_env
        if output_mode not in {"json_schema", "json_object", "prompt"}:
            raise ValueError("Invalid output mode")
        self.output_mode, self.max_tokens = output_mode, max_tokens
        self.max_input_chars, self.max_request_chars = max_input_chars, 0
        self.cache = Path(cache) if cache else None
        self.calls = self.cache_hits = self.input_tokens = self.output_tokens = 0
        self.retries = 0
        if request_timeout <= 0 or max_retries < 0 or retry_base <= 0 or retry_max_delay <= 0:
            raise ValueError("Timeouts/delays must be positive and retries nonnegative")
        self.request_timeout, self.max_retries = request_timeout, max_retries
        self.retry_base, self.retry_max_delay = retry_base, retry_max_delay
        self.control, self.on_retry = control or RunControl(), on_retry
        if (max_tokens <= 0 or max_input_chars <= 0 or not math.isfinite(bytes_per_token)
                or bytes_per_token <= 0 or token_margin < 0
                or (context_window is not None and context_window <= max_tokens + token_margin)):
            raise ValueError("Context window must exceed output reservation plus margin; budgets must be positive")
        self.context_window, self.bytes_per_token, self.token_margin = context_window, bytes_per_token, token_margin
        self.max_estimated_input_tokens = self.budget_adjustments = 0

    def request_body(self, instructions, payload, schema):
        body = {"model": self.model,
                "messages": [{"role": "system", "content": instructions + "\nReturn JSON matching this schema: " + json.dumps(schema)},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=True)}],
                "max_tokens": self.max_tokens}
        if self.output_mode == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "njordcup", "strict": True, "schema": schema}}
        elif self.output_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    def request_size(self, body):
        text = "".join(m["content"] for m in body["messages"]) + json.dumps(body.get("response_format", {}))
        return len(text), math.ceil(len(text.encode("utf-8")) / self.bytes_per_token)

    def fits(self, instructions, payload, schema):
        chars, tokens = self.request_size(self.request_body(instructions, payload, schema))
        return chars <= self.max_input_chars and (self.context_window is None or
               tokens + self.max_tokens + self.token_margin <= self.context_window)

    def fit_payload(self, instructions, payload, schema):
        """Trim optional context in place so callers validate only transmitted evidence."""
        from .budget import shrink_payload
        before = self.budget_adjustments
        while not self.fits(instructions, payload, schema):
            self.control.check()
            if not shrink_payload(payload):
                raise ContextBudgetExceeded(
                    "Mandatory request exceeds context window or character budget; "
                    "reduce --batch-chars/--max-tokens or increase the configured input limits")
            self.budget_adjustments += 1
        if self.budget_adjustments != before:
            log.debug("Request budget adjusted: %d reductions", self.budget_adjustments - before)

    def ask(self, instructions, payload, schema):
        self.control.check()
        self.fit_payload(instructions, payload, schema)
        body = self.request_body(instructions, payload, schema)
        input_chars, estimated_tokens = self.request_size(body)
        log.debug("Request budget: %d input characters, %d estimated input tokens, %d output tokens reserved", input_chars, estimated_tokens, self.max_tokens)
        self.max_request_chars = max(self.max_request_chars, input_chars)
        self.max_estimated_input_tokens = max(self.max_estimated_input_tokens, estimated_tokens)
        encoded = json.dumps(body, sort_keys=True).encode()
        key = hashlib.sha256(self.base_url.encode() + b"\0" + encoded).hexdigest()
        cached = self.cache / (key + ".json") if self.cache else None
        if cached and cached.is_file():
            try:
                value = json.loads(cached.read_text())
                validate(value, schema)
                self.cache_hits += 1
                log.info("Using cached model response")
                return value
            except (OSError, ValueError, ReviewError):
                pass
        api_key = os.environ.get(self.api_key_env)
        if not api_key and urlsplit(self.base_url).hostname == "api.openai.com":
            raise ReviewError(f"Set {self.api_key_env} to run a live review")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        request = urllib.request.Request(self.base_url + "/chat/completions", data=encoded, headers=headers)
        result = self._request(request)
        if not isinstance(result, dict):
            raise ReviewError("Provider returned an invalid response envelope")
        usage = result.get("usage") or {}
        if not isinstance(usage, dict):
            raise ReviewError("Provider returned invalid usage metadata")
        if any(type(usage.get(k, 0)) is not int or usage.get(k, 0) < 0 for k in ("prompt_tokens", "completion_tokens")):
            raise ReviewError("Provider returned invalid token counters")
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)
        choices = result.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or choices[0].get("finish_reason") != "stop":
            raise ReviewError("Model response incomplete; review remains incomplete")
        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            raise ReviewError("Provider returned an invalid message")
        if message.get("refusal"):
            raise ReviewError("Model declined the review")
        try:
            value = json.loads(message.get("content") or "")
        except (ValueError, TypeError) as exc:
            raise ReviewError("Model did not return valid structured output") from exc
        validate(value, schema)
        log.debug("Model output validated; reported usage: %d input / %d output tokens", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        if cached:
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.cache, delete=False) as handle:
                json.dump(value, handle)
                temporary = handle.name
            os.replace(temporary, cached)
        return value

    def _request(self, request):
        for attempt in range(self.max_retries + 1):
            self.control.check()
            if self.calls >= self.max_calls:
                raise RunStopped("call_budget", "API call budget exhausted (including retry attempts)")
            self.calls += 1
            if attempt:
                self.retries += 1
            retry_after = None
            started = time.monotonic()
            log.info("Model request %d/%d started (attempt %d, I/O timeout %.1fs)", self.calls, self.max_calls, attempt + 1, self.request_timeout)
            try:
                with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.control.timeout(self.request_timeout)) as response:
                    chunks = []
                    while True:
                        self.control.check()
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    result = json.loads(b"".join(chunks))
                self.control.check()
                log.info("Model request %d received in %.1fs", self.calls, time.monotonic() - started)
                return result
            except urllib.error.HTTPError as exc:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                code = exc.code
                exc.close()
                if code not in RETRYABLE_HTTP:
                    raise RunStopped("provider_error", f"Provider HTTP {code}; check endpoint, credentials, model and output mode") from None
                failure = f"Provider HTTP {code}"
            except (ssl.SSLCertVerificationError, ssl.CertificateError):
                raise RunStopped("provider_error", "Provider TLS certificate verification failed") from None
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, ssl.SSLCertVerificationError):
                    raise RunStopped("provider_error", "Provider TLS certificate verification failed") from None
                failure = "Provider connection failed"
            except (TimeoutError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected):
                failure = "Provider connection timed out or was interrupted"
            except (ValueError, UnicodeError):
                raise ReviewError("Provider returned invalid JSON; response was not retried") from None
            self.control.check()
            if attempt == self.max_retries:
                raise RunStopped("retry_exhausted", failure + "; retry limit exhausted")
            if self.calls >= self.max_calls:
                raise RunStopped("call_budget", "API call budget exhausted (including retry attempts)")
            delay = retry_delay(attempt, retry_after, self.retry_base, self.retry_max_delay)
            if self.on_retry:
                self.on_retry({"retry": attempt + 1, "delay_seconds": delay, "reason": failure})
            self.control.wait(delay)
