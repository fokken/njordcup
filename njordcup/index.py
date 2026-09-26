"""Local metadata index. Python AST and optional Tree-sitter parsing with lexical/text fallback."""
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import PurePosixPath
import re

MANIFESTS = {"package.json", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml", "Gemfile", "requirements.txt", "composer.json"}
SIGNALS = {
    "http/input": r"\b(request|router|route|endpoint|controller|handler)\b",
    "authentication/authorization": r"\b(auth|authenticate|authorize|permission|session|jwt|login)\b",
    "database": r"\b(query|execute|cursor|sql|database)\b",
    "process execution": r"\b(subprocess|exec|eval|system|popen)\b",
    "filesystem/upload": r"\b(upload|open|readFile|writeFile|extract|unzip)\b",
    "network": r"\b(fetch|requests|http|urlopen|socket|urllib)\b",
    "secrets/crypto": r"\b(password|secret|token|encrypt|decrypt|hashlib)\b",
}
SIGNAL_PATTERNS = {k: re.compile(v, re.I) for k, v in SIGNALS.items()}
IDENTIFIERS = re.compile(r"[^\W\d]\w*", re.UNICODE)


def search_terms(text):
    # Split conventions, not language grammars; preserve whole Unicode identifiers.
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", spaced).replace("_", " ")
    return {term.casefold() for term in IDENTIFIERS.findall(text) + IDENTIFIERS.findall(spaced)}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def component_for(path, manifest_dirs):
    parent = PurePosixPath(path).parent
    for candidate in (parent, *parent.parents):
        if str(candidate) != "." and str(candidate) in manifest_dirs:
            return str(candidate)
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        return "."
    if parts[0] in {"src", "lib", "services", "packages", "apps", "modules"} and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0]


