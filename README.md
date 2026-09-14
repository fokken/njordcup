# njordcup

A Python 3.10+ security review CLI with configurable OpenAI-compatible inference.
Connect to Ollama, vLLM or OpenAI. No runtime Python dependencies are required.

The workflow is **index → architectural flyover → saved memory → select areas →
focused investigation → audit summary**. It also imports Semgrep SARIF and can
investigate all scanner results without per-area prompts.

## Quick start

Run from this checkout, or install with `pip install -e .` for the `njordcup` command.
Replace `YOUR_MODEL` with a model served by your endpoint.

```sh
# Local index only: no credentials or model calls.
python3 -m njordcup /path/to/repo --index-only

# Flyover, then select an area in the interactive menu.
python3 -m njordcup /path/to/repo \
  --base-url http://localhost:11434/v1 --model YOUR_MODEL \
  --api-key-env OLLAMA_API_KEY

# Save/resume architectural analysis without starting a focused review.
python3 -m njordcup /path/to/repo --model YOUR_MODEL \
  --base-url http://localhost:8000/v1 --api-key-env VLLM_API_KEY --flyover-only

# Select an area from saved memory using the same provider/scope settings.
python3 -m njordcup /path/to/repo --model YOUR_MODEL \
  --base-url http://localhost:8000/v1 --api-key-env VLLM_API_KEY --area 2

# Offline snapshot summary; no model or credentials required.
python3 -m njordcup /path/to/repo --summary
```

Interactive reviews save after every completed batch and return to the area menu.
Non-interactive runs stop after the flyover unless `--area` or `--investigate-all`
explicitly authorizes investigation. `--dry-run` previews eligible paths without
model calls or writing the index.

## Semgrep SARIF investigations

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

## Large repositories

The local index inventories every eligible file and line before model analysis.
It groups files by nested dependency manifests or module directories. Python uses
AST symbol ranges and import/call metadata; other supported languages use lexical
metadata with that limitation recorded in the index. These are candidate navigation
edges, not a sound semantic call graph or taint analysis.

Large files are chunked with original line numbers and preferred symbol boundaries.
Oversized functions fall back to bounded line chunks. A single line exceeding the
batch budget remains explicitly unreviewed. Default file-size limit: 2 MB, adjustable
with `--max-file-bytes`. Supported source/config extensions, exclusions, lockfile
skips and symlink protection are defined in `njordcup/repository.py`.

Multi-component repositories, repositories over 2,000 lines or over 40 files use
progressive component flyovers. All selected files belong to an area, with no global
eight-area cap. Components are analyzed in pages of up to 30 files. Each completed
page is saved, and later runs resume pending pages. A page's model analysis still
samples source; indexing completeness is distinct from architectural understanding.
Small single-component repositories retain the lightweight flyover.

Focused review uses bounded source batches and up to three retrieval rounds. The
model can request chunks, original line locations, or identifier searches across
the complete index. Related import/caller candidates help navigation. Findings are
challenged in a second model pass and checked against exact supplied source lines.
Reports count reviewed chunks, lines, symbols and heuristic attack-surface signals.
Coverage measures processing, not the absence of vulnerabilities.

## Memory and resuming

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

`--summary` is offline and describes the saved snapshot; it does not check the
current filesystem. It shows remaining areas, deduplicated findings by severity,
chunk/line/symbol coverage, SARIF dispositions, limitations and focused-review usage.
It does not count archived findings as current. Use `--output PATH` to save any JSON
report separately; output cannot overwrite memory or its index.

## Configuration and resource planning

| Flag | Environment | Default |
| --- | --- | --- |
| `--base-url` | `REVIEW_BASE_URL` | `https://api.openai.com/v1` |
| `--model` | `REVIEW_MODEL` | Required for model operations |
| `--api-key-env` | `REVIEW_API_KEY_ENV` | `OPENAI_API_KEY` |
| `--output-mode` | `REVIEW_OUTPUT_MODE` | `json_schema` |

`--api-key-env` names the variable containing the credential, not the credential
itself. Local endpoints can run without a key. HTTP redirects are rejected. Only
the configured endpoint is contacted; there is no fallback to a cloud provider.
For servers without strict schemas, choose `--output-mode json_object` or `prompt`.
Every mode still validates model output locally. The adapter uses Chat Completions
with `max_tokens`; verify compatibility with your deployment.

