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
[evals/cases.json](../evals/cases.json) contains vulnerable/fixed SQL and shell-injection smoke fixtures;
the evaluation command makes live model calls and reports CWE-level precision/recall,
incomplete cases and usage. Four fixtures are not a production benchmark. No live
model accuracy or server compatibility has been established in this workspace.

## Related guides

- [Resource planning and benchmarks](resource-planning.md)
- [Architecture and limitations](architecture.md)
