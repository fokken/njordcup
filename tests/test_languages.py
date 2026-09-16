import os
from pathlib import Path
import tempfile
import unittest

from njordcup.agent import review
from njordcup.flyover import flyover, read_memory
from njordcup.index import build_index, CodeIndex
from njordcup.provider import ReviewError
from njordcup.repository import discover
from njordcup.sarif import load_sarif
from test_scale import AutomaticProvider
from test_sarif import document, location
import json


class LanguageAgnosticTests(unittest.TestCase):
    def test_unknown_extensions_and_extensionless_text_are_eligible(self):
        files = {"app.swift": "func serve() {}\n", "app.ex": "defmodule App do\nend\n",
                 "app.lua": "local message = 'hello'\n", "app.custom": "安全检查(value)\n",
                 "runner": "#!/usr/bin/env custom\nserve\n", "README.md": "Project documentation\n"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, source in files.items():
                (root / name).write_text(source)
            sources, targets, skipped = discover(root)
            self.assertEqual(sources, files)
            self.assertEqual(set(targets), set(files))
            self.assertEqual(skipped, [])

    def test_sensitive_binary_nonutf8_and_special_files_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in [".env", "private.pem", "id_ed25519", "scan.sarif"]:
                (root / name).write_text("text")
            (root / "binary.custom").write_bytes(b"abc\x01def")
            (root / "invalid.custom").write_bytes(b"\xff\xfe")
            (root / "link.custom").symlink_to(root / ".env")
            os.mkfifo(root / "pipe")
            sources, targets, skipped = discover(root)
            self.assertEqual(sources, {})
            self.assertEqual(len(skipped), 8)

    def test_include_filters_and_exclude_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("app.swift", "test.swift", "app.lua"):
                (root / name).write_text("source")
            _, targets, _ = discover(root, includes=["*.swift"], excludes=["test.*"])
            self.assertEqual(targets, ["app.swift"])

    def test_text_mode_covers_lines_and_searches_unicode_without_parsing(self):
        sources = {"app.custom": "安全检查(value)\n\n" * 100, "app.py": "def known():\n    return 1\n"}
        index = build_index(sources, list(sources), index_mode="text", chunk_chars=300)
        self.assertEqual(index["stats"]["indexing_methods"]["text"], 2)
        self.assertEqual(index["stats"]["symbols"], 0)
        lookup = CodeIndex(index, sources)
        self.assertTrue(lookup.search("安全检查"))
        result = review(sources, list(sources), [], AutomaticProvider(), repository_index=index, batch_chars=1200)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["coverage"]["lines_reviewed"], 202)
        self.assertEqual(result["coverage"]["indexing_methods"]["text"], 2)

    def test_changing_mode_invalidates_metadata_and_flyover_memory(self):
        sources = {"app.py": "def known():\n    return 1\n"}
        auto = build_index(sources, list(sources))
        text = build_index(sources, list(sources), previous=auto, index_mode="text")
        self.assertEqual(text["stats"]["reused_files"], 0)
        self.assertEqual(auto["stats"]["symbols"], 1)
        with tempfile.TemporaryDirectory() as tmp:
            memory = Path(tmp) / "memory.json"
            flyover(sources, list(sources), AutomaticProvider(), memory, repository_index=auto)
            with self.assertRaisesRegex(ReviewError, "Index mode changed"):
                read_memory(memory, sources, list(sources), AutomaticProvider(), index_mode="text")
            changed, reused = flyover(sources, list(sources), AutomaticProvider(), memory, repository_index=text)
            self.assertFalse(reused)
            self.assertEqual(changed["index_mode"], "text")

    def test_sarif_locations_work_for_previously_unsupported_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.swift").write_text("func serve() {}\n")
            sarif = root / "scan.sarif"
            sarif.write_text(json.dumps(document([{"ruleId": "swift.rule", "locations": [location("app.swift")]}])))
            sources, _, _ = discover(root)
            seed = load_sarif(sarif, root, sources)["candidates"][0]
            self.assertEqual(seed["location_status"], "resolved")