Defaults: `--max-calls 20`, `--max-tokens 6000`, `--batch-chars 24000`,
`--context-chars 24000`, `--context-rounds 3`, `--max-input-chars 80000`.
The request cap includes messages and output-format schema and rejects oversized
requests before contacting the server. It is a character cap, not exact token
counting; set server limits using the model's tokenizer. Large repositories need
more calls or more resumptions, not a repository-sized context window.

For 50k, 150k and 250k-line repositories alike, plan on a **64k-token context** for
default settings, or start with **32k** and reduced budgets, for example:

```sh
python3 -m njordcup /path/to/repo --model YOUR_MODEL \
  --base-url http://localhost:11434/v1 --api-key-env OLLAMA_API_KEY \
  --batch-chars 12000 --context-chars 12000 --max-tokens 4000 --max-input-chars 48000
```

These are planning estimates, not certified minimums. Assuming 2–4 characters per
token, a 48,000-character request is roughly 12k–24k input tokens, plus up to 4k
output tokens and server formatting overhead. Some languages/content tokenize
less efficiently. A 16k window requires further tuning and sacrifices useful context.

Budget roughly **4 GB RAM and two CPU cores for the njordcup controller** as an
initial deployment allowance, excluding inference and growing audit archives.
This is headroom, not a measured hard requirement. Model RAM/VRAM depends on model
weights, precision, architecture, KV-cache length and concurrency; repository line
count alone cannot specify it. Calls currently run sequentially.

Synthetic Linux indexing measurements in this development environment:

| Lines | Files / components | Initial indexing | Process peak RSS | Index file |
| --- | --- | --- | --- | --- |
| 50,000 | 50 / 10 | 0.66 s | 28.9 MiB | 0.82 MB |
| 150,000 | 150 / 30 | 2.02 s | 52.4 MiB | 2.46 MB |
| 250,000 | 250 / 50 | 3.41 s | 77.8 MiB | 4.11 MB |

These measure generated Python indexing/retrieval metadata, not a full live audit,
server memory, detection quality or guaranteed performance on arbitrary repositories.
Reproduce with `python3 benchmarks/scale.py --lines 250000`.

`--base COMMIT` selects changed whole files, including staged, unstaged and untracked
files. Related source remains available for retrieval. Deleted files are not analyzed.
SARIF investigations explicitly include eligible reported locations even when those
files are outside the changed-file selection.

`--cache /private/cache` enables response caching by endpoint, model and complete
request. Clear it when changing weights behind an unchanged model name. Cache
entries contain findings/source excerpts. No automatic retries consume extra budget.

Exit codes: `0` completed without findings, saved flyover/import, index/dry run or
successful summary; `1` completed with findings; `2` incomplete investigation/error.
For `--investigate-all`, any inconclusive result returns `2`. Read the JSON status:
a successful summary command does not mean the audit itself is complete.

## Tests and model evaluation

```sh
python3 -m unittest discover -s tests -v
python3 benchmarks/scale.py --lines 250000
python3 -m njordcup.evaluate --model YOUR_MODEL --base-url http://localhost:11434/v1
```

Tests use mocked model responses and include a 250k-line index, checkpoint resumption,
component invalidation, evidence validation and all-results SARIF accounting.
`evals/cases.json` contains vulnerable/fixed SQL and shell-injection smoke fixtures;
the evaluation command makes live model calls and reports CWE-level precision/recall,
incomplete cases and usage. Four fixtures are not a production benchmark. No live
model accuracy or server compatibility has been established in this workspace.

Repository code and fixtures are never executed. Source and scanner messages are
untrusted prompt data; neither prompt-injection resistance nor detection accuracy
is guaranteed. Use a stable checkout. File exclusions are not a comprehensive secret
redactor. The implementation has no sound cross-language taint engine, sandboxed
exploit testing, or distributed job scheduler.

Protocol references: [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Ollama compatibility](https://docs.ollama.com/api/openai-compatibility),
[vLLM compatibility](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/),
[SARIF 2.1.0](https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/sarif-v2.1.0-os.html).
