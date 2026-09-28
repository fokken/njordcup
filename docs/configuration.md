# Configuration

[Back to README](../README.md)

## Configuration file

Store persistent settings in `~/.config/njordcup/config.toml` (or
`$XDG_CONFIG_HOME/njordcup/config.toml` when `XDG_CONFIG_HOME` is absolute).
Njordcup never searches the audited repository for configuration. `--config PATH`
loads a different file **instead of** the user file; `--no-config` disables file
loading. Missing automatic configuration is fine; a missing explicit file is an error.

```toml
default_profile = "local-32k"

[settings]
output_mode = "prompt"
workers = 1
request_timeout = 120
max_retries = 2

[profiles.local-32k]
base_url = "http://localhost:11434/v1"
api_key_env = "OLLAMA_API_KEY"
model = "YOUR_MODEL"
context_window = 32768
max_tokens = 4096
max_input_chars = 24000
batch_chars = 10000
context_chars = 6000

[profiles.local-64k]
base_url = "http://localhost:11434/v1"
api_key_env = "OLLAMA_API_KEY"
model = "YOUR_MODEL"
context_window = 65536
max_tokens = 6000
max_input_chars = 48000
batch_chars = 20000
context_chars = 10000
```

```sh
njordcup /path/to/repo --audit-only
njordcup /path/to/repo --quick-implementation-analysis
njordcup /path/to/repo --quick-audit --quick-max-files 30
njordcup /path/to/repo --audit-only --profile local-64k --workers 2
njordcup /path/to/repo --config /path/to/models.toml --audit-only
```

Precedence, lowest to highest: **built-in defaults → existing `REVIEW_*`
environment defaults → `[settings]` → selected profile → explicit CLI flags**.
Profiles inherit `[settings]`, not other profiles. `--profile NAME` replaces
`default_profile`. Existing full CLI flags still work; `--help` shows common options
and `--help-all` includes advanced settings. Use full flag names, not abbreviations.

Allowed keys in `[settings]` and each profile use underscores:

| Category | Keys |
| --- | --- |
| Provider | `model`, `base_url`, `api_key_env`, `output_mode` |
| Execution | `workers`, `max_calls`, `max_seconds`, `request_timeout`, `max_retries`, `retry_base`, `retry_max_delay` |
| Context | `context_window`, `max_tokens`, `max_input_chars`, `batch_chars`, `context_chars`, `context_rounds`, `bytes_per_token`, `token_margin` |
| Index and selection | `index_mode`, `max_file_bytes`, `quick_max_files` |
| Storage and logs | `cache`, `log_file`, `trace_file` |

Numeric settings require TOML numbers. Unknown keys, invalid values and unknown
profiles fail before execution. Paths in configuration resolve relative to the
configuration file, with `~` expansion; CLI paths retain their usual working-directory
semantics. Credentials stay in the environment: configure the variable name via
`api_key_env`, never the secret itself. Modes, target filters, report outputs and
memory selection remain explicit CLI options. Verbosity stays on `--verbose`/`--quiet`.

Python 3.11+ reads TOML using the standard library. Installing njordcup on Python
3.10 installs `tomli`; when running directly from a checkout on 3.10, install it
with `python3 -m pip install tomli` to use configuration files.

## Environment defaults

| Flag | Environment | Default |
| --- | --- | --- |
| `--base-url` | `REVIEW_BASE_URL` | `https://api.openai.com/v1` |
| `--model` | `REVIEW_MODEL` | Required for model operations |
| `--api-key-env` | `REVIEW_API_KEY_ENV` | `OPENAI_API_KEY` |
| `--output-mode` | `REVIEW_OUTPUT_MODE` | `prompt` |

`--api-key-env` names the variable containing the credential, not the credential
itself. Local endpoints can run without a key. HTTP redirects are rejected. Only
the configured endpoint is contacted; there is no fallback to a cloud provider.
The adapter uses Chat Completions with `max_tokens`; verify compatibility with your deployment.

## Model output modes

All modes expect the server's normal JSON API envelope. They differ in how
`choices[0].message.content` is handled:

| Mode | Model content |
| --- | --- |
| `json_schema` | Requests a strict server-side schema, then parses and validates the model's JSON locally |
| `json_object` | Requests JSON syntax from the server, then parses and validates the required schema locally |
| `prompt` (default) | Requests prose/Markdown and saves the model's text verbatim; no JSON parsing or schema validation of the content |

