# Resource planning and benchmarks

[Back to README](../README.md)

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

## Related guides

- [Configuration and request budgets](configuration.md)
- [Architecture and coverage](architecture.md)
- [Testing and model evaluation](testing.md)
