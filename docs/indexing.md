# Optional syntax indexing

[Back to README](../README.md)

Install the optional parser extra in your njordcup environment:

```sh
pip install -e ".[syntax]"
python3 -m njordcup /path/to/repo --index-only
```

The default `--index-mode auto` automatically uses installed Tree-sitter grammars
for JavaScript (including JSX), TypeScript/TSX, Go, Rust, Java, C and C++.
Python retains its standard-library AST parser. No parser packages are downloaded
at runtime, and the basic installation still has no runtime dependencies.

Tree-sitter provides syntactic function, method and class ranges and call names.
Those ranges guide the existing bounded chunker; long functions still split to fit
input budgets. Named arrow functions are recognized, and declarations or calls in
comments are not treated as real syntax. Unknown or anonymous names remain explicit.
Call names feed the existing related-file lookup, which supplies reference context
for both structured and narrative reviews.

All eligible source lines remain indexed, including top-level code, comments and
configuration. This is not a function-only audit. Missing or incompatible parsers,
unsupported extensions and syntax errors fall back to lexical metadata. The index
records fallback notes and counts files by parser. `--index-mode text` bypasses
all syntax parsing and remains available for any UTF-8 source language.

Import extraction and cross-file call resolution remain heuristic. Tree-sitter does
not resolve dynamic dispatch, establish exploitability or perform taint analysis.
A `.h` file is initially treated as C; C++ syntax errors trigger fallback. Extra
languages can be added with a grammar mapping and real-grammar tests.

Parser package versions and the extraction profile are saved in the index. Installing,
removing or upgrading parser packages rebuilds metadata on the next indexing run.
Review checkpoint signatures also include the profile. Already completed areas in
automatic mode are still skipped; use `--rerun` to reanalyze them with the new context.
Saved architectural descriptions need not be regenerated simply to enable parsing.

This applies the structural-indexing idea used by
[Cisco AI Deep SAST](https://github.com/cisco-open/ai-deep-sast/blob/main/indexer.py)
to njordcup's existing index, without adopting its function-only scanning scope.
The adapter uses the [Tree-sitter Python API](https://tree-sitter.github.io/py-tree-sitter/).
