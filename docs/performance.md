# Prompt efficiency and performance

[Back to README](../README.md) · [Configuration](configuration.md)

## Faster narrative reviews

The default prompt-mode security review sends compact architecture/history excerpts
(maximum 3,000 serialized characters). Prior findings and narrative excerpts are
filtered to the target and supplied reference files; implementation notes are selected
by their recorded paths. Full analyses remain in memory and reports.

Optional reference code is ranked by matching identifiers and declarations within
related files, rather than selecting their first two chunks. At most six excerpts
(two per related file) are considered. Their combined allowance is the smaller of
remaining `--context-chars` and half the target's numbered text size, with a 1,024
character floor before applying that remaining allowance. Large reference chunks
are excerpted around matching declarations or lines, retaining original line numbers.
This is heuristic retrieval, not proof that all relevant context was supplied.

Target chunks are unchanged. Explicit SARIF flow locations are selected first under
the existing context budget. The redundant related-file catalog is omitted from
prompt-mode requests; structured modes retain their model-driven retrieval protocol.

Security prompts request evidence-backed issues with Title, Description, Impact and
Remediation, avoiding code restatements and generic advice. When no supported issue
is identified, the model is asked for a one-to-three-sentence scope/limitations note.
There is no forced short answer when evidence needs explanation, and raw responses
remain accepted regardless of headings or length. Per-file consolidation still runs
once all parts are complete and uses checkpoints as before.

Use `--verbose` to see architecture/history character reductions, reference counts,
request budget estimates, and provider-reported input/output tokens. Request timings
remain available at normal verbosity. Reductions in prompt size do not establish an
inference speedup or unchanged detection accuracy; measure these on your model/server.
Use `--rerun` if you want already-completed areas regenerated with the new prompts.

## Timing and throughput

Normal runtime logs finish with per-phase performance summaries: flyover, security
review, file consolidation, implementation description, and report synthesis,
when those phases ran. Each shows elapsed time, API attempts, cache hits, average
HTTP-attempt duration, server-reported input/output tokens and end-to-end output
tokens per second. `--log-file` captures these summaries; `--quiet` suppresses them.

The final analysis JSON includes `performance.phases` and overall invocation elapsed
time. Review checkpoints also save performance for that review invocation and a
`reuse` record listing completed files and chunks reused. The offline audit summary
exposes `area_performance` and `area_reuse`; HTML shows latest-attempt review and
consolidation times and reused-file counts.

Phase time includes request preparation, network time, input processing and retry
backoff. HTTP-attempt time excludes backoff. Neither metric measures raw decoding
speed. Missing token usage is recorded as `responses_without_usage`; throughput is
null when no complete usage metadata is available. Mixed reporting gives only a
partial token total. Cache hits do not add API attempts or token usage. These
measurements are controller observations, not GPU utilization or hardware benchmarks.

With [concurrent workers](workers.md), phase durations sum overlapping request times
and may exceed wall-clock elapsed time. The phase token rate uses that summed time,
not aggregate wall-clock throughput. Use the overall invocation or area's elapsed
time to compare one worker against several under equivalent audit conditions.
