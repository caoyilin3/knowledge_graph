"""命令行入口的冒烟测试。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path

from knowledge_graph import Engine, JsonStore, utc_now
from knowledge_graph.__main__ import main


class CliTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_demo_runs_end_to_end(self):
        code, output = self.run_cli(["demo"])
        self.assertEqual(code, 0)
        self.assertIn("纠错链", output)
        self.assertIn("幂等", output)

    def test_inspect_prints_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kb.json"
            Engine(JsonStore(path)).add("news-001", "t", "c", "news")
            code, output = self.run_cli(["inspect", str(path)])
            self.assertEqual(code, 0)
            stats = json.loads(output)
            self.assertEqual(stats["total"], 1)

    def test_cleanup_dry_run_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kb.json"
            engine = Engine(JsonStore(path))
            engine.add("news-001", "t", "c", "news", created_at=utc_now() - timedelta(days=30))
            code, output = self.run_cli(["cleanup", str(path), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertIn('"dry_run": true', output)
            self.assertIsNotNone(Engine(JsonStore(path)).get("news-001"))

    def test_inspect_missing_file_reports_error(self):
        missing = Path(tempfile.gettempdir()) / "kg-does-not-exist.json"
        error = io.StringIO()
        with redirect_stderr(error):
            code = main(["inspect", str(missing)])
        self.assertEqual(code, 1)
        self.assertIn("文件不存在", error.getvalue())

    def test_inspect_corrupt_file_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{这不是 JSON", encoding="utf-8")
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(["inspect", str(path)])
            self.assertEqual(code, 1)
            self.assertIn("合法 JSON", error.getvalue())

    def test_inspect_directory_reports_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(["inspect", tmp])
            self.assertEqual(code, 1)
            self.assertIn("目录", error.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
