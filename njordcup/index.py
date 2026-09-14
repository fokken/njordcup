"""Local metadata index. Python AST parsing; explicit lexical fallback elsewhere."""
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
IDENTIFIERS = re.compile(r"[A-Za-z_][A-Za-z_0-9]{2,}")


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


def parse_file(path, source, chunk_chars):
    lines = source.splitlines()
    symbols, imports, calls, notes = [], [], [], []
    parser = "lexical"
    if path.endswith(".py"):
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
    if parser == "lexical":
        notes.append("Symbols/imports/calls are heuristic; no semantic call graph")
        definition = re.compile(r"(?:\b(?:function|func|fn|def|class|interface)\s+(?:\([^)]*\)\s*)?)([A-Za-z_]\w*)")
        for i, line in enumerate(lines, 1):
            match = definition.search(line)
            if match:
                symbols.append({"name": match.group(1), "start": i, "end": i, "kind": "declaration"})
        imports = re.findall(r"(?:from\s+|require\s*\(|import\s*\(|import\s+|#include\s*)[\"'<]([^\"'>]+)", source)
        calls = re.findall(r"\b([A-Za-z_]\w*)\s*\(", source)
    boundaries = {s["start"] for s in symbols} | {s["end"] + 1 for s in symbols}
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


def build_index(sources, targets, previous=None, chunk_chars=10000):
    previous = previous or {}
    reusable = previous.get("files", {}) if previous.get("version") == 1 and previous.get("chunk_chars") == chunk_chars else {}
    files, reused = {}, 0
    manifest_dirs = {str(PurePosixPath(p).parent) for p in sources if PurePosixPath(p).name in MANIFESTS}
    components = defaultdict(list)
    for path, source in sorted(sources.items()):
        old = reusable.get(path)
        if old and old["hash"] == digest(source):
            metadata = dict(old)
            reused += 1
        else:
            metadata = parse_file(path, source, chunk_chars)
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
    return {"version": 1, "chunk_chars": chunk_chars, "files": files,
            "components": dict(components), "dependencies": dependencies, "targets": sorted(targets),
            "stats": {"files": len(files), "lines": sum(f["lines"] for f in files.values()),
                      "chunks": sum(len(f["chunks"]) for f in files.values()),
                      "symbols": sum(len(f["symbols"]) for f in files.values()),
                      "components": len(components), "reused_files": reused}}


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
        self.by_path = {p: [c["id"] for c in f["chunks"]] for p, f in data["files"].items()}
        self.reverse = defaultdict(set)
        for path, deps in data["dependencies"].items():
            for dep in deps:
                self.reverse[dep].add(path)
        for chunk_id, chunk in self.chunks.items():
            text = "\n".join(self.lines[chunk["path"]][chunk["start"] - 1:chunk["end"]])
            for term in set(IDENTIFIERS.findall(text + " " + chunk["path"])):
                self.terms[term.lower()].add(chunk_id)

    def entry(self, chunk_id):
        c = self.chunks[chunk_id]
        return {"id": chunk_id, "path": c["path"], "start": c["start"], "end": c["end"],
                "lines": "\n".join(f"{i}: {self.lines[c['path']][i - 1]}" for i in range(c["start"], c["end"] + 1))}

    def search(self, query, limit=10):
        scores = defaultdict(int)
        for term in set(IDENTIFIERS.findall(query)):
            for chunk_id in self.terms.get(term.lower(), ()):
                scores[chunk_id] += 1
        return sorted(scores, key=lambda c: (-scores[c], c))[:limit]

    def related(self, paths, limit=15):
        candidates = set()
        for path in paths:
            candidates.update(self.data["dependencies"].get(path, []))
            candidates.update(self.reverse.get(path, []))
        return [{"path": p, "symbols": [s["name"] for s in self.data["files"][p]["symbols"]][:12],
                 "chunks": self.by_path[p][:5]} for p in sorted(candidates)[:limit]]