def parse_file(path, source, chunk_chars, index_mode="auto"):
    lines = source.splitlines()
    symbols, imports, calls, notes = [], [], [], []
    parser = "text" if index_mode == "text" else "lexical"
    if index_mode == "auto" and path.endswith(".py"):
        try:
            tree = ast.parse(source)
            parser = "python_ast"
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                    symbols.append({"name": node.name, "start": start, "end": node.end_lineno,
                                    "kind": "class" if isinstance(node, ast.ClassDef) else "function"})
                elif isinstance(node, ast.Import):
                    imports.extend(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    prefix = "." * node.level + (node.module or "")
                    imports.append(prefix)
                    imports.extend(prefix + ("." if node.module else "") + a.name for a in node.names)
                elif isinstance(node, ast.Call):
                    name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
                    if name:
                        calls.append(name)
        except (SyntaxError, RecursionError, ValueError):
            notes.append("Python parsing failed; using lexical metadata")
    if parser == "lexical" and index_mode == "auto":
        from .syntax import extract
        syntax, note = extract(path, source)
        if note:
            notes.append(note)
        if syntax is not None:
            symbols, calls = syntax
            parser = "tree_sitter"
            notes.append("Syntax-derived symbols/calls; imports and dependency edges are heuristic, not a semantic call graph")
    if parser in {"lexical", "tree_sitter"}:
        imports = re.findall(r"(?:from\s+|require\s*\(|import\s*\(|import\s+|#include\s*)[\"'<]([^\"'>]+)", source)
    if parser == "lexical":
        notes.append("Symbols/imports/calls are heuristic; no semantic call graph")
        definition = re.compile(r"(?:\b(?:function|func|fn|def|class|interface)\s+(?:\([^)]*\)\s*)?)([A-Za-z_]\w*)")
        for i, line in enumerate(lines, 1):
            match = definition.search(line)
            if match:
                symbols.append({"name": match.group(1), "start": i, "end": i, "kind": "declaration"})
        calls = re.findall(r"\b([A-Za-z_]\w*)\s*\(", source)
    if parser == "text":
        notes.append("Text-only indexing: symbol and call/import resolution unavailable")
    boundaries = {s["start"] for s in symbols} | {s["end"] + 1 for s in symbols}
    boundaries.update(i + 1 for i, line in enumerate(lines, 1) if not line.strip())
    chunks, start, size = [], 1, 0
    def append(end):
        text = "\n".join(lines[start - 1:end])
        chunks.append({"id": f"{path}@{start}-{end}", "path": path, "start": start, "end": end,
                       "hash": digest(text), "chars": size,
                       "signals": [k for k, pattern in SIGNAL_PATTERNS.items() if pattern.search(text)]})
    for number, line in enumerate(lines, 1):
        cost = len(json.dumps(f"{number}: {line}")) + 2
        if number > start and (size + cost > chunk_chars or (number in boundaries and size >= chunk_chars // 2)):
            append(number - 1)
            start, size = number, 0
        size += cost
    if lines:
        append(len(lines))
    return {"hash": digest(source), "lines": len(lines), "parser": parser, "symbols": symbols,
            "imports": sorted(set(imports)), "calls": sorted(set(calls)), "chunks": chunks, "notes": notes}


def build_index(sources, targets, previous=None, chunk_chars=10000, index_mode="auto"):
    if index_mode not in {"auto", "text"}:
        raise ValueError("index_mode must be auto or text")
    previous = previous or {}
    from .syntax import profile
    parser_profile = profile() if index_mode == "auto" else None
    reusable = previous.get("files", {}) if previous.get("version") == 2 and previous.get("chunk_chars") == chunk_chars and previous.get("index_mode") == index_mode and previous.get("parser_profile") == parser_profile else {}
    files, reused = {}, 0
    manifest_dirs = {str(PurePosixPath(p).parent) for p in sources if PurePosixPath(p).name in MANIFESTS}
    components = defaultdict(list)
    for path, source in sorted(sources.items()):
        old = reusable.get(path)
        if old and old["hash"] == digest(source):
            metadata = dict(old)
            reused += 1
        else:
            metadata = parse_file(path, source, chunk_chars, index_mode)
        metadata["component"] = component_for(path, manifest_dirs)
        files[path] = metadata
        components[metadata["component"]].append(path)
    # Resolve import aliases and symbol candidates once, rather than scanning every file per query.
    aliases, definitions = defaultdict(set), defaultdict(set)
    for path, metadata in files.items():
        stem = str(PurePosixPath(path).with_suffix(""))
        for alias in {stem, stem.replace("/", "."), stem.removesuffix("/__init__").replace("/", "."), stem.removesuffix("/index")}:
            aliases[alias].add(path)
        for symbol in metadata["symbols"]:
            definitions[symbol["name"]].add(path)
    dependencies = {}
    for path, metadata in files.items():
        resolved = set()
        for name in metadata["imports"]:
            options = {name}
            if name.startswith("."):
                if path.endswith(".py"):
                    levels = len(name) - len(name.lstrip("."))
                    parent = PurePosixPath(path).parent
                    for _ in range(levels - 1):
                        parent = parent.parent
                    options.add(str(parent / name.lstrip(".").replace(".", "/")).lstrip("./"))
                else:
                    parts = list(PurePosixPath(path).parent.parts)
                    for part in name.split("/"):
                        if part == ".." and parts:
                            parts.pop()
                        elif part not in {".", ""}:
                            parts.append(part)
                    options.add("/".join(parts))
            for option in options:
                resolved.update(aliases.get(option, ()))
                resolved.update(aliases.get(str(PurePosixPath(option).with_suffix("")), ()))
        # Unique names provide useful candidate edges, not a claim of runtime resolution.
        for name in metadata["calls"]:
            if len(definitions.get(name, ())) == 1:
                resolved.update(definitions[name])
        # Ancestor manifests/configuration affect their descendants.
        for parent in (PurePosixPath(path).parent, *PurePosixPath(path).parent.parents):
            for manifest in MANIFESTS:
                candidate = str(parent / manifest)
                if candidate in files:
                    resolved.add(candidate)
        dependencies[path] = sorted(resolved - {path})
    methods = {method: sum(f["parser"] == method for f in files.values()) for method in ("python_ast", "tree_sitter", "lexical", "text")}
    return {"version": 2, "parser_profile": parser_profile, "index_mode": index_mode, "chunk_chars": chunk_chars, "files": files,
            "components": dict(components), "dependencies": dependencies, "targets": sorted(targets),
            "stats": {"files": len(files), "lines": sum(f["lines"] for f in files.values()),
                      "chunks": sum(len(f["chunks"]) for f in files.values()),
                      "symbols": sum(len(f["symbols"]) for f in files.values()),
                      "components": len(components), "reused_files": reused, "indexing_methods": methods}}


def affected_files(previous, current):
    before, after = previous.get("files", {}), current["files"]
    affected = {p for p in before.keys() | after.keys() if before.get(p, {}).get("hash") != after.get(p, {}).get("hash")}
    affected.update(p for p in before.keys() | after.keys()
                    if previous.get("dependencies", {}).get(p) != current.get("dependencies", {}).get(p))
    reverse = defaultdict(set)
    for index in (previous, current):
        for path, dependencies in index.get("dependencies", {}).items():
            for dependency in dependencies:
                reverse[dependency].add(path)
    queue = list(affected)
    while queue:
        for dependent in reverse[queue.pop()] - affected:
            affected.add(dependent)
            queue.append(dependent)
    return affected


class CodeIndex:
    def __init__(self, data, sources):
        self.data, self.sources = data, sources
        self.lines = {p: source.splitlines() for p, source in sources.items()}
        self.chunks = {c["id"]: c for f in data["files"].values() for c in f["chunks"]}
        self.terms = defaultdict(set)
        self.fragments = defaultdict(set)
        self.text = {}
        self.by_path = {p: [c["id"] for c in f["chunks"]] for p, f in data["files"].items()}
        self.reverse = defaultdict(set)
        for path, deps in data["dependencies"].items():
            for dep in deps:
                self.reverse[dep].add(path)
        for chunk_id, chunk in self.chunks.items():
            text = "\n".join(self.lines[chunk["path"]][chunk["start"] - 1:chunk["end"]])
            self.text[chunk_id] = text.casefold()
            for term in set(IDENTIFIERS.findall(text + " " + chunk["path"])):
                self.terms[term.casefold()].add(chunk_id)
            for term in search_terms(text + " " + chunk["path"]):
                self.fragments[term].add(chunk_id)

    def entry(self, chunk_id):
        c = self.chunks[chunk_id]
        return {"id": chunk_id, "path": c["path"], "start": c["start"], "end": c["end"],
                "lines": "\n".join(f"{i}: {self.lines[c['path']][i - 1]}" for i in range(c["start"], c["end"] + 1))}

    def search(self, query, limit=10, exclude=()):
        if limit <= 0 or not query.strip():
            return []
        query = query.strip()[:512]
        quoted = len(query) > 1 and query[0] == query[-1] and query[0] in "\"'"
        needle = (query[1:-1] if quoted else query).casefold()
        if not needle:
            return []
        scores = defaultdict(float)
        excluded = set(exclude)
        if not quoted:
            for term in search_terms(query):
                matches = self.fragments.get(term, ())
                # Rare identifiers distinguish useful matches from ubiquitous names.
                weight = 1 + len(self.chunks) / (1 + len(matches))
                for chunk_id in matches:
                    scores[chunk_id] += weight
                for chunk_id in self.terms.get(term, ()):
                    scores[chunk_id] += weight
        # Literal matching includes punctuation, strings and paths without parsing code.
        for chunk_id, chunk in self.chunks.items():
            if chunk_id in excluded:
                continue
            if needle in chunk["path"].casefold():
                scores[chunk_id] += 4 * (len(self.chunks) + 1)
            if needle in self.text[chunk_id]:
                scores[chunk_id] += 2 * (len(self.chunks) + 1)
        return sorted((c for c in scores if c not in excluded),
                      key=lambda c: (-scores[c], self.chunks[c]["path"], self.chunks[c]["start"]))[:limit]

    def reference_entry(self, chunk_id, target_ids, char_budget):
        """Bound a reference excerpt around a relevant declaration or matching line."""
        entry = self.entry(chunk_id)
        if len(json.dumps(entry)) <= char_budget:
            return entry
        terms = {t.casefold() for target in target_ids for t in IDENTIFIERS.findall(self.text[target])}
        chunk = self.chunks[chunk_id]
        anchors = [s['start'] for s in self.data['files'][chunk['path']]['symbols']
                   if s['name'].casefold() in terms and chunk['start'] <= s['start'] <= chunk['end']]
        if not anchors:
            def score(line):
                return sum(1 / max(1, len(self.terms.get(t, ())))
                           for t in {v.casefold() for v in IDENTIFIERS.findall(line)} & terms)
            anchors = [max(range(chunk['start'], chunk['end'] + 1),
                           key=lambda n: score(self.lines[chunk['path']][n - 1]))]
        start = end = anchors[0]
        def excerpt(first, last):
            return {**entry, 'start': first, 'end': last, 'excerpt': True,
                    'lines': '\n'.join(f"{n}: {self.lines[chunk['path']][n - 1]}" for n in range(first, last + 1))}
        result = excerpt(start, end)
        if len(json.dumps(result)) > char_budget:
            return None
        # Prefer following lines (function body), then leading context if space remains.
        for direction in (1, -1):
            while (direction == 1 and end < chunk['end']) or (direction == -1 and start > chunk['start']):
                candidate = excerpt(start, end + 1) if direction == 1 else excerpt(start - 1, end)
                if len(json.dumps(candidate)) > char_budget:
                    break
                result = candidate
                start, end = result['start'], result['end']
        return result

    def reference_chunks(self, target_ids, limit=6):
        """Rank nearby definitions/callers by identifiers in this batch, not file order."""
        if limit <= 0:
            return []
        paths = {self.chunks[c]['path'] for c in target_ids}
        related = set()
        for path in paths:
            related.update(self.data['dependencies'].get(path, []))
            related.update(self.reverse.get(path, []))
        related -= paths
        scores = defaultdict(float)
        # Inverted postings avoid scanning every related source chunk per request.
        terms = set()
        for chunk_id in target_ids:
            terms.update(t.casefold() for t in IDENTIFIERS.findall(self.text[chunk_id]))
        for term in terms:
            matches = self.terms.get(term, ())
            weight = 1 / max(1, len(matches))
            for chunk_id in matches:
                if self.chunks[chunk_id]['path'] in related:
                    scores[chunk_id] += weight
        # Prefer the declaration actually referenced by the target code.
        for path in related:
            for symbol in self.data['files'][path]['symbols']:
                if symbol['name'].casefold() in terms:
                    for chunk_id in self.by_path[path]:
                        c = self.chunks[chunk_id]
                        if c['start'] <= symbol['start'] <= c['end']:
                            scores[chunk_id] += 10
            # Keep a low-priority fallback for imports/manifests without shared names.
            if self.by_path[path]:
                scores[self.by_path[path][0]] += 0.001
        chosen, per_path = [], defaultdict(int)
        for chunk_id in sorted(scores, key=lambda c: (-scores[c], self.chunks[c]['path'], self.chunks[c]['start'])):
            path = self.chunks[chunk_id]['path']
            if per_path[path] < 2:
                chosen.append(chunk_id)
                per_path[path] += 1
            if len(chosen) >= limit:
                break
        return chosen

    def related(self, paths, limit=15):
        candidates = set()
        for path in paths:
            candidates.update(self.data["dependencies"].get(path, []))
            candidates.update(self.reverse.get(path, []))
        return [{"path": p, "symbols": [s["name"] for s in self.data["files"][p]["symbols"]][:12],
                 "chunks": self.by_path[p][:5]} for p in sorted(candidates)[:limit]]
