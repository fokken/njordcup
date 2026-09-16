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

## Related guides

- [Resource planning and benchmarks](resource-planning.md)
- [Architecture and limitations](architecture.md)
