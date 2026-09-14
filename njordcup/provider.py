"""OpenAI-compatible Chat Completions adapter with bounded calls and caching."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class ReviewError(Exception):
    pass


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
                 api_key_env="OPENAI_API_KEY", output_mode="json_schema", max_tokens=6000, max_input_chars=80000):
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

    def ask(self, instructions, payload, schema):
        body = {"model": self.model,
                "messages": [{"role": "system", "content": instructions + "\nReturn JSON matching this schema: " + json.dumps(schema)},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=True)}],
                "max_tokens": self.max_tokens}
        if self.output_mode == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "njordcup", "strict": True, "schema": schema}}
        elif self.output_mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        input_chars = sum(len(m["content"]) for m in body["messages"]) + len(json.dumps(body.get("response_format", {})))
        self.max_request_chars = max(self.max_request_chars, input_chars)
        if input_chars > self.max_input_chars:
            raise ReviewError(f"Request input exceeds {self.max_input_chars} character budget; lower source/context budgets or raise --max-input-chars")
        encoded = json.dumps(body, sort_keys=True).encode()
        key = hashlib.sha256(self.base_url.encode() + b"\0" + encoded).hexdigest()
        cached = self.cache / (key + ".json") if self.cache else None
        if cached and cached.is_file():
            try:
                value = json.loads(cached.read_text())
                validate(value, schema)
                self.cache_hits += 1
                return value
            except (OSError, ValueError, ReviewError):
                pass
        if self.calls >= self.max_calls:
            raise ReviewError("API call budget exhausted")
        api_key = os.environ.get(self.api_key_env)
        if not api_key and urlsplit(self.base_url).hostname == "api.openai.com":
            raise ReviewError(f"Set {self.api_key_env} to run a live review")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        request = urllib.request.Request(self.base_url + "/chat/completions", data=encoded, headers=headers)
        self.calls += 1
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=120) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ReviewError(f"Provider HTTP {exc.code}; check endpoint, credentials, model and output mode") from None
        except (OSError, ValueError) as exc:
            raise ReviewError("Provider request failed or returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ReviewError("Provider returned an invalid response envelope")
        usage = result.get("usage") or {}
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)
        choices = result.get("choices") or []
        if not choices or choices[0].get("finish_reason") != "stop":
            raise ReviewError("Model response incomplete; review remains incomplete")
        message = choices[0].get("message", {})
        if message.get("refusal"):
            raise ReviewError("Model declined the review")
        try:
            value = json.loads(message.get("content") or "")
        except (ValueError, TypeError) as exc:
            raise ReviewError("Model did not return valid structured output") from exc
        validate(value, schema)
        if cached:
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.cache, delete=False) as handle:
                json.dump(value, handle)
                temporary = handle.name
            os.replace(temporary, cached)
        return value
