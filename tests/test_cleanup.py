"""清理流程：时间到期、动作分派、幂等、批量清除。"""

from __future__ import annotations

import unittest

from knowledge_graph import (
    Action,
    AuditLog,
    Engine,
    MemoryStore,
    Retention,
    RetentionKind,
    RetentionPolicy,
    Status,
    ValidationError,
)
from knowledge_graph.models import utc_now

from _support import FIXED, EngineTestCase, custom_book


def engine_with(**overrides):
    """用自定义策略表建一个独立引擎（不污染用例里的默认引擎）。"""
    store = MemoryStore()
    audit = AuditLog()
    return Engine(store, policies=custom_book(**overrides), audit=audit), store, audit


class ExpiryTests(EngineTestCase):
    def test_expired_news_is_deleted(self):
        self.add_news(days_old=8)
        report = self.engine.cleanup(now=FIXED)
        self.assertEqual(report.deleted, ["news-001"])
        self.assertIsNone(self.engine.get("news-001"))

    def test_fresh_news_is_kept(self):
        self.add_news(days_old=6)
        report = self.engine.cleanup(now=FIXED)
        self.assertEqual(report.handled, 0)
        self.assertIsNotNone(self.engine.get("news-001"))

    def test_exactly_due_counts_as_expired(self):
        self.add_news(days_old=7)  # expire_at 恰好等于 FIXED
        self.assertEqual(self.engine.cleanup(now=FIXED).deleted, ["news-001"])

    def test_science_never_expires(self):
        self.add_science(days_old=3650)
        self.assertEqual(self.engine.cleanup(now=FIXED).handled, 0)
        self.assertIsNotNone(self.engine.get("sci-001"))

    def test_unknown_type_never_expires(self):
        self.engine.add("misc-001", "t", "c", "misc", created_at=FIXED)
        self.engine.cleanup(now=utc_now())
        self.assertIsNotNone(self.engine.get("misc-001"))

    def test_superseded_fake_survives_cleanup(self):
        self.add_fake_pair()
        self.engine.cleanup(now=FIXED)
        fake = self.engine.get("fake-001")
        self.assertIsNotNone(fake, "纠错链不能因为清理而消失")
        self.assertIs(fake.status, Status.DEPRECATED)


class ReportTests(EngineTestCase):
    def test_report_separates_buckets(self):
        self.add_news("news-old", days_old=9)      # 删除
        self.add_news("news-new", days_old=1)      # 保留
        self.add_fake_pair()                        # 已 deprecated，不参与
        report = self.engine.cleanup(now=FIXED)
        self.assertEqual(report.deleted, ["news-old"])
        self.assertEqual(report.archived, [])
        self.assertEqual(report.deprecated, [])
        self.assertEqual(report.total_active, 2)   # news-new + sup-001
        self.assertEqual(report.as_dict()["handled"], 1)

    def test_dry_run_does_not_mutate(self):
        self.add_news(days_old=8)
        report = self.engine.cleanup(now=FIXED, dry_run=True)
        self.assertEqual(report.deleted, ["news-001"])
        self.assertTrue(report.dry_run)
        self.assertIsNotNone(self.engine.get("news-001"))
        self.assertIs(self.engine.get("news-001").status, Status.ACTIVE)

    def test_cleanup_is_idempotent(self):
        self.add_news(days_old=9)
        self.engine.cleanup(now=FIXED)
        second = self.engine.cleanup(now=FIXED)
        self.assertEqual(second.handled, 0)

    def test_cleanup_audits_each_action(self):
        self.add_news(days_old=9)
        self.engine.cleanup(now=FIXED)
        actions = [e["action"] for e in self.audit.get_history()]
        self.assertIn("delete", actions)

    def test_cleanup_delete_clears_inbound_corrects(self):
        # 正常 API 不会产生「news 被 supplement 纠正」，这里手工构造脏数据验证防御逻辑
        self.add_news(days_old=9)
        supplement = self.engine.add("sup-001", "t", "c", "supplement", created_at=FIXED)
        supplement.corrects = "news-001"
        self.store.save(supplement)
        self.engine.cleanup(now=FIXED)
        self.assertIsNone(self.store.get("sup-001").corrects)
        self.assertEqual(self.engine.integrity()["dangling_corrects"], [])


class ActionTests(unittest.TestCase):
    def test_archive_action_keeps_item_but_hides_it(self):
        book_policy = RetentionPolicy(
            "news", Retention(RetentionKind.FIXED_DAYS, days=7), Action.ARCHIVE, priority=10
        )
        engine, store, _ = engine_with(news=book_policy)
        engine.add("news-001", "t", "c", "news", created_at=FIXED)
        report = engine.cleanup(now=FIXED.replace(day=9))
        self.assertEqual(report.archived, ["news-001"])
        item = store.get("news-001")
        self.assertIsNotNone(item)
        self.assertIs(item.status, Status.ARCHIVED)

    def test_deprecate_action_keeps_item_visible_only_with_include_hidden(self):
        policy = RetentionPolicy(
            "news", Retention(RetentionKind.FIXED_DAYS, days=2), Action.DEPRECATE, priority=10
        )
        engine, store, _ = engine_with(news=policy)
        engine.add("news-001", "t", "c", "news", created_at=FIXED)
        engine.cleanup(now=FIXED.replace(day=9))
        self.assertIs(store.get("news-001").status, Status.DEPRECATED)
        self.assertEqual(engine.list_active(), [])

    def test_none_action_marks_expired_but_keeps(self):
        policy = RetentionPolicy(
            "news", Retention(RetentionKind.FIXED_DAYS, days=2), Action.NONE, priority=10
        )
        engine, store, _ = engine_with(news=policy)
        engine.add("news-001", "t", "c", "news", created_at=FIXED)
        report = engine.cleanup(now=FIXED.replace(day=9))
        self.assertEqual(report.expired, ["news-001"])
        self.assertIs(store.get("news-001").status, Status.EXPIRED)


class PurgeTests(EngineTestCase):
    def test_purge_deprecated_removes_and_unlinks(self):
        self.add_fake_pair()
        purged = self.engine.purge()
        self.assertEqual(purged, ["fake-001"])
        self.assertIsNone(self.engine.get("fake-001"))
        self.assertIsNone(self.engine.get("sup-001").corrects)
        self.assertEqual(self.engine.integrity()["dangling_corrects"], [])

    def test_purge_dry_run_keeps_items(self):
        self.add_fake_pair()
        preview = self.engine.purge(dry_run=True)
        self.assertEqual(preview, ["fake-001"])
        self.assertIsNotNone(self.engine.get("fake-001"))

    def test_purge_active_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.engine.purge(status=Status.ACTIVE)

    def test_purge_respects_age_filter(self):
        self.add_fake_pair()
        self.assertEqual(self.engine.purge(older_than_days=10_000), [])

    def test_purge_refuses_to_break_chain_without_force(self):
        self.add_fake_pair()
        self.engine.update("sup-001", status="archived")
        with self.assertRaises(Exception) as ctx:
            self.engine.purge(status=Status.ARCHIVED)
        self.assertIn("force=True", str(ctx.exception))

    def test_purge_force_restores_referencing_item(self):
        self.add_fake_pair()
        self.engine.update("sup-001", status="archived")
        self.engine.purge(status=Status.ARCHIVED, force=True)
        self.assertIsNone(self.engine.get("sup-001"))
        fake = self.engine.get("fake-001")
        self.assertIs(fake.status, Status.ACTIVE)
        self.assertIsNone(fake.superseded_by)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
