# Testing and model evaluation

[Back to README](../README.md)

Run these commands from the repository root.

```sh
python3 -m unittest discover -s tests -v
python3 benchmarks/scale.py --lines 250000
python3 -m njordcup.evaluate --model YOUR_MODEL --base-url http://localhost:11434/v1
```

Tests use mocked model responses and include a 250k-line index, checkpoint resumption,
component invalidation, evidence validation and all-results SARIF accounting.
Failure-injection tests cover bounded retries, call-budget accounting, timeouts,
cancellation/resumption, rule/CWE mismatches, and missing or fabricated counterevidence.
Language-agnostic tests cover unknown extensions, extensionless files, Unicode
search, text-only indexing, source filters, and SARIF locations in additional languages.
Budget tests cover output reservation, Unicode, batch splitting/resumption, flyover
coverage updates and explicit incomplete reviews when source context cannot fit.
Retrieval tests cover identifier fragments, paths, quoted phrases and unread matches.
Automatic-mode tests cover uninterrupted selection, budget resumption, and notifications
after persistence. HTML tests cover offline generation, escaped hostile content,
and protection of memory/index files.
Implementation-analysis tests cover separate output, partial resumption, stale/scope
rejection, and security reuse that saves mapping calls while still reviewing source.
Logging tests check stderr/stdout separation, verbosity, checkpoint visibility,
request timing/cache events, sensitive-payload exclusion, and handler cleanup.
Opt-in trace tests cover retry correlation, error bodies, invalid JSON, cache hits,
private permissions, append sessions, source exclusion, and destination protection.
Runtime-logfile tests cover append behavior, stderr notifications, stdout separation,
source exclusion, permissions, destination collisions, and stream cleanup.
Regression tests cover Git subdirectory scope, index protection during summaries,
completed SARIF scan exit codes, malformed input, flyover resumption/coverage, and
retrieval bookkeeping after context trimming.
[evals/cases.json](../evals/cases.json) contains ten vulnerable/fixed smoke fixtures
across Python, JavaScript, Go and Ruby, covering SQL injection, shell injection and
object authorization. JavaScript and Ruby cases require cross-file reasoning.
The evaluation command makes live model calls and reports CWE-level precision/recall,
incomplete cases and per-case token/call usage. Ten fixtures are not a production benchmark. No live
model accuracy or server compatibility has been established in this workspace.

Cases support a `sources` mapping of repository paths to text and optional `targets`
to keep supporting files outside the finding scope. Legacy `source` fixtures remain
supported as `app.py`. Expected CWEs are scored per fixture, not per location;
false negatives include missing expected CWEs in incomplete cases. No fixture code
is executed. The default evaluation budget is 100 calls shared across all cases.

Free-form output tests exercise JSON API envelopes containing arbitrary text, verbatim
Unicode/Markdown preservation, cache round trips, truncated checkpoints, failed
resumption, SARIF non-promotion, and both HTML report workflows.

Report synthesis tests cover bounded map/reduce calls, interrupted resumption, unchanged
input reuse, stale-summary suppression, free-form/truncated responses, escaped HTML,
both CLI report modes and preservation of original analyses and HTML on cancellation.

Install `pip install -e ".[syntax]"` to include real Tree-sitter grammar tests in
the normal unittest run. Without the extra, those tests are skipped; parser fallback
and cache invalidation tests still run. Grammar tests cover JS, TS/TSX, Go, Rust,
Java, C/C++, Unicode names, calls, dependency hints and full line coverage.

Per-file review tests verify separate target requests, complete chunk coverage, empty
files, retained original responses, consolidation resumption without repeated source
review, and one escaped HTML entry per file.

Context optimization tests cover relevant-history filtering, bounded excerpts around
late-file definitions, unchanged target source/coverage, preserved SARIF flow context,
and request-size reduction against the previous context-selection strategy.

Incremental tests verify file reuse within a changed component, persistence through
a separate flyover, dependency/reference invalidation, added/removed scope and forced
reruns. Performance tests cover retries, cache accounting, phase separation and missing
token-usage metadata. These use mocked model responses, not live-model benchmarks.

Overflow tests inject explicit HTTP context errors and verify bounded recovery,
unchanged target coverage, synthesis repartitioning, call limits and incomplete
single chunks. Regression tests also cover parser-profile invalidation in mapping,
structured flyovers and explicit area selection, and overlapping file analyses.

Worker tests exercise overlapping HTTP attempts, shared call/retry budgets,
coordinator-only checkpoint callbacks, cancellation/deadlines, failed persistence,
consolidation resumption, overflow recovery, trace integrity, cache accounting and
the CLI report workflow. They use mocked transport, not live-model speed measurements.

Quick implementation tests cover model-selected inventory paging, the global file
limit, collective synthesis input, invalid choices, bounded prefix samples, overflow
recovery, checkpoint resumption, artifact isolation and escaped offline HTML.

## Related guides

- [Resource planning and benchmarks](resource-planning.md)
- [Architecture and limitations](architecture.md)
