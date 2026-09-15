# Semgrep SARIF investigations

[Back to README](../README.md)

```sh
# Import candidates and choose an investigation interactively.
python3 -m njordcup /path/to/repo --sarif /path/to/results.sarif \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 --api-key-env OLLAMA_API_KEY

# Authorize ALL findings, including low-level and suppressed results.
python3 -m njordcup /path/to/repo --sarif /path/to/results.sarif \
  --investigate-all --max-calls 100 \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 --api-key-env OLLAMA_API_KEY
```

Import builds a local component map; it does not spend the model budget on a full
architectural flyover before investigating scanner results. Generic component
flyovers remain available later with `--flyover-only`.

Every SARIF `result` receives a stable ID within that imported file. No severity,
suppression or baseline-state filter is applied. Results are grouped by component
and rule, with up to ten candidates per investigation. Each is assessed as
`confirmed`, `not_confirmed`, or `inconclusive`; a confirmation must reference a
locally validated finding from the verification pass. Counterevidence is a model
assessment, not proof. Related locations and code-flow source are retrieved within
context limits. SARIF investigations cover the chunks containing reported locations,
not every line of each affected file.

## Evidence requirements

Each disposition names the exact scanner rule and candidate ID, states the scanner
claim, and explains whether source evidence supports or refutes it. Both confirmations
and dismissals must quote the primary reported location with its original path and
line number. Quotes must exactly match source actually supplied to the model.

- **Confirmed:** additionally cites supporting source at a verified finding, matches
  its path/line/CWE, and places it at a location reported by that candidate. When the
  scanner rule declares CWE metadata, the finding must match one of those CWEs.
- **Not confirmed:** additionally cites concrete counterevidence and explains why
  it refutes this particular claim. Missing evidence alone cannot justify dismissal.
- **Inconclusive:** used for missing context, unresolved flow locations, invalid
  citations, unrelated findings, rule/CWE mismatches, and unsupported dispositions.

The verifier challenges both findings and candidate assessments before deterministic
checks run. Saved assessments include citations, the proposed status, validation
errors and the adjudication-policy version. Rule descriptions and CWE tags are read
from SARIF rule metadata. Reimporting the same scan enriches older candidates without
changing their area IDs.

These checks establish evidence provenance and a stronger association to the scanner
claim. Semantic relevance, exploitability and the explanation of counterevidence
still depend on model judgment; they are not a formal proof of correctness.

Older decisions without the current evidence policy appear as inconclusive and their
groups are eligible for `--investigate-all` again. Older attempts remain in history.

## Resuming and supported input

Budget exhaustion preserves completed work. Re-run the same command to continue
unfinished investigations; completed groups are skipped. Use `--rerun` to start
again. `--investigate-all` without `--sarif` resumes the active imported scan in
saved memory. Changed source requires a refreshed map/reimport first.

Supported input: SARIF 2.1.0 inline results, artifact indexes, URI bases, related
locations and code-flow locations. Paths must resolve inside the eligible local
checkout. Missing, excluded, out-of-tree or invalid locations remain explicitly
inconclusive in the summary; they are never silently dropped. Absolute paths from
another CI machine may need rebasing in the SARIF before import. The tool does not
run Semgrep or execute scanner-supplied commands.

## Format reference

[SARIF 2.1.0 specification](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html).

## Related guides

- [Memory and audit summaries](memory.md)
- [Configuration and request budgets](configuration.md)
- [Exit codes](getting-started.md#exit-codes)
