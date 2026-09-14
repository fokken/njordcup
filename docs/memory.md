# Memory, resuming, and audit summaries

[Back to README](../README.md)

Default memory: `.njordcup/memory.json`; local index: `.njordcup/memory.index.json`.
`--memory PATH` relocates memory and its adjacent index. Existing legacy
`.security-review/memory.json` is reused when the new default does not exist.

Each attempt saves findings, source evidence, coverage, errors, dependencies read,
SARIF assessments and usage. Incomplete reviews resume completed chunks when their
source and observed dependencies still match. Completed areas may be deliberately
reviewed again. Old attempts remain in history.

For component-mapped audits, edits invalidate affected components and reverse
import/call dependents. Unaffected review attempts and component summaries carry
forward to the new snapshot. Read-context hashes invalidate additional affected
reviews. Root/ancestor manifests participate in dependency edges. Dynamic dispatch,
reflection and unresolved cross-language links can evade inferred dependencies;
use `--refresh-memory` for a conservative full reset. Small legacy flyovers reset
as a whole. SARIF scans are archived on source changes and must be reimported.

Prior snapshots move into `archives`. The menu and summary use the latest attempt
for each current area; bounded prior-review summaries are supplied as context.
Memory contains sensitive code excerpts and findings. Use a private directory and
one writer per memory file; concurrent writers are not supported. Archives are
retained indefinitely, so storage requirements grow with audit history.

## Audit summaries

```sh
python3 -m njordcup /path/to/repo --summary
python3 -m njordcup /path/to/repo --summary --output audit-summary.json
```

`--summary` is offline and describes the saved snapshot; it does not check the
current filesystem. It shows remaining areas, deduplicated findings by severity,
chunk/line/symbol coverage, SARIF dispositions, limitations and focused-review usage.
It does not count archived findings as current. Use `--output PATH` to save any JSON
report separately; output cannot overwrite memory or its index.

## Related guides

- [Getting started](getting-started.md)
- [Architecture and coverage](architecture.md)
- [Semgrep SARIF investigations](sarif.md)
