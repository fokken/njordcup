# HTML audit reports

[Back to README](../README.md)

For a separate implementation/functionality report, use `--implementation-report`
([implementation analysis guide](implementation-analysis.md#html-implementation-report)).

Generate a self-contained report from saved audit memory, without model calls:

```sh
python3 -m njordcup /path/to/repo --report
python3 -m njordcup /path/to/repo --report --output audit.html
```

The default is `.njordcup/memory.report.html`, alongside memory. Custom `--memory`
paths use the same stem with `.report.html`. Open the file in a browser; it also
includes a print layout. No scripts, external fonts, or network resources are needed.
Source excerpts, filenames, and model text are escaped as plain HTML text. Reports
are replaced atomically and created with private file permissions.

The report includes severity counts, current findings with evidence, attack scenarios
and remediation, area coverage, scanner dispositions, and outstanding limitations.
Findings are deduplicated across areas using path, line, and CWE. As with `--summary`,
current means the latest saved attempt per area in the active snapshot; archived
snapshots and superseded attempts are excluded. The filesystem is not rescanned.

Generating a report returns `0` even when the saved audit has findings or unfinished
work. The report displays that audit status separately. `--report` cannot be combined
with audit actions, and output cannot overwrite memory or its index. The default
report path is excluded from future source discovery; exclude custom report paths
inside the repository yourself, for example `--exclude 'reports/*'`.

See [saved coverage](memory.md#saved-coverage) for the difference between processing
coverage and security assurance, and [automatic auditing](getting-started.md#automatic-auditing)
for unattended investigation.

Prompt-mode output appears in separate **Security analysis** and **Architectural
analysis** sections, preserving the full text as escaped preformatted content.
An empty structured-findings list is not a claim that the narrative found no issues.
Implementation HTML likewise displays raw page descriptions, including unfinished ones.

## Optional AI executive summary

Add `--synthesize` to either report command to summarize the saved analyses:

```sh
python3 -m njordcup /path/to/repo --report --synthesize \
  --model my-model --base-url http://localhost:11434/v1
python3 -m njordcup /path/to/repo --implementation-report --synthesize \
  --model my-model --base-url http://localhost:11434/v1
```

The security summary connects observations, prioritizes potential issues and describes
uncertainty and coverage gaps. The implementation summary describes architecture,
functionality, languages, stack, dependencies and flows. Both appear near the top of
the HTML report; the original analyses remain below. This summarizes the current saved
snapshot, not archived snapshots or superseded review attempts, and performs no new
source review. Summary prose never changes findings, severity counts, scanner dispositions
or coverage.

Synthesis always accepts free-form model text, regardless of `--output-mode`. It uses
the configured endpoint, retries, context limits, call/time budgets, tracing and logging.
Large reports are split into bounded fragments and their summaries combined in multiple
passes. This can require many model calls and lose detail during compression; consult
the original analyses for evidence. If intermediate summaries cannot fit together,
increase input/context limits or reduce the output token allowance.

Responses and checkpoints are stored under `report_synthesis` in audit memory or the
separate implementation result. Rerun the same command after an interruption to resume;
completed synthesis is reused when the saved input and synthesis settings match. Changed
analyses invalidate the old summary. A regular offline report includes a matching saved
summary without model calls and omits stale summaries. `--summary` remains an offline
progress aggregation.

Empty or truncated responses remain in the checkpoint and are retried on the next run.
Incomplete synthesis produces an HTML report with an unfinished-summary notice and exits
`2`; cancellation/deadline uses the usual stop exit code and preserves any existing HTML.
All original results remain available. Synthesis requires `--model` (or `REVIEW_MODEL`)
and is only available with the two HTML report commands.
