# Quick implementation analysis

[Back to README](../README.md)

Get a broad, shallow rundown of a codebase without starting a security audit:

```sh
python3 -m njordcup /path/to/repo --quick-implementation-analysis \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --quick-max-files 50
```

The default file limit is **50**. This mode creates one collective analysis and an
HTML report automatically. It is a standalone orientation tool for the user; its
result is not automatically supplied to security audits. The existing
[`--implementation-analysis`](implementation-analysis.md) remains available for
component-based descriptions that can support later audits.

## How it works

1. Discover eligible files using the usual source filters. The model receives a
   file inventory containing paths, numeric IDs, line counts and character counts.
   It does not receive file contents at this selection stage.
2. Ask the model to choose representative manifests, documentation, entry points,
   configuration and core modules. Large inventories are paged. If their combined
   nominations exceed the file limit, the model narrows the shortlist in further
   bounded passes. No source is sent until a final selection within the limit is
   saved. The model may choose fewer than the limit.
3. Inspect selected files in bounded batches. Each sample contains up to the first
   6,000 characters of a file, reduced further if needed for the input budget. Long
   files are explicitly marked truncated. The model records concise observations,
   including a path and role for every supplied file sample.
4. Supply **all completed observations and the entire selected-file list** to the
   synthesis process. The model produces one collective rundown of purpose,
   architecture, languages, stack, functionality, entry points, integrations and
   data flow, plus an annotated list explaining every selected file's role.
   If the input is too large, bounded intermediate summaries are combined.

The report preserves the exact selected-file list, sample sizes, truncation details
and original observations alongside the collective analysis. Summarization can lose
details; an instruction to cover every file is not a guarantee that the model's
prose will mention every detail or correctly infer every relationship. The explicit
file list remains available regardless of the generated prose.

This mode uses prose even if `--output-mode` is set to a structured mode. File
selection uses simple `FILE <id>` lines, validated against the supplied inventory.
Unknown IDs, too many choices or unfinished selection responses are saved as an
incomplete attempt; they never authorize arbitrary paths. Analysis prose requires
no JSON or fixed headings.

## Files and reports

Default outputs:

| Artifact | Path |
| --- | --- |
| Saved result and resumable checkpoints | `.njordcup/memory.quick-implementation.json` |
| Collective HTML report | `.njordcup/memory.quick-implementation.html` |

Custom `--memory` changes the filename stem but does not make this mode read or
write security memory. `--quick-implementation-file PATH` selects a separate result
file; its HTML report uses the same stem with `.html`. `--output` optionally saves
an additional JSON copy. Audit memory, its index and the ordinary implementation
result are not modified. This mode does not build the audit's syntax index.

Regenerate HTML from saved results without model calls:

```sh
python3 -m njordcup /path/to/repo --quick-implementation-report
python3 -m njordcup /path/to/repo --quick-implementation-report --output rundown.html
```

Pass the same `--quick-implementation-file` when reporting a custom result. HTML is
self-contained and escapes model text and filenames. Partial results can also be
rendered; they show an incomplete status and any available original observations.
Cancellation or a fatal runtime stop preserves an existing HTML report; the saved
JSON checkpoint reflects the interrupted run. Use the offline report command to
render that partial snapshot explicitly.

## Budgets and resumption

`--quick-max-files` limits distinct files whose source is shown to the model across
the whole analysis, not per batch. It does not cap inventory entries, model calls,
local discovery reads or total tokens. Inventory selection, source descriptions
and collective synthesis all count toward the shared `--max-calls` budget.

Use the [context presets](getting-started.md#quick-start-by-context-size) for
`--context-window`, `--max-tokens` and `--max-input-chars`, substituting
`--quick-implementation-analysis` for `--automatic`. Source samples use this mode's
own 6,000-character ceiling; `--batch-chars`, `--context-chars`, and `--workers` do
not change its sampling or sequential execution. Retry limits, deadlines, logging,
tracing and context-overflow recovery still apply.

Re-run the same command to resume selection, inspection or synthesis. Matching
completed results need no new model calls. Changing source, selected scope, file
limit, provider or input/output budgets starts a fresh quick result. `--rerun` or
`--refresh-memory` also regenerates it. Unlike audit history, this result does not
archive earlier snapshots.

`--include`, `--exclude`, `--base`, file-size limits and standard discovery exclusions
define available files. An inventory of only changed files describes that scope,
not the whole repository. Generated quick artifacts are excluded from discovery;
continue passing a custom result path when resuming, and exclude additional report
copies inside the source tree. Coverage reports inspected file samples, not complete
implementation understanding or security assurance.
