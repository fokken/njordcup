# Logging and troubleshooting

[Back to README](../README.md) · [Configuration](configuration.md)

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

Routine progress messages do not dump source, prompts, raw model responses, API keys,
or endpoint URLs. Automatic-mode narrative notifications do include model text,
which can quote source. Treat runtime logfiles as potentially sensitive. They can include component/area names and timing/usage metadata. Existing
finding notifications still include finding titles, paths and CWEs. Keep redirected
logs outside the audited source tree, or exclude their path from discovery. Logging
is configured for each CLI invocation and does not change the application's root logger.

### Diagnosing `ReviewError`

Warnings include the error reason, not just the exception name. The JSON/schema
checks below apply to structured modes; prompt mode saves content as text. JSON reports and
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

Cache hits have their own `cache_hit` event containing the request and cached
output (structured data or prompt-mode text); no HTTP exchange occurred, and the original response envelope is not
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


See [performance measurements](performance.md#timing-and-throughput) for phase statistics.
