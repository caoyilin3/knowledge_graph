"""存储层：内存实现、JSON 原子写入、损坏文件必须报错而不是静默清空。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from knowledge_graph import ContentItem, JsonStore, MemoryStore, Status, StoreError, ValidationError


def item(item_id: str = "news-001", content_type: str = "news") -> ContentItem:
    return ContentItem(
        id=item_id,
        title="标题",
        content="正文",
        content_type=content_type,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore()

    def test_save_and_get(self):
        self.store.save(item())
        self.assertEqual(self.store.get("news-001").title, "标题")
        self.assertIsNone(self.store.get("missing"))

    def test_delete_returns_whether_removed(self):
        self.store.save(item())
        self.assertTrue(self.store.delete("news-001"))
        self.assertFalse(self.store.delete("news-001"))

    def test_query_by_status_accepts_string(self):
        self.store.save(item())
        deprecated = item("old-001")
        deprecated.status = Status.DEPRECATED
        self.store.save(deprecated)
        self.assertEqual(len(self.store.query(status="deprecated")), 1)
        self.assertEqual(self.store.count(status=Status.ACTIVE), 1)

    def test_query_unknown_field_raises(self):
        with self.assertRaises(ValidationError):
            self.store.query(no_such_field=1)

    def test_len(self):
        self.store.save(item())
        self.assertEqual(len(self.store), 1)


class JsonStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="kg-store-")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "kb.json"

    def test_auto_flush_persists_without_explicit_flush(self):
        JsonStore(self.path).save(item())
        reopened = JsonStore(self.path)
        self.assertEqual(reopened.get("news-001").content, "正文")

    def test_manual_flush_mode_batches_writes(self):
        store = JsonStore(self.path, auto_flush=False)
        store.save(item())
        self.assertFalse(self.path.exists(), "auto_flush=False 时不应立即写盘")
        store.flush()
        self.assertTrue(self.path.exists())

    def test_delete_is_persisted(self):
        store = JsonStore(self.path)
        store.save(item())
        store.delete("news-001")
        self.assertIsNone(JsonStore(self.path).get("news-001"))

    def test_file_is_valid_utf8_json_without_ascii_escapes(self):
        JsonStore(self.path).save(item())
        raw = self.path.read_text(encoding="utf-8")
        self.assertIn("标题", raw)
        payload = json.loads(raw)
        self.assertEqual(payload["schema_version"], 2)

    def test_v1_flat_format_is_still_loadable(self):
        legacy = {"news-001": item().to_dict()}
        self.path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self.assertIsNotNone(JsonStore(self.path).get("news-001"))

    def test_corrupt_file_raises_instead_of_silently_empty(self):
        self.path.write_text("{这不是 JSON", encoding="utf-8")
        with self.assertRaises(StoreError):
            JsonStore(self.path)

    def test_future_schema_version_is_rejected(self):
        self.path.write_text(
            json.dumps({"schema_version": 99, "items": {}}), encoding="utf-8"
        )
        with self.assertRaises(StoreError):
            JsonStore(self.path)

    def test_invalid_schema_version_type_is_rejected(self):
        self.path.write_text(
            json.dumps({"schema_version": "abc", "items": {}}), encoding="utf-8"
        )
        with self.assertRaises(StoreError):
            JsonStore(self.path)

    def test_non_serializable_metadata_does_not_write_partial_file(self):
        store = JsonStore(self.path)
        bad = item()
        bad.metadata = {"bad": object()}
        with self.assertRaises(StoreError):
            store.save(bad)
        self.assertFalse(self.path.exists(), "写入失败时不应留下半个文件")
        leftovers = [p.name for p in self.path.parent.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_no_temp_files_left_after_write(self):
        store = JsonStore(self.path)
        for index in range(3):
            store.save(item(f"news-{index:03d}"))
        leftovers = [p.name for p in self.path.parent.iterdir() if p.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_missing_file_is_empty_store(self):
        store = JsonStore(self.path)
        self.assertEqual(len(store), 0)
        self.assertTrue(store.flush())

    def test_nested_directory_is_created(self):
        nested = Path(self._tmp.name) / "a" / "b" / "kb.json"
        JsonStore(nested).save(item())
        self.assertTrue(nested.exists())

    def test_auto_flush_error_propagates(self):
        # 目录被占用（同名文件）时，写入应抛 StoreError 而不是静默失败
        blocker = Path(self._tmp.name) / "blocked"
        blocker.write_text("x", encoding="utf-8")
        store = JsonStore(blocker / "kb.json")
        with self.assertRaises(StoreError):
            store.save(item())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
