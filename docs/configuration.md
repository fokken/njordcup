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
Removed reference chunks release their context allowance and may be requested again
in later rounds; the saved omission limitation remains visible for that attempt.
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

## Execution logging

Progress logs go to **stderr**, with local timestamps and severity levels. Standard
output remains JSON for audit commands. By default, logs describe discovery/indexing,
saved-analysis reuse, mapping pages, review queues and batches, model stages, network
request start/finish times, and completed areas. A request-start line remains visible
while the server is generating a response; the finish line reports elapsed time.

- `--verbose` (or `-v`) adds request character/token estimates, budget reductions,
  reported token usage, retrieval rounds, and saved checkpoint details.
- `--quiet` suppresses progress/debug logs, while keeping warnings, errors, prompts,
  and potential-issue notifications.

```sh
python3 -m njordcup /path/to/repo --automatic --model YOUR_MODEL \
  --base-url http://localhost:11434/v1 --verbose \
  > audit.json 2> audit.log
```

Progress logs do not dump source, prompts, raw model responses, API keys, or endpoint
URLs. They can include component/area names and timing/usage metadata. Existing
finding notifications still include finding titles, paths and CWEs. Keep redirected
logs outside the audited source tree, or exclude their path from discovery. Logging
is configured for each CLI invocation and does not change the application's root logger.

### Diagnosing `ReviewError`

Warnings include the error reason, not just the exception name. JSON reports and
saved incomplete attempts also retain error details. Common distinctions:

- `finish_reason=length`: the provider truncated its completion. Increase
  `--max-tokens` only within the configured context window, or reduce source/context
  budgets. An incomplete response is not accepted as a successful review.
- Invalid JSON: the model returned text that could not be parsed as the required
  JSON response, possibly with Markdown or commentary. Check structured-output
  support and the configured `--output-mode`.
- Schema mismatch: the error identifies the expected field path/type or missing
  fields. Raw response values and unexpected field names are not printed.
- No usable choice or unsupported finish reason: check the endpoint's Chat
  Completions response format.

When reporting a failure, include the command (without credentials), endpoint type,
and the full warning reason. Completed checkpoints remain reusable; a failed first
batch can legitimately leave no findings while the attempt remains incomplete.

### Full request/response tracing

`--trace-file PATH` enables an additional, opt-in JSONL trace independently of
`--verbose` or `--quiet`:

```sh
python3 -m njordcup /path/to/repo --automatic --model YOUR_MODEL \
  --base-url http://localhost:11434/v1 --verbose \
  --trace-file /private/njordcup-requests.jsonl
```

Each HTTP attempt records its full JSON request body and received response body,
including error responses and malformed JSON. Request/response pairs share a
`request_id`; entries include UTC timestamps, session IDs, attempt numbers, response
status and elapsed time. Responses are recorded before model-output validation.
Non-UTF-8 response bytes use base64 with `body_encoding: "base64"`. Connection
failures are recorded as transport errors; interrupted responses may be partial.
A terminated process can leave a request without a response entry.

Cache hits have their own `cache_hit` event containing the request and validated
cached output; no HTTP exchange occurred, and the original response envelope is not
available from the cache. Tracing does not record requests rejected before sending
by local input budgets, missing credentials, or exhausted call budgets.

Trace files contain prompts, source excerpts, and model output, including any secrets
present in that text. HTTP headers (including Authorization) and endpoint URLs are
not recorded. Files are created with private `0600` permissions, existing trace files
are appended with a new session ID, and unrelated existing files/symlinks/hard links
are rejected. A trace-writing failure stops that review rather than silently losing
trace entries. There is no automatic rotation or retention limit.

The configured trace path is excluded from source discovery and cannot collide with
memory, index, reports, output, or SARIF input. Keep passing the same `--trace-file`
when resuming if it is inside the source tree, or exclude it explicitly. Offline
commands do not create traces because they make no model requests. Standard progress
logs and JSON output continue to work as before.

### Runtime text logfile

Use `--log-file PATH` to append runtime output to a regular text logfile while
continuing to show it on stderr:

```sh
python3 -m njordcup /path/to/repo --automatic --model YOUR_MODEL \
  --base-url http://localhost:11434/v1 --verbose \
  --log-file /private/njordcup.log \
  --trace-file /private/njordcup-requests.jsonl
```

The text logfile includes progress, warnings, errors, interactive prompts and finding
notifications. `--verbose` and `--quiet` apply to both the terminal and logfile.
Stdout JSON results and report-generation messages remain on stdout; use `--output`
for saved results. The separate `--trace-file` records full model request/response
bodies and is optional.

Logfiles are opened in append mode with private `0600` permissions and flushed after
each write. Parent directories are created as needed. Symlinks, hard links and
non-regular files are rejected. The path must differ from audit artifacts, output,
SARIF input and the trace file. The configured logfile is excluded from discovery;
keep the option when resuming or explicitly exclude the file if it is in the repository.
There is no automatic log rotation. Command-line parsing and logfile-setup errors
occur before logging to the file starts and are shown on stderr.
