"""knowledge-graph-lite 命令行测试。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
from pathlib import Path

from knowledge_graph import utc_now
from kg_lite.cli import main


class LiteCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="kg-lite-")
        self.addCleanup(self._tmp.cleanup)
        self.data = str(Path(self._tmp.name) / "kb.json")

    def run_kg(self, *argv: str, data: str | None = None) -> tuple[int, str, str]:
        args = list(argv) + ["--data", data or self.data]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        return code, out.getvalue(), err.getvalue()

    def add(self, item_id: str, content_type: str = "news", **flags: str) -> None:
        argv = ["add", "--type", content_type, "--title", f"标题 {item_id}", "--id", item_id]
        for key, value in flags.items():
            argv += [f"--{key.replace('_', '-')}", value]
        code, _, err = self.run_kg(*argv)
        self.assertEqual(code, 0, err)

    def list_json(self, *flags: str) -> list[dict]:
        code, out, err = self.run_kg("list", "--json", *flags)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    # ---------------------------------------------------------------- init

    def test_init_creates_file(self):
        code, out, _ = self.run_kg("init")
        self.assertEqual(code, 0)
        self.assertTrue(Path(self.data).exists())
        self.assertIn("已初始化", out)

    def test_init_is_idempotent(self):
        self.run_kg("init")
        code, out, _ = self.run_kg("init")
        self.assertEqual(code, 0)
        self.assertIn("已存在", out)

    # ---------------------------------------------------------------- add/list

    def test_add_and_list_json(self):
        self.add("n1", tags="AI,模型")
        items = self.list_json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "n1")
        self.assertEqual(items[0]["tags"], ["AI", "模型"])
        self.assertIsNotNone(items[0]["expire_at"])

    def test_add_generates_id_when_omitted(self):
        code, out, err = self.run_kg("add", "--type", "science", "--title", "黑洞")
        self.assertEqual(code, 0, err)
        self.assertIn("已添加", out)
        self.assertEqual(len(self.list_json()), 1)

    def test_list_table_contains_title_and_type(self):
        self.add("n1")
        code, out, _ = self.run_kg("list")
        self.assertEqual(code, 0)
        self.assertIn("标题 n1", out)
        self.assertIn("news", out)

    def test_list_all_includes_hidden(self):
        self.add("f1", "fake_science")
        self.add("s1", "supplement", corrects="f1")
        self.assertEqual(
            [item["id"] for item in self.list_json()],
            ["s1"],
            "默认视图不应包含已下架的假科普",
        )
        self.assertEqual(len(self.list_json("--all")), 2)

    # ---------------------------------------------------------------- 纠错链

    def test_show_displays_chain(self):
        self.add("f1", "fake_science")
        self.add("s1", "supplement", corrects="f1")
        code, out, _ = self.run_kg("show", "f1")
        self.assertEqual(code, 0)
        self.assertIn("deprecated", out)
        self.assertIn("s1", out)

    def test_show_json_includes_chain(self):
        self.add("f1", "fake_science")
        self.add("s1", "supplement", corrects="f1")
        code, out, _ = self.run_kg("show", "f1", "--json")
        payload = json.loads(out)
        self.assertEqual(payload["status"], "deprecated")
        self.assertEqual(payload["chain"]["corrected_by"], ["s1"])

    # ---------------------------------------------------------------- 搜索/更新/删除

    def test_search(self):
        self.add("n1", content="量子计算取得突破")
        self.add("n2", content="另一条新闻")
        code, out, _ = self.run_kg("search", "量子")
        self.assertEqual(code, 0)
        self.assertIn("n1", out)
        self.assertNotIn("n2", out)

    def test_update_title(self):
        self.add("n1")
        code, _, err = self.run_kg("update", "n1", "--title", "新标题")
        self.assertEqual(code, 0, err)
        payload = json.loads(self.run_kg("show", "n1", "--json")[1])
        self.assertEqual(payload["title"], "新标题")

    def test_delete(self):
        self.add("n1")
        code, _, err = self.run_kg("delete", "n1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.list_json("--all"), [])
        code, _, _ = self.run_kg("delete", "n1")
        self.assertEqual(code, 1)

    # ---------------------------------------------------------------- 清理

    def test_cleanup_dry_run_then_real(self):
        old = (utc_now() - timedelta(days=30)).isoformat()
        self.add("n-old", created_at=old)
        code, out, _ = self.run_kg("cleanup", "--dry-run", "--json")
        self.assertEqual(code, 0)
        self.assertIn("n-old", json.loads(out)["deleted"])
        self.assertEqual(len(self.list_json("--all")), 1, "预演不应改数据")

        code, out, _ = self.run_kg("cleanup", "--json")
        self.assertEqual(code, 0)
        self.assertIn("n-old", json.loads(out)["deleted"])
        self.assertEqual(self.list_json("--all"), [])

    def test_purge_requires_confirmation(self):
        self.add("f1", "fake_science")
        self.add("s1", "supplement", corrects="f1")
        code, _, err = self.run_kg("purge")
        self.assertEqual(code, 2)
        self.assertIn("--yes", err)
        self.assertEqual(len(self.list_json("--all")), 2)

    def test_purge_with_yes(self):
        self.add("f1", "fake_science")
        self.add("s1", "supplement", corrects="f1")
        code, out, err = self.run_kg("purge", "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("f1", out)
        remaining = self.list_json("--all")
        self.assertEqual([item["id"] for item in remaining], ["s1"])
        self.assertIsNone(remaining[0]["corrects"])

    def test_purge_dry_run_needs_no_yes(self):
        self.add("f1", "fake_science")
        code, out, _ = self.run_kg("purge", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("将清除", out)

    # ---------------------------------------------------------------- 导入导出

    def test_export_then_restore_preserves_status_and_chain(self):
        self.add("f1", "fake_science")
        self.add("s1", "supplement", corrects="f1")
        export_path = str(Path(self._tmp.name) / "backup.json")
        code, _, err = self.run_kg("export", export_path)
        self.assertEqual(code, 0, err)

        other = str(Path(self._tmp.name) / "restored.json")
        code, out, err = self.run_kg("import", export_path, "--restore", data=other)
        self.assertEqual(code, 0, err)
        self.assertIn("已还原", out)

        code, out, _ = self.run_kg("list", "--all", "--json", data=other)
        items = {item["id"]: item for item in json.loads(out)}
        self.assertEqual(items["f1"]["status"], "deprecated")
        self.assertEqual(items["s1"]["corrects"], "f1")

    def test_import_rebuild_applies_policy(self):
        payload = [
            {
                "id": "n2",
                "title": "旧闻",
                "content": "正文",
                "content_type": "news",
                "created_at": (utc_now() - timedelta(days=30)).isoformat(),
            }
        ]
        source = Path(self._tmp.name) / "seed.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        code, _, err = self.run_kg("import", str(source))
        self.assertEqual(code, 0, err)
        items = self.list_json("--all")
        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0]["expire_at"], "重建导入应按策略补算过期时间")

    def test_import_jsonl(self):
        source = Path(self._tmp.name) / "seed.jsonl"
        lines = [
            {"id": "a1", "title": "甲", "content_type": "news"},
            {"id": "a2", "title": "乙", "content_type": "science"},
        ]
        source.write_text(
            "\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8"
        )
        code, _, err = self.run_kg("import", str(source))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.list_json()), 2)

    def test_import_duplicate_requires_skip_existing(self):
        source = Path(self._tmp.name) / "seed.json"
        source.write_text(
            json.dumps([{"id": "a1", "title": "甲", "content_type": "news"}]), encoding="utf-8"
        )
        self.run_kg("import", str(source))
        code, _, _ = self.run_kg("import", str(source))
        self.assertEqual(code, 1)
        code, out, err = self.run_kg("import", str(source), "--skip-existing")
        self.assertEqual(code, 0, err)
        self.assertIn("跳过", out)

    def test_import_dry_run_does_not_create_file(self):
        source = Path(self._tmp.name) / "seed.json"
        source.write_text(
            json.dumps([{"id": "a1", "title": "甲", "content_type": "news"}]), encoding="utf-8"
        )
        missing = str(Path(self._tmp.name) / "fresh.json")
        code, out, _ = self.run_kg("import", str(source), "--dry-run", data=missing)
        self.assertEqual(code, 0)
        self.assertIn("预演", out)
        self.assertFalse(Path(missing).exists())

    # ---------------------------------------------------------------- 搜索/标签/备份

    def test_search_multi_term_requires_all(self):
        self.add("n1", content="量子计算取得突破")
        self.add("n2", content="量子通信")
        code, out, _ = self.run_kg("search", "量子 计算", "--json")
        self.assertEqual([item["id"] for item in json.loads(out)], ["n1"])

    def test_search_any_term(self):
        self.add("n1", content="量子计算")
        self.add("n2", content="量子通信")
        code, out, _ = self.run_kg("search", "计算 通信", "--any", "--json")
        self.assertEqual({item["id"] for item in json.loads(out)}, {"n1", "n2"})

    def test_list_tag_filter(self):
        self.add("a", tags="AI,模型")
        self.add("b", tags="AI")
        code, out, _ = self.run_kg("list", "--tag", "AI", "--json")
        self.assertEqual({item["id"] for item in json.loads(out)}, {"a", "b"})

        code, out, _ = self.run_kg("list", "--tag", "AI", "--tag", "模型", "--json")
        self.assertEqual([item["id"] for item in json.loads(out)], ["a"])

        code, out, _ = self.run_kg(
            "list", "--tag", "AI", "--tag", "模型", "--any-tag", "--json"
        )
        self.assertEqual({item["id"] for item in json.loads(out)}, {"a", "b"})

    def test_tags_command(self):
        self.add("a", tags="AI,模型")
        self.add("b", tags="AI")
        code, out, _ = self.run_kg("tags", "--json")
        self.assertEqual(json.loads(out), {"AI": 2, "模型": 1})

    def test_auto_backup_before_delete(self):
        self.add("n1")
        code, _, err = self.run_kg("delete", "n1")
        self.assertIn("已备份", err)
        backups = list((Path(self.data).parent / "backups").glob("*.json"))
        self.assertEqual(len(backups), 1)

    def test_no_backup_flag(self):
        self.add("n1")
        code, _, err = self.run_kg("delete", "n1", "--no-backup")
        self.assertNotIn("已备份", err)
        self.assertFalse((Path(self.data).parent / "backups").exists())

    def test_backup_rotation(self):
        self.add("n1")
        for _ in range(3):
            code, _, err = self.run_kg("backup", "--keep", "2")
            self.assertEqual(code, 0, err)
        backups = list((Path(self.data).parent / "backups").glob("*.json"))
        self.assertEqual(len(backups), 2)

    # ---------------------------------------------------------------- 错误处理

    def test_missing_data_file_reports_error(self):
        missing = str(Path(self._tmp.name) / "nope.json")
        code, _, err = self.run_kg("list", data=missing)
        self.assertEqual(code, 1)
        self.assertIn("数据文件不存在", err)

    def test_global_data_flag_before_subcommand(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--data", self.data, "init"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue(Path(self.data).exists())

    def test_json_flag_before_subcommand(self):
        self.add("n1")
        out, _ = io.StringIO(), io.StringIO()
        with redirect_stdout(out):
            code = main(["--json", "list", "--data", self.data])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())[0]["id"], "n1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
