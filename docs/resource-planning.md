# Resource planning and benchmarks

[Back to README](../README.md)

Repository size determines the number of requests, not a minimum context window.
For 50k, 150k and 250k-line repositories, **32k tokens** is a starting configuration
to measure on your model, with reduced budgets such as:

```sh
python3 -m njordcup /path/to/repo --model YOUR_MODEL \
  --base-url http://localhost:11434/v1 --api-key-env OLLAMA_API_KEY \
  --context-window 32768 --batch-chars 12000 --context-chars 12000 --max-tokens 4000 --max-input-chars 48000
```

These are planning estimates, not certified minimums. Assuming 2–4 characters per
token, a 48,000-character request is roughly 12k–24k input tokens, plus up to 4k
output tokens and server formatting overhead. This is only an illustrative tokenizer
estimate, not the controller's default estimate. Some languages/content tokenize
less efficiently. A 16k window requires further tuning and sacrifices useful context.
The adaptive planner defaults to a more conservative one serialized byte per token,
so it may split batches or omit context before reaching those character caps. Set
the actual server window explicitly; see [budget configuration](configuration.md).
A 64k window allows more surrounding context, but does not guarantee that the
default 80,000-character cap fits. Smaller windows can work with smaller batches
and output allowances; cross-file understanding may suffer. There is no validated
minimum context size or model size for reliable vulnerability detection.

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
| 250,000 | 250 / 50 | 3.44 s | 97.3 MiB | 4.11 MB |

These measure generated Python indexing/retrieval metadata, not a full live audit,
server memory, detection quality or guaranteed performance on arbitrary repositories.
Reproduce with `python3 benchmarks/scale.py --lines 250000`.
The 250k row was rerun with fragment/literal retrieval enabled; constructing its
retrieval index took another 0.65 seconds. The smaller rows are earlier measurements.

## Related guides

- [Configuration and request budgets](configuration.md)
- [Architecture and coverage](architecture.md)
- [Testing and model evaluation](testing.md)
