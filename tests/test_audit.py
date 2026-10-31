"""审计日志：返回副本、保留字段不可覆盖、JSONL 落盘。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from knowledge_graph import AuditLog


class AuditLogTests(unittest.TestCase):
    def test_history_returns_copies(self):
        log = AuditLog()
        log.log("create", "a-1", "创建")
        entry = log.get_history()[0]
        entry["action"] = "hacked"
        self.assertEqual(log.get_history()[0]["action"], "create")

    def test_entries_property_returns_copies(self):
        log = AuditLog()
        log.log("create", "a-1")
        log.entries[0]["action"] = "hacked"
        self.assertEqual(log.entries[0]["action"], "create")

    def test_reserved_fields_cannot_be_overridden(self):
        log = AuditLog()
        with self.assertLogs("knowledge_graph.audit", level="WARNING"):
            entry = log.log("create", "a-1", "d", timestamp="1999-01-01")
        self.assertNotEqual(entry["timestamp"], "1999-01-01")
        self.assertEqual(entry["action"], "create")
        self.assertEqual(log.get_history()[0]["item_id"], "a-1")

    def test_custom_fields_are_kept(self):
        log = AuditLog()
        entry = log.log("update", "a-1", changes={"title": "x"})
        self.assertEqual(entry["changes"], {"title": "x"})

    def test_history_filters(self):
        log = AuditLog()
        log.log("create", "a-1")
        log.log("create", "a-2")
        log.log("delete", "a-1")
        self.assertEqual(len(log.get_history(item_id="a-1")), 2)
        self.assertEqual(len(log.get_history(action="create")), 2)
        self.assertEqual(log.stats(), {"create": 2, "delete": 1})

    def test_sink_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            sink = Path(tmp) / "audit.jsonl"
            log = AuditLog(sink=sink)
            log.log("create", "a-1")
            log.log("delete", "a-1")
            lines = sink.read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line)["action"] for line in lines], ["create", "delete"])

    def test_export_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "export.json"
            log = AuditLog()
            log.log("create", "a-1")
            log.export(target)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))[0]["action"], "create")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
