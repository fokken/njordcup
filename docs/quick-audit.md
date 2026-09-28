# Quick audit: model-prioritized files

[Back to README](../README.md)

`--quick-audit` uses a saved quick implementation rundown and the eligible file
inventory to choose a bounded set of files for a normal security review. It is
intended to prioritize potentially sensitive code when a full audit is too costly.
Selection is model judgment, not a guarantee that the most important issues will
be found or that omitted files are safe.

First generate a compatible [quick rundown](quick-implementation.md), then audit
up to your chosen number of priority files:

```sh
# Broad implementation rundown, sampling up to 50 files.
python3 -m njordcup /path/to/repo --quick-implementation-analysis \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 --quick-max-files 50

# Prioritize and audit up to 20 files, with two concurrent file workers.
python3 -m njordcup /path/to/repo --quick-audit --quick-max-files 20 --workers 2 \
  --model YOUR_MODEL --base-url http://localhost:11434/v1
```

Add the [input/output budgets](getting-started.md#quick-start-by-context-size) that
match your server to both commands. The file limit defaults to 50 in each mode;
the rundown's sample limit and the audit's target limit are independent.

## Selection and security review

The selector receives a bounded excerpt of the saved collective rundown and an
inventory of all eligible target files, including files the rundown did not inspect.
It is asked to prioritize entry points, authentication/authorization, tenant
boundaries, untrusted inputs, uploads, sensitive operations, secrets, cryptography
and security configuration, with a reason for each choice.

Inventory pages contain compact IDs and paths, filling the configured input/context
budget with output tokens and the token margin reserved. There is no fixed 200-file
cap. Large inventories are paged and shortlists narrowed in bounded model passes;
only nominees from each page advance. Logs distinguish inventory and shortlist
rounds and show page counts and candidate totals. Choices
use inventory IDs and are validated before any security review starts. The global
limit applies to distinct selected targets, not to each page or component. The model
can select fewer than the limit. Selection reasons are saved as unverified model text.
The report indicates whether the rundown excerpt was truncated; the original complete
rundown remains in its separate result file.

Chosen files go through the **normal security-review pipeline**, including all
eligible chunks of each target file, original line numbers, checkpointing and
per-file consolidation in prompt mode. This is not a shallow prefix-only audit.
Unfit chunks and unfinished consolidation remain incomplete. Related source from
other eligible files can be included within the normal context budget, but does
not count as reviewed target code or consume additional target slots.

The selector always accepts prose `FILE <id> - reason` choices. Subsequent security
reviews honor `--output-mode`: prompt mode preserves narrative analyses; structured
modes use their retrieval and verification protocol. Selection is sequential, and
`--workers` controls concurrent files within each selected component area. Selection
does not establish a strict risk-ranked execution order across components.

No architectural model flyover is run. The saved rundown is also supplied as bounded,
untrusted context for the selected reviews. Quick rundown results remain separate
from ordinary audits; this mode explicitly opts into their use.

## Saved scope and reporting

Default quick-audit memory is `.njordcup/quick-audit.json`, separate from the ordinary
`.njordcup/memory.json`. Its index is `.njordcup/quick-audit.index.json`. A custom
`--memory PATH` is allowed, but an existing non-quick audit file is rejected to avoid
mixing the scopes.

The rundown is read from the ordinary default
`.njordcup/memory.quick-implementation.json` (or the existing legacy memory location).
Use `--quick-implementation-file PATH` for a custom rundown, including one created
using a custom memory stem. Quick audit does not modify that rundown or its HTML.

Generate the report or summary using the quick-audit memory explicitly:

```sh
python3 -m njordcup /path/to/repo --report \
  --memory /path/to/repo/.njordcup/quick-audit.json

python3 -m njordcup /path/to/repo --summary \
  --memory /path/to/repo/.njordcup/quick-audit.json
```

The default HTML path is `.njordcup/quick-audit.report.html`. Add `--synthesize` and
model settings to the report command for an executive summary of the selected audit.

JSON and HTML identify this as a **prioritized subset** and show the selected-file
list, candidate count, number of files outside the scope, file limit, rundown source
and model selection responses. A complete quick audit means processing completed
for the selected targets; it does not mean the repository was fully audited.

## Resuming and limits

Re-run the same quick-audit command to reuse the selection and completed source work.
`--max-calls` includes both selection and review requests, retries and consolidation.
Time limits, cancellation, tracing, caching and overflow recovery also apply. Failed
selection remains checkpointed, with no source audit started. `--workers` and runtime
call/time limits can change between resumed runs without forcing a new selection.

The rundown must be complete and match the current source snapshot and discovery
scope. A missing, stale or incomplete rundown produces an actionable error rather
than silently starting extra analysis. Regenerate it with the same source filters.
Different model endpoints may generate the rundown and perform the audit.

Changing the target limit, source/scope, rundown text, audit model/output mode or
input/output budgets starts a new selection and archives the old quick-audit scope.
`--rerun` or `--refresh-memory` also reselects and reviews afresh. Ordinary parser and
dependency invalidation still apply to reviewed chunks.

Do not combine `--quick-audit` with `--automatic`, `--audit-only`, `--area`, SARIF,
reporting or implementation-analysis actions. For exhaustive eligible-file processing,
use `--audit-only` or `--automatic`; for scanner-only investigations use
`--sarif FILE --investigate-all`.
