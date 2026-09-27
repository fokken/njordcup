# Getting started

[Back to README](../README.md)

Run from this checkout, or install with `pip install -e .` for the `njordcup` command.
Replace `YOUR_MODEL` with a model served by your endpoint.

See the [review flow description](architecture.md#flow-description) for how files
are grouped, reviewed, consolidated and summarized.

## Quick start by context size

Choose the row matching the context window **configured on your inference server**.
These are conservative starting budgets, not measured model-quality presets. Here,
16k means 16,384 tokens; use your server's exact limit if it differs. The flags tell
njordcup how to budget requests; they do not resize the server's context window.

| Server context | `--context-window` | `--max-tokens` | `--max-input-chars` | `--batch-chars` | `--context-chars` |
| --- | ---: | ---: | ---: | ---: | ---: |
| 16k | 16384 | 3072 | 11000 | 4000 | 2000 |
| 32k | 32768 | 4096 | 24000 | 10000 | 6000 |
| 64k | 65536 | 6000 | 48000 | 20000 | 10000 |
| 128k | 131072 | 8000 | 96000 | 32000 | 16000 |

`--max-tokens` reserves output tokens. `--max-input-chars` caps the serialized
messages, including instructions and metadata. `--batch-chars` controls target-code
batches; `--context-chars` limits additional reference code. These are ceilings,
not amounts the tool always sends. All rows retain the defaults of
`--bytes-per-token 1` and `--token-margin 1024`; the token estimate and character
limit both apply. Larger windows need not be filled, especially on slower hardware.

Run **one** of these commands, replacing the repository path, model and endpoint.
The examples use a local endpoint at port 11434; for a deployment at port 8000,
replace the base URL with `http://localhost:8000/v1`. Local endpoints without
authentication need no key. If yours requires one, set `OPENAI_API_KEY` or use
`--api-key-env` to name your credential variable.

```sh
# 16k context
python3 -m njordcup /path/to/repo --automatic --output-mode prompt \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --context-window 16384 --max-tokens 3072 --max-input-chars 11000 \
  --batch-chars 4000 --context-chars 2000

# 32k context
python3 -m njordcup /path/to/repo --automatic --output-mode prompt \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --context-window 32768 --max-tokens 4096 --max-input-chars 24000 \
  --batch-chars 10000 --context-chars 6000

# 64k context
python3 -m njordcup /path/to/repo --automatic --output-mode prompt \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --context-window 65536 --max-tokens 6000 --max-input-chars 48000 \
  --batch-chars 20000 --context-chars 10000

# 128k context
python3 -m njordcup /path/to/repo --automatic --output-mode prompt \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --context-window 131072 --max-tokens 8000 --max-input-chars 96000 \
  --batch-chars 32000 --context-chars 16000
```

These commands map the repository and review all eligible source components, saving
progress in `.njordcup/memory.json`. Re-run the same command to resume unfinished
work. Remove `--automatic` to choose areas interactively. Add `--log-file
/path/outside/repo/njordcup.log` to retain runtime logs. Calls are unlimited by
default; use `--max-calls` or `--max-seconds` for an optional run budget.
Add `--workers 2` to try concurrent file reviews within each area; see
[worker behavior and shared budgets](workers.md). The default is one worker.

Generate the saved audit's HTML report and progress summary without model calls:

```sh
python3 -m njordcup /path/to/repo --report
python3 -m njordcup /path/to/repo --summary
```

The HTML report is written to `.njordcup/memory.report.html`. A prose review can
contain potential vulnerabilities even when the structured findings count is zero.
If responses end with `finish_reason=length`, increase the output allowance while
reducing input budgets to leave room. Explicit input-overflow errors trigger bounded
recovery; an unfit single chunk remains incomplete. See [context budgeting](configuration.md#adaptive-context-budgeting)
and [troubleshooting](logging.md#diagnosing-reviewerror) for adjustments.

## Direct security audit

Use `--audit-only` to build the local index and component map, then immediately
review all selected files without a model-based architectural flyover or area menu:

```sh
python3 -m njordcup /path/to/repo --audit-only --workers 2 \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --context-window 32768 --max-tokens 4096 --max-input-chars 24000 \
  --batch-chars 10000 --context-chars 6000
```

Choose context budgets that match your server using the table above. You can also
replace `--automatic` with `--audit-only` in any of those commands. Source filters
(`--include`, `--exclude`, `--base`), output modes, workers, call/time budgets,
per-file consolidation, checkpoint resumption and reports work as usual. Re-run
the same command to resume, or add `--rerun` to repeat completed reviews.

| Mode | Architectural model calls | Security review |
| --- | --- | --- |
| Default interactive run | Flyover | Choose an area |
| `--automatic` | Flyover, reusing compatible completed pages | All pending source areas |
| `--audit-only` | None | All pending source areas |

Direct audits still use related source excerpts and any compatible architectural
or implementation context already saved. On a fresh audit, that architectural
context is absent. It skips the initial overview calls; it does not change the
security prompt or add a cross-file review pass. `--audit-only` cannot be combined
with other action selectors such as `--area`, `--flyover-only`, implementation
analysis or SARIF investigation.

Memory and summaries record `audit_mode: "direct"`. Unanalyzed architectural pages
remain pending in flyover coverage, but do not by themselves make the direct audit
incomplete. Source gaps and unfinished consolidation still do. Run `--automatic`
later to add the architectural flyover while retaining compatible completed source
reviews. Generate the HTML report with the usual `--report` command.

## Other workflows

For a standalone overview instead of an audit, run
`--quick-implementation-analysis --quick-max-files 50` with your model settings.
It selects representative files and automatically writes one collective HTML
report; see [quick implementation analysis](quick-implementation.md).

```sh
# Local index only: no credentials or model calls.
python3 -m njordcup /path/to/repo --index-only

# Flyover, then select an area in the interactive menu.
python3 -m njordcup /path/to/repo \
  --base-url http://localhost:11434/v1 --model YOUR_MODEL \
  --api-key-env OLLAMA_API_KEY

# Save/resume architectural analysis without starting a focused review.
python3 -m njordcup /path/to/repo --model YOUR_MODEL \
  --base-url http://localhost:8000/v1 --api-key-env VLLM_API_KEY --flyover-only

# Select an area from saved memory using the same provider/scope settings.
python3 -m njordcup /path/to/repo --model YOUR_MODEL \
  --base-url http://localhost:8000/v1 --api-key-env VLLM_API_KEY --area 2

# Offline snapshot summary; no model or credentials required.
python3 -m njordcup /path/to/repo --summary
```

Interactive reviews save after every completed batch and return to the area menu.
Non-interactive runs stop after the flyover unless `--area`, `--automatic`, `--audit-only`, or `--investigate-all`
explicitly authorizes investigation. `--dry-run` previews eligible paths without
model calls or writing the index.

## Review scope

`--base COMMIT` selects changed whole files, including staged, unstaged and untracked
files. Related source remains available for retrieval. Deleted files are not analyzed.
SARIF investigations explicitly include eligible reported locations even when those
files are outside the changed-file selection.

## Exit codes

Exit codes: `0` completed without findings, saved flyover/import, index/dry run or
successful summary; `1` completed with findings; `2` incomplete investigation/error.
Failed architectural page analysis also returns `2`, with saved progress and errors;
rerun `--flyover-only` to finish pending pages.
Cancellation uses `130` for Ctrl+C and `143` for SIGTERM; an exhausted `--max-seconds`
budget uses `124`. These stops retain completed checkpoints and report `stop_reason`.
For `--investigate-all`, any inconclusive result returns `2`. Read the JSON status:
a successful summary command does not mean the audit itself is complete.

## Related guides

- [Configuration](configuration.md)
- [Memory and audit summaries](memory.md)
- [Semgrep SARIF investigations](sarif.md)

## Automatic auditing

```sh
python3 -m njordcup /path/to/repo --automatic \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --max-calls 200 --max-seconds 7200
```

`--automatic` (alias `--auto`) maps every eligible target into a component area,
then reviews those areas without prompting. This includes small repositories where
an ordinary flyover might suggest only a subset of files. Existing include/exclude
filters and `--base` still define the target scope. Switching from a small suggested-area
map to a component map archives the old snapshot and starts the component reviews.

Each completed source batch is saved. Structured modes additionally verify findings. Newly encountered findings are announced immediately
after that checkpoint as `Potential issue saved:` messages on stderr; stdout remains
the final JSON result. Findings pass the existing model verification and exact-source
checks first. Duplicate path/line/CWE notifications are suppressed within an invocation.
A finding from a resumed partial attempt may be announced again.

The queue visits each unfinished source component once per invocation. It does not
loop indefinitely over inconclusive areas. Call budgets, retries, cancellation, and
time limits still apply, including calls spent on architectural mapping. Rerun the
same command to resume; completed areas are skipped unless `--rerun` is supplied.
Final output includes saved findings from all source components. In prompt mode,
it also includes raw narrative analysis and announces each saved response; issue
counts are not extracted from prose. Exit codes remain
`1` for completed audits with findings and `2` for incomplete work, with the usual
cancellation/time-limit codes.

This mode performs broad source reviews; scanner adjudication is a separate action
using `--sarif ... --investigate-all`. Saved scanner investigations remain available
in summaries and [HTML reports](reporting.md). Automatic mode does not execute code
or prove that it found every security issue.

The main CLI runs without an API call cap by default (`--max-calls 0`).
For an optional budget use `--max-calls N` or `--max-seconds N`; otherwise
automatic mode continues through its pending queue, subject to errors or cancellation.

Prompt-mode security reviews produce one analysis per file. Large files use bounded
parts followed by resumable model consolidation; see [per-file reporting](reporting.md#one-security-analysis-per-file).
