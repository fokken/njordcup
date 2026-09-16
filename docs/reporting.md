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
