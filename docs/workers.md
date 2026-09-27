# Concurrent file reviews

[Back to README](../README.md) · [Review flow](architecture.md#flow-description)

`--workers N` allows up to N files in the selected source-review area to be processed
concurrently. The default is `1`, preserving sequential execution. Start with `2`
and compare elapsed time on your inference server before increasing it.

```sh
python3 -m njordcup /path/to/repo --automatic --workers 2 \
  --model YOUR_MODEL --base-url http://localhost:11434/v1 \
  --context-window 32768 --max-tokens 4096 --max-input-chars 24000 \
  --batch-chars 10000 --context-chars 6000
```

Use your server's actual context limit; other starting budgets are in the
[context quick start](getting-started.md#quick-start-by-context-size). `--workers`
also applies to interactive and `--area` source reviews and to explicit structured
output modes. All workers use the same configured endpoint and model.
Replace `--automatic` with `--audit-only` to skip architectural model calls and
start the security review after local indexing.
Workers also apply to selected files in `--quick-audit`; its model-driven selection
stage remains sequential and shares the same invocation call budget.

## Scheduling and context

Each worker reviews one file's batches sequentially, then consolidates that file's
responses if needed. Other workers can review or consolidate different files at the
same time. New files are assigned as workers finish. There are at most N active
file jobs and HTTP attempts at once, and fewer when an area has fewer pending files.

Areas themselves remain sequential. Indexing, architectural flyover, SARIF
investigations, both implementation-analysis modes and report-wide synthesis also remain
sequential; increasing `--workers` does not parallelize those operations. File
consolidation is part of the file job and can overlap another file's source review.

Workers share the read-only source index and receive the area's saved review context.
They do not exchange live discoveries or act as independent reviewers of the same
code. Related source excerpts remain available as before. Increasing workers changes
scheduling, not the audit scope, per-request context budget or verification policy.
Logs show file-job start/finish messages and globally numbered HTTP attempts;
completion messages may appear out of source-file order.

## Checkpoints, limits and failures

Workers send progress snapshots to a central coordinator. Only that coordinator
writes audit memory and announces saved results. A worker waits until its checkpoint
is accepted before continuing, so concurrent jobs do not race to replace memory.
Trace writes are serialized and share one trace session; cache files use atomic
replacement. Separate njordcup processes must still use separate memory files.

`--max-calls` is shared across the entire invocation, including flyover, all workers,
retries and file consolidation. It is not multiplied by the worker count. A shared
deadline and cancellation control also apply to all workers. A learned lower input
cap following a server context error is shared with subsequent worker requests;
already-sent requests are unaffected.

Call-budget exhaustion or a fatal provider error stops new file jobs and requests.
Already-sent requests can finish and save their results; pending work remains
incomplete. Cancellation and deadline checks apply during response reading as well,
so an interrupted response may need to be requested again. Blocking network I/O can
delay shutdown until the socket operation returns or times out. A checkpoint-writing
failure stops the workers; the last successfully written snapshot remains available.

Re-run the same audit to resume. You can change `--workers` without invalidating
completed chunk checkpoints. Completed source is reused when only consolidation
remains unfinished. Changes to source, parser profile and review budgets retain the
existing [invalidation rules](memory.md).

## Measuring whether workers help

Concurrency may improve throughput when the server can batch requests or has spare
capacity. It may instead increase latency and KV-cache memory pressure on a saturated
GPU. There is no measured speedup for a live model in this workspace.

Compare equivalent fresh audits with separate memory files and consistent model,
scope, budgets and cache settings. Reusing completed work would distort the comparison.
Use overall invocation or area elapsed time for wall-clock comparisons. Phase durations
sum request times and can exceed elapsed wall time because workers overlap; their
token rate is not aggregate wall-clock throughput or raw decoding speed.
