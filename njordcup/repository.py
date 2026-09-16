"""Read-only source discovery. Never executes repository code."""
import fnmatch
import os
from pathlib import Path
import re
import subprocess

IGNORED = {".git", ".agents", ".codex", ".venv", "venv", "node_modules", "vendor", "dist", "build", "__pycache__", ".security-review-cache", ".security-review", ".njordcup", ".njordcup-cache"}
LOCKS = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
SENSITIVE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc", ".git-credentials"}


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("Git operation failed; check repository and --base revision")
    return result.stdout.decode("utf-8", errors="surrogateescape")


def discover(root: Path, base=None, excludes=(), max_bytes=2000000, includes=()):
    """Snapshot eligible files; Git repositories honor Git ignore rules."""
    skipped = []
    try:
        git(root, "rev-parse", "--show-toplevel")
        names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0")
    except ValueError:
        if base:
            raise ValueError("--base requires a Git repository")
        names = []
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED and not (Path(directory) / d).is_symlink())
            names.extend(str((Path(directory) / f).relative_to(root)) for f in files)
    selected = None
    if base:
        revision = git(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}").strip()
        selected = set(git(root, "diff", "--name-only", "-z", "--diff-filter=ACMRT", revision, "--").split("\0"))
        selected.update(git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    sources = {}
    for name in sorted(set(names) - {""}):
        path = root / name
        parts = Path(name).parts
        reason = None
        if any(p in IGNORED for p in parts) or any(fnmatch.fnmatch(name, p) for p in excludes):
            reason = "excluded"
        elif includes and not any(fnmatch.fnmatch(name, p) for p in includes):
            reason = "outside include patterns"
        elif path.name.startswith(".env") or path.name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
            reason = "sensitive file name/type"
        elif path.name in LOCKS or path.suffix.lower() in {".sarif", ".swp", ".swo"}:
            reason = "lockfile or generated artifact"
        elif any((root.joinpath(*parts[:i])).is_symlink() for i in range(1, len(parts) + 1)) or not path.resolve().is_relative_to(root):
            reason = "symlink or outside repository"
        elif not path.is_file():
            reason = "not a regular file"
        else:
            try:
                with path.open("rb") as handle:
                    raw = handle.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    reason = "file size limit"
                elif any(byte < 32 and byte not in {9, 10, 12, 13} for byte in raw):
                    reason = "binary file"
                else:
                    sources[name] = raw.decode("utf-8")
            except (OSError, UnicodeError):
                reason = "unreadable or non-UTF-8"
        if reason:
            skipped.append({"path": name, "reason": reason})
    targets = [p for p in sources if selected is None or p in selected]
    # Cheap prioritization influences order, never silently filters targets.
    targets.sort(key=lambda p: (-len(re.findall(r"auth|execute|query|subprocess|upload|password|token|request", sources[p], re.I)), p))
    return sources, targets, skipped


def numbered(path, source):
    return {"path": path, "lines": "\n".join(f"{i}: {line}" for i, line in enumerate(source.splitlines(), 1))}
