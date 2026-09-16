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
The character cap includes messages and output-format schema. Optional metadata
and flyover samples shrink to fit; review batches split when mandatory input is too
large. Target source is never silently truncated. Large repositories need more
calls or more resumptions, not a repository-sized context window.

## Adaptive context budgeting

Set `--context-window` to the context size configured on your server. It includes
both input and output; njordcup reserves `--max-tokens` for output and
`--token-margin` (default 1024) for server formatting overhead.

```sh
python3 -m njordcup /path/to/repo --model YOUR_MODEL \
  --base-url http://localhost:11434/v1 --context-window 32768 --max-tokens 4000
```

Input tokens are **estimated**, not counted with the model's tokenizer. The default
`--bytes-per-token 1` budgets one token per serialized UTF-8 byte, conservatively
including JSON escaping and schemas. A larger divisor allows more input but risks
underestimating tokens; only tune it against measurements for your deployed model.
The margin is also an estimate of server overhead, not a guarantee. njordcup does
not discover the server limit or change its configuration. Without `--context-window`,
only the character cap applies.

Before a request, optional catalogs, architectural memory and previous candidate
hints can be removed. Flyovers reduce samples and record coverage changes. Focused
reviews can omit reference chunks only with a saved limitation that keeps the review
incomplete; evidence validation uses only source retained in the final request.
If mandatory input still cannot fit, batches split. A single unfit chunk remains
unreviewed with an error; lower `--batch-chars` to rebuild smaller chunks or raise
the input budget. Candidates, scanner claims and target code are never trimmed.
Server context errors are not automatically retried with smaller requests.

Focused reports save `budget_notes`, `batch_splits`, and `request_budget` settings
and invocation-wide estimate/adjustment counters. Budget changes invalidate reuse
of chunk checkpoints when a review is invoked again. Use `--rerun` to revisit
already completed SARIF groups in all-results mode.

## Source selection and indexing

All eligible UTF-8 text files are included by default, regardless of language or
extension. Use repeated repository-relative glob patterns to narrow the scope:

```sh
python3 -m njordcup /path/to/repo --index-only --index-mode text \
  --include '*.swift' --include '*.ex' --include 'scripts/*' --exclude '*generated*'
```

`--include` patterns are combined as alternatives. `--exclude` takes precedence;
includes do not override binary, credential, symlink, size or Git-ignore protections.
These filters also restrict the source available for context retrieval and SARIF
location resolution, so include relevant configuration and shared modules too.
Globs match complete repository-relative paths using Python's `fnmatch`; `*` can
match directory separators. Quote patterns so your shell does not expand them.

`--index-mode auto` preserves optional Python AST parsing and best-effort lexical
metadata for other files. `--index-mode text` uses general text chunks and identifier
search without language-specific symbol parsing. Switching modes rebuilds metadata
and requires a new flyover before reusing an area selection.

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
