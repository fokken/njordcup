# Architecture and coverage

[Back to README](../README.md)

The local index inventories every eligible file and line before model analysis.
It groups files by nested dependency manifests or module directories. Python uses
AST symbol ranges and import/call metadata; other supported languages use lexical
metadata with that limitation recorded in the index. These are candidate navigation
edges, not a sound semantic call graph or taint analysis.

Large files are chunked with original line numbers and preferred symbol boundaries.
Oversized functions fall back to bounded line chunks. A single line exceeding the
batch budget remains explicitly unreviewed. Default file-size limit: 2 MB, adjustable
with `--max-file-bytes`. Supported source/config extensions, exclusions, lockfile
skips and symlink protection are defined in [njordcup/repository.py](../njordcup/repository.py).

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

## Boundaries

Repository code and fixtures are never executed. Source and scanner messages are
untrusted prompt data; neither prompt-injection resistance nor detection accuracy
is guaranteed. Use a stable checkout. File exclusions are not a comprehensive secret
redactor. The implementation has no sound cross-language taint engine, sandboxed
exploit testing, or distributed job scheduler.

## Related guides

- [Memory and invalidation](memory.md)
- [Resource planning and benchmarks](resource-planning.md)
- [Testing and model evaluation](testing.md)
