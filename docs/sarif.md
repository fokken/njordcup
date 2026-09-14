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
