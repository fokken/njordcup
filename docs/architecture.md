# Architecture and coverage

[Back to README](../README.md)

The local index inventories every eligible file and line before model analysis.
Discovery is language agnostic: any UTF-8 text file is eligible, including unknown
extensions, extensionless scripts, documentation and configuration. No language
allowlist or parser installation is needed. Non-UTF-8 files are currently skipped.

Files are grouped by known nested dependency manifests or module directories.
`--index-mode auto` (default) enhances Python with AST symbol ranges and import/call
metadata; other text receives best-effort lexical metadata. `--index-mode text`
uses only bounded text chunks and Unicode-aware identifier search, even for Python.
This mode does not infer symbols, imports or call edges. Manifest dependency edges
remain available. Both modes retain original line numbers and cover eligible lines.

Index statistics and focused-review coverage count files by indexing method:
`python_ast`, `lexical`, and `text`. Missing symbol information is not evidence that
a file has no functions. Lexical edges are navigation hints, not a sound semantic
call graph or taint analysis. Model understanding still varies by language.

Large files are chunked with original line numbers and preferred symbol boundaries.
Oversized functions fall back to bounded line chunks. A single line exceeding the
batch budget remains explicitly unreviewed. Default file-size limit: 2 MB, adjustable
with `--max-file-bytes`. Binary/control-byte files, common credential filenames,
lockfiles, SARIF artifacts and symlinks are excluded. Additional filters are defined
in [njordcup/repository.py](../njordcup/repository.py). UTF-8 detection and filename
exclusions are not comprehensive secret detection.

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
