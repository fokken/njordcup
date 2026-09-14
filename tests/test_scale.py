from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from njordcup.agent import review
from njordcup.flyover import flyover
from njordcup.index import CodeIndex, affected_files, build_index
from njordcup.memory import record_review
from njordcup.provider import ReviewError
from njordcup.repository import discover


class AutomaticProvider:
    model = "test"
    base_url = "http://localhost:8000/v1"
    output_mode = "json_schema"

    def __init__(self, max_calls=10000):
        self.calls, self.max_calls = 0, max_calls
        self.payloads = []

    def ask(self, instructions, payload, schema):
        if self.calls >= self.max_calls:
            raise ReviewError("API call budget exhausted")
        self.calls += 1
        self.payloads.append(deepcopy(payload))
        if "areas" in schema["properties"]:
            return {"summary": "Component summary", "tech_stack": ["Python"], "dependencies": [],
                    "areas": [{"title": "Input", "reason": "Entry points", "features": ["requests"],
                               "attack_surfaces": ["inputs"], "paths": payload["target_paths"]}], "unknowns": []}
        result = {"findings": [], "context_paths": []}
        if "assessments" in schema["properties"]:
            result["assessments"] = [{"result_id": s["id"], "status": "not_confirmed", "reason": "Mock counterevidence",
                                      "finding_path": "", "finding_line": 0} for s in payload["sarif_candidates"]]
        return result


class ScaleTests(unittest.TestCase):
    def test_files_over_old_60kb_limit_are_indexed(self):
        source = "".join(f"def function_{i}(value):\n    return value\n" for i in range(2000))
        self.assertGreater(len(source), 60000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.py").write_text(source)
            sources, targets, skipped = discover(root)
            self.assertEqual(targets, ["large.py"])
            self.assertEqual(skipped, [])
            index = build_index(sources, targets)
            self.assertEqual(index["stats"]["symbols"], 2000)
            self.assertGreater(index["stats"]["chunks"], 1)

    def test_index_covers_250k_lines_and_reuses_unchanged_metadata(self):
        block = "def handler(value):\n    return value\n" + "# padding\n" * 998
        sources = {f"modules/m{i}/app.py": block for i in range(250)}
        index = build_index(sources, list(sources))
        self.assertEqual(index["stats"]["lines"], 250000)
        self.assertEqual(index["stats"]["components"], 250)
        for file in index["files"].values():
            expected = 1
            for chunk in file["chunks"]:
                self.assertEqual(chunk["start"], expected)
                expected = chunk["end"] + 1
            self.assertEqual(expected, 1001)
        reused = build_index(sources, list(sources), index)
        self.assertEqual(reused["stats"]["reused_files"], 250)

    def test_python_symbols_and_dependency_invalidation(self):
        sources = {"services/a/main.py": "from services.b.util import sanitize\ndef route(x):\n    return sanitize(x)\n",
                   "services/b/util.py": "def sanitize(x):\n    return x\n", "services/c/main.py": "def untouched():\n    return 1\n"}
        old = build_index(sources, list(sources))
        self.assertIn("services/b/util.py", old["dependencies"]["services/a/main.py"])
        changed = {**sources, "services/b/util.py": "def sanitize(x):\n    return str(x)\n"}
        new = build_index(changed, list(changed), old)
        self.assertEqual(affected_files(old, new), {"services/a/main.py", "services/b/util.py"})
        lookup = CodeIndex(old, sources)
        self.assertTrue(lookup.search("sanitize"))

    def test_large_file_review_resumes_verified_chunks(self):
        source = "".join(f"def f{i}(request):\n    return request\n\n" for i in range(120))
        sources = {"large.py": source}
        index = build_index(sources, list(sources), chunk_chars=800)
        checkpoints = []
        first = review(sources, list(sources), [], AutomaticProvider(2), batch_chars=1200,
                       repository_index=index, checkpoint=lambda r: checkpoints.append(deepcopy(r)))
        self.assertEqual(first["status"], "incomplete")
        self.assertGreater(first["coverage"]["chunks_reviewed"], 0)
        done = {c for c, r in first["chunk_results"].items() if r["status"] == "complete"}
        provider = AutomaticProvider()
        final = review(sources, list(sources), [], provider, batch_chars=1200, repository_index=index, previous=first)
        self.assertEqual(final["status"], "complete")
        sent = {c for p in provider.payloads for c in p["target_chunks"]}
        self.assertFalse(sent & done)
        self.assertEqual(final["coverage"]["lines_reviewed"], len(source.splitlines()))
        self.assertEqual(final["coverage"]["symbols_reviewed"], 120)

    def test_component_mapping_resumes_and_preserves_unaffected_review(self):
        sources = {f"modules/m{i}/app.py": f"def f{i}():\n    return {i}\n" for i in range(12)}
        index = build_index(sources, list(sources))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            first, _ = flyover(sources, list(sources), AutomaticProvider(2), path, repository_index=index)
            self.assertEqual(len(first["overview"]["areas"]), 12)
            self.assertEqual(first["coverage"]["pages_pending"], 10)
            provider = AutomaticProvider()
            complete, _ = flyover(sources, list(sources), provider, path, repository_index=index)
            self.assertEqual(provider.calls, 10)
            self.assertEqual(complete["coverage"]["pages_pending"], 0)
            keep = next(i for i, a in enumerate(complete["overview"]["areas"], 1) if a["component"] == "modules/m1")
            record_review(path, complete, keep, {"status": "complete", "findings": [], "read_dependencies": {}})
            sources["modules/m0/app.py"] += "# changed\n"
            new = build_index(sources, list(sources), index)
            provider = AutomaticProvider()
            updated, _ = flyover(sources, list(sources), provider, path, repository_index=new)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(len(updated["reviews"]), 1)
            self.assertEqual(len(updated["archives"]), 1)

    def test_multi_round_search_can_find_file_not_in_initial_catalog(self):
        sources = {"app.py": "handle(user)\n", "deep/auth.py": "def authorize(user):\n    return user.allowed\n"}
        class SearchProvider(AutomaticProvider):
            def ask(self, instructions, payload, schema):
                self.payloads.append(deepcopy(payload))
                self.calls += 1
                return {"findings": [], "context_paths": ["search:authorize"] if self.calls == 1 else ["deep/auth.py:L2"] if self.calls == 2 else []}
        provider = SearchProvider()
        result = review(sources, ["app.py"], [], provider)
        self.assertEqual(provider.calls, 3)
        self.assertEqual(result["status"], "complete")
        self.assertIn("deep/auth.py", result["read_dependencies"])
