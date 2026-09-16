# Implementation analysis

[Back to README](../README.md)

Describe how the project works before investigating security issues:

```sh
python3 -m njordcup /path/to/repo --implementation-analysis \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 --max-calls 100
```

This mode asks the model to describe languages, technology stack, dependencies,
important functionality, module responsibilities, entry points, configuration,
integrations, important functions, and control/data flows. It records uncertainties
and only describes behavior supported by the supplied source samples.

The result is separate from security-audit memory. By default it is saved to
`.njordcup/memory.implementation.json`. With custom `--memory`, the same stem receives
`.implementation.json`. Use `--implementation-file PATH` to choose a different result
path; use that option again when asking a security review to reuse it. `--output`
can additionally save the final JSON output to a separate destination.

The JSON contains aggregate language/stack lists and per-component page descriptions,
functionality areas, source hashes, sampling coverage, unknowns, errors, and model usage
for the current invocation. It is not a security findings report. Running this mode
does not change security review attempts or mark security coverage complete.

## HTML implementation report

```sh
python3 -m njordcup /path/to/repo --implementation-report
python3 -m njordcup /path/to/repo --implementation-report --output implementation.html
```

This generates a separate, self-contained HTML report from the saved implementation
result, without model calls or security-audit memory. The default is
`.njordcup/memory.implementation.html`. For a custom `--implementation-file`, the
result filename gets an `.html` suffix; pass the same input option when reporting.

The report includes languages, stack, component functionality, implementation details,
data/control flows, dependencies, sampling coverage, pending pages and unknowns.
Partial analyses can be reported too. Generation returns `0` on success regardless
of whether analysis is complete; the report displays its saved status. Source and
model text are escaped, and the HTML uses no external resources or scripts.

Use `--report` for the separate security-findings HTML report. Both report commands
read snapshots; they do not verify whether current source has changed.

## Bounded analysis and resumption

Eligible files use the same language-agnostic discovery, include/exclude filters,
`--base` scope and indexing settings as audits. Components are described in pages
of up to 30 files using bounded source samples. This is architectural description,
not an exhaustive function-by-function account of every line in a large repository.

Each completed page is saved atomically. Call limits, retry settings, output/context
budgets and cancellation remain in force. Rerun the same command to finish pending
pages for the same source snapshot and provider. `--rerun` or `--refresh-memory`
regenerates this analysis. Changed source, scope or indexing mode starts a new result;
the implementation result file describes the current snapshot rather than keeping
historical copies. Existing security memory remains separate.

## Reuse during security reviews

A normal, automatic or SARIF review looks for the implementation result automatically.
When its source fingerprint, selected scope and indexing mode match, completed page
descriptions can seed component flyovers without repeating those model calls. Relevant
implementation details and data flows also enter the bounded context of focused reviews.
Partial results can supply completed pages while remaining pages are mapped normally.
Security reuse can use a different model than the implementation analysis.

Descriptions remain untrusted model context. Security reviews still retrieve source,
verify findings and check exact evidence. Implementation coverage never counts toward
security review coverage. Stale, malformed or incompatible optional results are ignored;
the security JSON reports whether compatible analysis was available. `--refresh-memory`
forces fresh architectural mapping, while valid descriptions may still inform focused
review context.

The default result path is excluded from source discovery. If you put extra output
copies inside the repository, exclude them from subsequent runs to keep the source
snapshot consistent. See [configuration](configuration.md) and [automatic auditing](getting-started.md#automatic-auditing).
