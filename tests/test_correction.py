"""纠错链：取代、恢复、双向追溯、策略约束。"""

from __future__ import annotations

import unittest

from knowledge_graph import ConflictError, Status, ValidationError

from _support import EngineTestCase


class SupersedeTests(EngineTestCase):
    def test_add_with_corrects_marks_fake_deprecated(self):
        self.add_fake_pair()
        fake = self.engine.get("fake-001")
        self.assertIs(fake.status, Status.DEPRECATED)
        self.assertEqual(fake.superseded_by, "sup-001")
        self.assertIn("sup-001", fake.related_ids)

    def test_fake_is_kept_not_deleted(self):
        self.add_fake_pair()
        self.assertIsNotNone(self.engine.get("fake-001"), "被取代的条目必须保留，否则纠错链断裂")

    def test_supplement_gets_backlink(self):
        self.add_fake_pair()
        self.assertEqual(self.engine.get("sup-001").corrects, "fake-001")

    def test_chain_is_bidirectional(self):
        self.add_fake_pair()
        chain = self.engine.correction_chain("fake-001")
        self.assertEqual([i.id for i in chain["corrected_by"]], ["sup-001"])
        self.assertEqual(chain["corrects"], [])

        reverse = self.engine.correction_chain("sup-001")
        self.assertEqual([i.id for i in reverse["corrects"]], ["fake-001"])

    def test_non_supersedable_type_is_rejected(self):
        self.add_science()
        self.add_supplement("sup-001", corrects=None)
        with self.assertRaises(ConflictError):
            self.engine.supersede("sci-001", "sup-001")

    def test_double_supersede_requires_force(self):
        self.add_fake_pair()
        self.add_supplement("sup-002", corrects=None)
        with self.assertRaises(ConflictError):
            self.engine.supersede("fake-001", "sup-002")

        self.engine.supersede("fake-001", "sup-002", force=True)
        self.assertEqual(self.engine.get("fake-001").superseded_by, "sup-002")
        self.assertIsNone(self.engine.get("sup-001").corrects, "旧补充条目的反向指针应被清理")

    def test_self_supersede_rejected(self):
        self.add_fake()
        with self.assertRaises(ValidationError):
            self.engine.supersede("fake-001", "fake-001")

    def test_supersede_missing_supplement_raises(self):
        self.add_fake()
        with self.assertRaises(Exception):
            self.engine.add("sup-001", "t", "c", "supplement", corrects="missing")

    def test_one_supplement_cannot_correct_two_fakes(self):
        self.add_fake("fake-001")
        self.add_fake("fake-002")
        self.add_supplement("sup-001", corrects="fake-001")
        with self.assertRaises(ConflictError):
            self.engine.supersede("fake-002", "sup-001", force=True)

    def test_supersede_is_audited(self):
        self.add_fake_pair()
        actions = [e["action"] for e in self.audit.get_history()]
        self.assertIn("supersede", actions)
        self.assertIn("deprecate", actions)


class RestoreTests(EngineTestCase):
    def test_restore_brings_fake_back_to_active(self):
        self.add_fake_pair()
        restored = self.engine.restore("fake-001")
        self.assertIs(restored.status, Status.ACTIVE)
        self.assertIsNone(restored.superseded_by)
        self.assertIsNone(self.engine.get("sup-001").corrects)

    def test_restore_non_superseded_raises(self):
        self.add_science()
        with self.assertRaises(ConflictError):
            self.engine.restore("sci-001")

    def test_restore_audited_as_unsupersede(self):
        self.add_fake_pair()
        self.engine.restore("fake-001")
        actions = [e["action"] for e in self.audit.get_history()]
        self.assertIn("unsupersede", actions)


class CorrectionsReportTests(EngineTestCase):
    def test_corrections_reports_healthy_chain(self):
        self.add_fake_pair()
        report = self.engine.corrections()
        self.assertEqual(len(report), 1)
        self.assertTrue(report[0]["supplement_exists"])
        self.assertTrue(report[0]["backlink_ok"])
        self.assertEqual(report[0]["fake_status"], "deprecated")

    def test_corrections_reports_broken_chain(self):
        self.add_fake_pair()
        self.store.delete("sup-001")  # 绕过引擎制造悬空
        report = self.engine.corrections()
        self.assertFalse(report[0]["supplement_exists"])
        self.assertEqual(self.engine.integrity()["dangling_superseded_by"], ["fake-001"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
