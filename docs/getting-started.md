# Getting started

[Back to README](../README.md)

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

## Review scope

`--base COMMIT` selects changed whole files, including staged, unstaged and untracked
files. Related source remains available for retrieval. Deleted files are not analyzed.
SARIF investigations explicitly include eligible reported locations even when those
files are outside the changed-file selection.

## Exit codes

Exit codes: `0` completed without findings, saved flyover/import, index/dry run or
successful summary; `1` completed with findings; `2` incomplete investigation/error.
For `--investigate-all`, any inconclusive result returns `2`. Read the JSON status:
a successful summary command does not mean the audit itself is complete.

## Related guides

- [Configuration](configuration.md)
- [Memory and audit summaries](memory.md)
- [Semgrep SARIF investigations](sarif.md)
