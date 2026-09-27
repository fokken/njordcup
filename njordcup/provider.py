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
from .errors import ReviewError, RunStopped, ContextBudgetExceeded, ServerContextOverflow
from .runtime import RunControl
from .narrative import Narrative, instructions as narrative_instructions

import logging
import time
from uuid import uuid4
import base64
import threading

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


def validate(value, schema, path="$"):
    kind = schema["type"]
    types = {"object": dict, "array": list, "string": str, "integer": int}
    if type(value) is not types[kind]:
        raise ReviewError(f"Model response has an invalid field type at {path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ReviewError(f"Model response has an invalid enum value at {path}")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            missing = sorted(set(schema["properties"]) - set(value))
            extra = len(set(value) - set(schema["properties"]))
            raise ReviewError(f"Model response has missing or unexpected fields at {path}; "
                              f"missing: {', '.join(missing) or 'none'}; unexpected field count: {extra}")
        for key, item in value.items():
            validate(item, schema["properties"][key], f"{path}.{key}")
    elif kind == "array":
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")


class OpenAIProvider:
    def __init__(self, model, max_calls=0, cache=None, base_url="https://api.openai.com/v1",
                 api_key_env="OPENAI_API_KEY", output_mode="json_schema", max_tokens=6000, max_input_chars=80000,
                 request_timeout=120, max_retries=2, retry_base=1, retry_max_delay=30, control=None, on_retry=None,
                 context_window=None, bytes_per_token=1, token_margin=1024, trace_file=None):
        if type(max_calls) is not int or max_calls < 0:
            raise ValueError("max_calls must be a nonnegative integer; 0 means unlimited")
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
        self.http_seconds = 0.0
        self.responses_with_usage = self.responses_without_usage = 0
        self.performance = {}
        self._state_lock = threading.RLock()
        self._parent = None
        self._request_gate = None
        self.server_input_chars = None
        self.context_overflows = 0
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
        from .tracing import TraceLog
        self.trace = TraceLog(trace_file) if trace_file else None

    def fork(self, gate):
        """Private per-file counters, shared transport budget/control/trace/cache."""
        from copy import copy
        child = copy(self)
        child._parent = self
        child._request_gate = gate
        for name in ('calls', 'cache_hits', 'input_tokens', 'output_tokens', 'retries',
                     'http_seconds', 'responses_with_usage', 'responses_without_usage',
                     'context_overflows', 'budget_adjustments', 'max_request_chars', 'max_estimated_input_tokens'):
            setattr(child, name, 0)
        child.performance = {}
        return child

    def reserve_attempt(self):
        owner = self._parent or self
        with owner._state_lock:
            self.control.check()
            if self._request_gate:
                self._request_gate.check()
            if owner.max_calls and owner.calls >= owner.max_calls:
                raise RunStopped('call_budget', 'API call budget exhausted (including retry attempts)')
            owner.calls += 1
            if self._parent:
                self.calls += 1
            return owner.calls

    def trace_event(self, event, **fields):
        if self.trace:
            self.trace.write(event, **fields)

    def trace_response(self, request_id, raw, **fields):
        if not self.trace:
            return
        try:
            body = raw.decode('utf-8')
            encoding = 'utf-8'
        except UnicodeDecodeError:
            body = base64.b64encode(raw).decode('ascii')
            encoding = 'base64'
        self.trace_event('response', request_id=request_id, body=body, body_encoding=encoding, **fields)

    def request_body(self, instructions, payload, schema):
        system = (narrative_instructions(schema) if self.output_mode == "prompt" else
                  instructions + "\nReturn JSON matching this schema: " + json.dumps(schema))
        body = {"model": self.model,
                "messages": [{"role": "system", "content": system},
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
        if self._parent:
            with self._parent._state_lock:
                caps = [c for c in (self.server_input_chars, self._parent.server_input_chars) if c is not None]
                self.server_input_chars = min(caps) if caps else None
        chars, tokens = self.request_size(self.request_body(instructions, payload, schema))
        return chars <= min(self.max_input_chars, self.server_input_chars or self.max_input_chars) and (self.context_window is None or
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
        from .performance import COUNTERS
        properties = schema.get('properties', {})
        phase = ('quick_selection' if 'quick_selection' in properties else
                 'quick_implementation' if 'quick_implementation' in properties else
                 'file_consolidation' if 'file_synthesis' in properties else
                 'report_synthesis' if 'report_synthesis' in properties or 'quick_synthesis' in properties else
                 'implementation' if 'languages' in properties else
                 'flyover' if 'areas' in properties else 'review')
        names = {'attempts': 'calls', 'cache_hits': 'cache_hits', 'retries': 'retries',
                 'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens', 'http_seconds': 'http_seconds',
                 'responses_with_usage': 'responses_with_usage', 'responses_without_usage': 'responses_without_usage',
                 'context_overflows': 'context_overflows'}
        before = {k: getattr(self, attr) for k, attr in names.items()}
        adjustments_before = self.budget_adjustments
        started, failed = time.monotonic(), False
        try:
            if self._request_gate:
                self._request_gate.check()
            # Retry only a smaller request. This limit is separate from transient
            # transport retries; every network attempt still counts toward max_calls.
            for recovery in range(4):
                try:
                    return self._ask(instructions, payload, schema)
                except ServerContextOverflow:
                    self.context_overflows += 1
                    chars, _ = self.request_size(self.request_body(instructions, payload, schema))
                    self.server_input_chars = min(self.server_input_chars or chars, max(1, int(chars * 0.75)))
                    if self._parent:
                        with self._parent._state_lock:
                            self._parent.server_input_chars = min(self._parent.server_input_chars or self.server_input_chars,
                                                                 self.server_input_chars)
                    log.warning('Server context overflow; reducing invocation input cap to %d characters (recovery %d/3)',
                                self.server_input_chars, min(recovery + 1, 3))
                    if recovery == 3:
                        raise ContextBudgetExceeded('Server context recovery limit reached; reduce input/output budgets') from None
                    # fit_payload trims only optional context/sampled flyover material;
                    # mandatory input failures return to the review batch splitter.
                    self.fit_payload(instructions, payload, schema)
        except BaseException as exc:
            failed = True
            if isinstance(exc, RunStopped) and self._request_gate:
                self._request_gate.stop(exc.reason)
            raise
        finally:
            delta = {k: 0 for k in COUNTERS}
            delta['requests'] = 1
            delta['failures'] = int(failed)
            delta['elapsed_seconds'] = time.monotonic() - started
            for key, attr in names.items():
                delta[key] = getattr(self, attr) - before[key]
            row = self.performance.setdefault(phase, {k: 0 for k in COUNTERS})
            for key in COUNTERS:
                row[key] += delta[key]
            if self._parent:
                with self._parent._state_lock:
                    total = self._parent.performance.setdefault(phase, {k: 0 for k in COUNTERS})
                    for key in COUNTERS:
                        total[key] += delta[key]
                    for key, attr in names.items():
                        if attr != 'calls':  # Attempts are reserved before sending.
                            setattr(self._parent, attr, getattr(self._parent, attr) + delta[key])
                    self._parent.budget_adjustments += self.budget_adjustments - adjustments_before
                    for attr in ('max_request_chars', 'max_estimated_input_tokens'):
                        setattr(self._parent, attr, max(getattr(self._parent, attr), getattr(self, attr)))

    def _ask(self, instructions, payload, schema):
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
                if self.output_mode == 'prompt':
                    if not isinstance(value, dict) or not isinstance(value.get('text'), str) or value.get('finish_reason') != 'stop':
                        raise ReviewError('Invalid narrative cache entry')
                    value = Narrative(value['text'])
                else:
                    validate(value, schema)
            except (OSError, ValueError, ReviewError):
                pass
            else:
                self.trace_event('cache_hit', request=body, response=value)
                self.cache_hits += 1
                log.info("Using cached model response")
                return value
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
        if "prompt_tokens" in usage and "completion_tokens" in usage:
            self.responses_with_usage += 1
        else:
            self.responses_without_usage += 1
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)
        log.debug("Provider reported token usage: %d input / %d output",
                  usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        choices = result.get("choices") or []
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ReviewError("Provider returned no usable completion choice; check the endpoint's Chat Completions response format")
        finish = choices[0].get("finish_reason")
        if self.output_mode == 'prompt':
            message = choices[0].get('message', {})
            if not isinstance(message, dict) or not isinstance(message.get('content'), str):
                raise ReviewError('Provider message content must be text for prompt mode')
            value = Narrative(message['content'], 'refusal' if message.get('refusal') else finish)
            if cached and value.complete:
                self.save_cache(cached, value.record())
            return value
        if finish == "length":
            raise ReviewError("Model response truncated (finish_reason=length); increase --max-tokens if the server context allows, "
                              "or reduce --batch-chars/--context-chars. Review remains incomplete")
        if finish == "content_filter":
            raise ReviewError("Provider filtered the response (finish_reason=content_filter); review remains incomplete")
        if finish != "stop":
            raise ReviewError("Provider returned an unsupported or missing finish_reason; expected stop. Review remains incomplete")
        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            raise ReviewError("Provider returned an invalid message")
        if message.get("refusal"):
            raise ReviewError("Model declined the review")
        try:
            value = json.loads(message.get("content") or "")
        except json.JSONDecodeError as exc:
            raise ReviewError(f"Model did not return valid JSON at line {exc.lineno}, column {exc.colno}; "
                              "expected a JSON object without Markdown fences or surrounding commentary. "
                              "Check the model's structured-output support and --output-mode") from exc
        except TypeError as exc:
            raise ReviewError("Provider message content is not JSON text; check the endpoint's Chat Completions response format") from exc
        validate(value, schema)
        log.debug("Model output validated; reported usage: %d input / %d output tokens", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        if cached:
            self.save_cache(cached, value)
        return value

    def save_cache(self, cached, value):
        self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.cache, delete=False) as handle:
            json.dump(value, handle)
            temporary = handle.name
        os.replace(temporary, cached)

    def _request(self, request):
        for attempt in range(self.max_retries + 1):
            call_number = self.reserve_attempt()
            if attempt:
                self.retries += 1
            retry_after = None
            started = time.monotonic()
            request_id = uuid4().hex
            if self.trace:
                self.trace_event('request', request_id=request_id, call=call_number, attempt=attempt + 1,
                                 body=json.loads(request.data))
            log.info("Model request %d/%s started (attempt %d, I/O timeout %.1fs)", call_number, self.max_calls or "unlimited", attempt + 1, self.request_timeout)
            chunks = []
            try:
                with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.control.timeout(self.request_timeout)) as response:
                    chunks = []
                    while True:
                        self.control.check()
                        chunk = response.read1(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    self.trace_response(request_id, raw, status=getattr(response, 'status', 200),
                                        elapsed_seconds=time.monotonic() - started, complete=True)
                    result = json.loads(raw)
                self.control.check()
                log.info("Model request %d received in %.1fs", call_number, time.monotonic() - started)
                return result
            except urllib.error.HTTPError as exc:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                code = exc.code
                error_chunks, complete = [], True
                retained = 0
                try:
                    # Inspect a bounded prefix even when tracing is disabled. Error
                    # content is never echoed into normal logs or exception messages.
                    try:
                        while self.trace or retained < 65536:
                            self.control.check()
                            chunk = exc.read1(65536 if self.trace else 65536 - retained)
                            if not chunk:
                                break
                            error_chunks.append(chunk)
                            retained += len(chunk)
                        else:
                            complete = False
                    except (OSError, http.client.HTTPException):
                        complete = False
                    raw_error = b''.join(error_chunks)
                    self.trace_response(request_id, raw_error, status=code,
                                        elapsed_seconds=time.monotonic() - started, complete=complete)
                finally:
                    exc.close()
                from .overflow import is_context_overflow
                if is_context_overflow(code, raw_error):
                    raise ServerContextOverflow('Provider rejected the request: context length exceeded') from None
                if code not in RETRYABLE_HTTP:
                    raise RunStopped("provider_error", f"Provider HTTP {code}; check endpoint, credentials, model and output mode") from None
                failure = f"Provider HTTP {code}"
            except (ssl.SSLCertVerificationError, ssl.CertificateError):
                self.trace_event('transport_error', request_id=request_id, reason='TLS certificate verification failed')
                raise RunStopped("provider_error", "Provider TLS certificate verification failed") from None
            except urllib.error.URLError as exc:
                self.trace_event('transport_error', request_id=request_id, reason='Connection failed')
                if isinstance(exc.reason, ssl.SSLCertVerificationError):
                    raise RunStopped("provider_error", "Provider TLS certificate verification failed") from None
                failure = "Provider connection failed"
            except (TimeoutError, ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected):
                self.trace_response(request_id, b''.join(chunks), status=None,
                                    elapsed_seconds=time.monotonic() - started, complete=False)
                self.trace_event('transport_error', request_id=request_id, reason='Connection interrupted or timed out')
                failure = "Provider connection timed out or was interrupted"
            except (ValueError, UnicodeError):
                raise ReviewError("Provider returned invalid JSON; response was not retried") from None
            except RunStopped as exc:
                self.trace_event('stopped', request_id=request_id, reason=exc.reason)
                raise
            finally:
                self.http_seconds += time.monotonic() - started
            self.control.check()
            if attempt == self.max_retries:
                raise RunStopped("retry_exhausted", failure + "; retry limit exhausted")
            if self.max_calls and self.calls >= self.max_calls:
                raise RunStopped("call_budget", "API call budget exhausted (including retry attempts)")
            delay = retry_delay(attempt, retry_after, self.retry_base, self.retry_max_delay)
            if self.on_retry:
                self.on_retry({"retry": attempt + 1, "delay_seconds": delay, "reason": failure})
            self.control.wait(delay)
