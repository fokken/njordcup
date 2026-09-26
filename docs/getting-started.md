# Getting started

[Back to README](../README.md)

Run from this checkout, or install with `pip install -e .` for the `njordcup` command.
Replace `YOUR_MODEL` with a model served by your endpoint.

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
Non-interactive runs stop after the flyover unless `--area`, `--automatic`, or `--investigate-all`
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

Each verified batch is saved. Newly encountered findings are announced immediately
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
