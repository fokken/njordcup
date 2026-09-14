"""Synthetic local indexing benchmark, not an LLM detection-quality benchmark.

Run separately per size for meaningful Linux process peak RSS:
    python3 benchmarks/scale.py --lines 250000
"""
import argparse
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from njordcup.index import CodeIndex, build_index
from njordcup.memory import save_memory
from njordcup.repository import discover


def generated_file(number):
    return "".join(f"def handler_{number}_{i}(request):\n"
                   "    user = request.get('user')\n"
                   "    payload = request.get('payload')\n"
                   "    allowed = user is not None\n"
                   "    name = str(payload)\n"
                   "    result = name.strip()\n"
                   "    audit = bool(allowed)\n"
                   "    status = 200 if audit else 403\n"
                   "    response = {'status': status, 'value': result}\n"
                   "    return response\n" for i in range(100))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, choices=[50000, 150000, 250000], default=250000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for i in range(args.lines // 1000):
            path = root / "modules" / f"service{i // 5}" / f"routes{i}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generated_file(i))
        start = time.perf_counter()
        sources, targets, skipped = discover(root)
        discovered = time.perf_counter()
        index = build_index(sources, targets)
        indexed = time.perf_counter()
        lookup = CodeIndex(index, sources)
        searched = lookup.search("handler_0_0")
        ready = time.perf_counter()
        index_path = root / ".njordcup" / "memory.index.json"
        save_memory(index_path, index)
        saved = time.perf_counter()
        incremental = build_index(sources, targets, index)
        end = time.perf_counter()
        assert index["stats"]["lines"] == args.lines and searched and not skipped
        print(json.dumps({"kind": "synthetic_index_only", "stats": index["stats"],
                          "timing_seconds": {"discovery": round(discovered - start, 3), "index": round(indexed - discovered, 3),
                                             "retrieval_index": round(ready - indexed, 3), "save": round(saved - ready, 3),
                                             "incremental_index": round(end - saved, 3)},
                          "index_bytes": index_path.stat().st_size, "source_bytes": sum(len(s.encode()) for s in sources.values()),
                          "peak_rss_mib_linux": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                          "reused_files": incremental["stats"]["reused_files"],
                          "model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
