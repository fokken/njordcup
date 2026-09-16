# njordcup

A Python 3.10+ security review CLI for Ollama, vLLM, and other OpenAI-compatible
APIs. No runtime Python dependencies are required.

**Index → flyover → saved memory → focused investigations → audit summary.**

njordcup maps repository components, reviews bounded code chunks, and saves
progress between runs. It can also investigate individual Semgrep SARIF findings
or the entire imported scan.

Source discovery accepts eligible UTF-8 text in any language, including unknown
extensions and extensionless files. Use `--index-mode text` for generic indexing.

## Quick start

Run from this checkout, or install with `pip install -e .` for the `njordcup` command.
Replace `YOUR_MODEL` with a model served by your endpoint.

```sh
# Map the codebase and choose an area to investigate.
python3 -m njordcup /path/to/repo \
  --base-url http://localhost:11434/v1 --model YOUR_MODEL \
  --api-key-env OLLAMA_API_KEY

# Summarize saved progress without model calls.
python3 -m njordcup /path/to/repo --summary
```

To investigate every Semgrep finding, add `--sarif results.sarif --investigate-all`
to the review command. To build only the local index, run
`python3 -m njordcup /path/to/repo --index-only`.

Use `--automatic` to audit all source components without prompts. Generate an offline
HTML security report with `python3 -m njordcup /path/to/repo --report`.
Use `--implementation-analysis` to describe the code and `--implementation-report`
to generate its separate HTML report.

## Documentation

- [Getting started](docs/getting-started.md): workflows, review scope, and exit codes.
- [Configuration](docs/configuration.md): providers, credentials, budgets, and caching.
- [Semgrep SARIF](docs/sarif.md): importing findings, investigating all results, and resuming.
- [Implementation analysis](docs/implementation-analysis.md): saved implementation descriptions and security reuse.
- [HTML reports](docs/reporting.md): formatted findings, evidence, and coverage.
- [Memory and summaries](docs/memory.md): checkpoints, history, and invalidation.
- [Architecture and coverage](docs/architecture.md): indexing, retrieval, and limitations.
- [Resource planning](docs/resource-planning.md): context windows, hardware, and scale benchmarks.
- [Testing and evaluation](docs/testing.md): tests and model-quality smoke fixtures.

Local indexing has been exercised on a synthetic 250k-line repository. Live model
accuracy remains unvalidated; coverage counts do not establish that code is secure.
