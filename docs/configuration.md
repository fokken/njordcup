# Configuration

[Back to README](../README.md)

| Flag | Environment | Default |
| --- | --- | --- |
| `--base-url` | `REVIEW_BASE_URL` | `https://api.openai.com/v1` |
| `--model` | `REVIEW_MODEL` | Required for model operations |
| `--api-key-env` | `REVIEW_API_KEY_ENV` | `OPENAI_API_KEY` |
| `--output-mode` | `REVIEW_OUTPUT_MODE` | `json_schema` |

`--api-key-env` names the variable containing the credential, not the credential
itself. Local endpoints can run without a key. HTTP redirects are rejected. Only
the configured endpoint is contacted; there is no fallback to a cloud provider.
For servers without strict schemas, choose `--output-mode json_object` or `prompt`.
Every mode still validates model output locally. The adapter uses Chat Completions
with `max_tokens`; verify compatibility with your deployment.

Defaults: `--max-calls 20`, `--max-tokens 6000`, `--batch-chars 24000`,
`--context-chars 24000`, `--context-rounds 3`, `--max-input-chars 80000`.
The request cap includes messages and output-format schema and rejects oversized
requests before contacting the server. It is a character cap, not exact token
counting; set server limits using the model's tokenizer. Large repositories need
more calls or more resumptions, not a repository-sized context window.

## Response caching

`--cache /private/cache` enables response caching by endpoint, model and complete
request. Clear it when changing weights behind an unchanged model name. Cache
entries contain findings/source excerpts. Valid cache hits do not consume network
attempts; retry attempts do count toward `--max-calls`.

## Long-running execution

| Flag | Default | Meaning |
| --- | --- | --- |
| `--request-timeout` | `120` | Socket I/O timeout in seconds for each network attempt |
| `--max-retries` | `2` | Additional attempts for a transient failure; use `0` to disable |
| `--retry-base` | `1` | Initial exponential-backoff delay in seconds, with jitter |
| `--retry-max-delay` | `30` | Maximum delay, including server `Retry-After` values |
| `--max-seconds` | Unset | Cooperative deadline for the entire invocation |

```sh
python3 -m njordcup /path/to/repo --sarif results.sarif --investigate-all \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 --api-key-env OLLAMA_API_KEY \
  --max-calls 200 --request-timeout 180 --max-retries 3 --max-seconds 7200
```

Retries apply to HTTP 408, 429, 500, 502, 503 and 504, and interrupted connections
or timeouts. HTTP authentication/configuration errors, certificate failures, malformed
JSON, schema failures and truncated model output are not retried. A successful
HTTP response is not enough: model output still undergoes schema and evidence checks.
All network attempts share the same call budget. The `retries` usage counter records
additional attempts actually sent. Retry exhaustion, permanent HTTP errors and
call-budget exhaustion stop the run without moving on through the remaining areas.

SIGINT (Ctrl+C) and SIGTERM request cancellation. Cancellation interrupts retry
backoff and stops further model calls. An in-flight blocking read may finish or hit
its I/O timeout before cancellation takes effect. Deadline checks occur between
phases and response reads; this is not a hard process-kill timer. The server may
continue inference after a client timeout or cancellation. A retry may therefore
repeat server work; token usage from responses that were not received is unknown.

Reports include `stop_reason`. Exit codes are `130` for Ctrl+C, `143` for SIGTERM,
`124` for the time budget, and `2` for other incomplete/error stops. Re-run the same
command to resume saved checkpoints. Execution remains sequential.

## Provider references

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility)
- [vLLM compatibility](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)

## Related guides

- [Getting started](getting-started.md)
- [Resource planning and benchmarks](resource-planning.md)