The CLI defaults to `prompt`, accepting the response as-is. Even JSON-looking
text, Markdown fences and reasoning text are preserved rather than interpreted.
API envelope validation, request budgets, retries, caching and source snapshots still
apply. The JSON saved by njordcup is its own storage format, not a format imposed on
the model. Prompt mode sends no `response_format` or output JSON schema.

Free-form output is saved with its source scope and completion metadata, included in
summaries and HTML reports, and reused as bounded analysis context. Flyover areas are
mapped locally; languages, stack, severity and findings are not guessed from prose.
Focused reviews proactively include bounded related/scanner source context. They do
not use structured retrieval requests or the structured finding-verification pass.
Automatic mode announces each saved narrative instead of trying to extract individual
issues from it. Full narrative text can therefore appear in runtime logs in this mode.

A completed prompt-mode review means its source batches received nonempty, finished
responses; it does not imply that there were no issues. `findings` contains only
structured findings, and `requires_manual_review` identifies narrative review output.
Read `file_analysis` for per-file results and `narrative_analysis` for original responses. SARIF text is retained but automatic
scanner verdicts remain inconclusive; use a structured mode for machine-checked
adjudications. Truncated or otherwise unfinished text is also saved, marked incomplete,
and is not cached as a successful response.

Changing modes changes provider identity; regenerate the flyover rather than selecting
an area from incompatible saved memory. Use `--output-mode prompt` consistently for
analysis and resumed reviews. To continue an existing structured-mode audit, explicitly
pass `--output-mode json_schema` (or its original mode), or set `REVIEW_OUTPUT_MODE`.
The standalone evaluation harness and direct Python provider API retain their
structured defaults for compatibility.

Defaults: `--max-calls 0` (unlimited), `--max-tokens 6000`, `--batch-chars 24000`,
`--context-chars 24000`, `--context-rounds 3`, `--max-input-chars 80000`.
The character cap includes messages and output-format schema. Optional metadata
and flyover samples shrink to fit; review batches split when mandatory input is too
large. Target source is never silently truncated. Large repositories use more calls, not a repository-sized context window.

The main CLI has no call cap by default, so automated analysis can finish its queue.
Use `--max-calls N` for an optional positive limit; retries count toward it.
Use `--max-calls 0` to explicitly disable the cap. Per-request retry limits,
timeouts, cancellation and optional `--max-seconds` still apply. Unlimited calls
does not retry failing requests indefinitely. The evaluation harness retains its
separate default of 100 calls.

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
Explicit server context-overflow errors trigger bounded recovery automatically.
The provider lowers its input character cap by 25% per rejection for the rest of the
invocation, removes optional material, and retries only if the smaller request fits.
There are at most three recovery retries per provider request; every HTTP attempt
counts toward `--max-calls`. Recovery is separate from `--max-retries` for transient
network errors. Focused reviews split rejected batches, and report/file synthesis
repartitions saved text with a bounded number of restarts, reusing completed nodes.

Mandatory source and scanner claims are preserved. An unfit single chunk remains
incomplete; reduce `--batch-chars` or adjust the server/input/output budgets before
resuming. Recovery does not change `--max-tokens` or server configuration, and the
learned cap resets on a new invocation. Generic HTTP errors and GPU out-of-memory
errors do not trigger this recovery. Silent server-side input truncation cannot be
detected from a successful response. Set `--context-window` proactively whenever
possible. Logs and performance counters record recognized `context_overflows`.

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

`--index-mode auto` uses Python AST parsing, optional Tree-sitter grammars, and
best-effort lexical metadata for other files ([installation and behavior](indexing.md)). `--index-mode text` uses general text chunks and identifier
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
| `--workers` | `1` | Concurrent files within a source-review area; see [worker behavior](workers.md) |

```sh
python3 -m njordcup /path/to/repo --sarif results.sarif --investigate-all \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 --api-key-env OLLAMA_API_KEY \
  --max-calls 200 --request-timeout 180 --max-retries 3 --max-seconds 7200
```

Retries apply to HTTP 408, 429, 500, 502, 503 and 504, and interrupted connections
or timeouts. HTTP authentication/configuration errors, certificate failures, malformed
JSON, schema failures and truncated model output are not retried. A successful
HTTP response is not enough: prompt-mode content must be nonempty and finished;
structured modes additionally apply schema and evidence checks.
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
command to resume saved checkpoints. Execution is sequential by default; `--workers N`
enables concurrent file reviews with centrally coordinated checkpoints and shared limits.

## Provider references

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility)
- [vLLM compatibility](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)

## Related guides

- [Getting started](getting-started.md)
- [Resource planning and benchmarks](resource-planning.md)
- [Logging and troubleshooting](logging.md)
- [Prompt efficiency and performance](performance.md)
