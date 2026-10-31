"""引擎主流程：新增、读取、更新、删除、可见性与统计。"""

from __future__ import annotations

import unittest
from datetime import timedelta

from knowledge_graph import ConflictError, NotFoundError, Status, ValidationError

from _support import FIXED, EngineTestCase


class AddTests(EngineTestCase):
    def test_news_gets_seven_day_expiry(self):
        item = self.add_news()
        self.assertIsNotNone(item.expire_at)
        self.assertEqual(item.expire_at - item.created_at, timedelta(days=7))

    def test_science_has_no_expiry(self):
        self.assertIsNone(self.add_science().expire_at)

    def test_duplicate_id_is_rejected(self):
        self.add_news()
        with self.assertRaises(ConflictError):
            self.add_news()

    def test_overwrite_allows_replacement(self):
        self.add_news()
        replaced = self.engine.add(
            "news-001", "新标题", "新正文", "news", created_at=FIXED, overwrite=True
        )
        self.assertEqual(replaced.title, "新标题")
        self.assertEqual(self.engine.get("news-001").title, "新标题")

    def test_overwrite_chain_participant_is_rejected(self):
        self.add_fake_pair()
        for item_id, content_type in (("fake-001", "fake_science"), ("sup-001", "supplement")):
            with self.subTest(item_id=item_id), self.assertRaises(ConflictError):
                self.engine.add(
                    item_id, "t", "c", content_type, created_at=FIXED, overwrite=True
                )

    def test_metadata_must_be_serializable_at_add_time(self):
        with self.assertRaises(ValidationError):
            self.engine.add("misc-001", "t", "c", "news", metadata={"bad": object()})

    def test_unknown_content_type_gets_permanent_default(self):
        item = self.engine.add("misc-001", "t", "c", "misc", created_at=FIXED)
        self.assertIsNone(item.expire_at)
        self.assertIs(item.status, Status.ACTIVE)

    def test_create_is_audited(self):
        self.add_news()
        actions = [e["action"] for e in self.audit.get_history()]
        self.assertEqual(actions, ["create"])


class ReadTests(EngineTestCase):
    def test_get_missing_returns_none(self):
        self.assertIsNone(self.engine.get("nope"))

    def test_require_missing_raises(self):
        with self.assertRaises(NotFoundError):
            self.engine.delete("nope")

    def test_list_active_sorted_desc_by_created_at(self):
        self.add_news("news-a", days_old=3)
        self.add_news("news-b", days_old=1)
        ids = [i.id for i in self.engine.list_active()]
        self.assertEqual(ids, ["news-b", "news-a"])

    def test_list_active_excludes_deprecated(self):
        self.add_fake_pair()
        self.assertEqual(self.engine.list_active("fake_science"), [])
        self.assertEqual(len(self.engine.list_by_type("fake_science", include_hidden=True)), 1)

    def test_search_matches_title_content_and_tags(self):
        self.engine.add("news-001", "量子计算突破", "正文", "news", tags=["芯片"], created_at=FIXED)
        self.assertEqual([i.id for i in self.engine.search("量子")], ["news-001"])
        self.assertEqual([i.id for i in self.engine.search("芯片")], ["news-001"])
        self.assertEqual([i.id for i in self.engine.search("不存在")], [])

    def test_search_hides_deprecated_by_default(self):
        self.add_fake_pair()
        self.assertEqual(self.engine.search("杀灭癌细胞"), [])
        self.assertEqual(len(self.engine.search("杀灭癌细胞", include_hidden=True)), 1)


class UpdateTests(EngineTestCase):
    def test_update_title_bumps_revision_and_audits(self):
        self.add_news()
        item = self.engine.update("news-001", title="改过的标题")
        self.assertEqual(item.title, "改过的标题")
        self.assertEqual(item.revision, 2)
        self.assertEqual(self.audit.get_history(action="update")[0]["item_id"], "news-001")

    def test_update_rejects_unknown_field(self):
        self.add_news()
        with self.assertRaises(Exception) as ctx:
            self.engine.update("news-001", id="hacked")
        self.assertIn("不可更新字段", str(ctx.exception))

    def test_content_type_change_recomputes_expiry(self):
        self.add_science()  # science 无过期时间
        item = self.engine.update("sci-001", content_type="news")
        self.assertIsNotNone(item.expire_at)

    def test_explicit_expire_at_is_respected(self):
        self.add_news()
        item = self.engine.update("news-001", expire_at=None)
        self.assertIsNone(item.expire_at)

    def test_cannot_set_active_when_superseded(self):
        self.add_fake_pair()
        with self.assertRaises(ConflictError):
            self.engine.update("fake-001", status="active")

    def test_cannot_change_type_when_superseded(self):
        self.add_fake_pair()
        with self.assertRaises(ConflictError):
            self.engine.update("fake-001", content_type="news")

    def test_empty_update_rejected(self):
        self.add_news()
        with self.assertRaises(Exception):
            self.engine.update("news-001")

    def test_failed_update_does_not_leave_partial_changes(self):
        self.add_news()
        with self.assertRaises(Exception):
            self.engine.update("news-001", title="不该生效", status="不存在的状态")
        self.assertEqual(self.engine.get("news-001").title, "某公司发布新产品")


class DeleteTests(EngineTestCase):
    def test_delete_removes_item_and_audits(self):
        self.add_news()
        self.assertTrue(self.engine.delete("news-001"))
        self.assertIsNone(self.engine.get("news-001"))
        self.assertEqual([e["action"] for e in self.audit.get_history()], ["create", "delete"])

    def test_delete_participating_in_chain_requires_force(self):
        self.add_fake_pair()
        with self.assertRaises(ConflictError):
            self.engine.delete("fake-001")
        with self.assertRaises(ConflictError):
            self.engine.delete("sup-001")

    def test_force_delete_supplement_restores_fake(self):
        self.add_fake_pair()
        self.engine.delete("sup-001", force=True)
        fake = self.engine.get("fake-001")
        self.assertIs(fake.status, Status.ACTIVE)
        self.assertIsNone(fake.superseded_by)
        self.assertEqual(self.engine.integrity()["dangling_superseded_by"], [])

    def test_force_delete_fake_clears_backlink(self):
        self.add_fake_pair()
        self.engine.delete("fake-001", force=True)
        self.assertIsNone(self.engine.get("sup-001").corrects)


class SearchTests(EngineTestCase):
    def test_ranks_title_match_above_content_match(self):
        self.engine.add("content-hit", "一条新闻", "量子", "news", created_at=FIXED)
        self.engine.add("title-hit", "量子计算", "正文", "news", created_at=FIXED)
        ids = [item.id for item in self.engine.search("量子", ranked=True)]
        self.assertEqual(ids[0], "title-hit")

    def test_all_terms_required_by_default(self):
        self.engine.add("both", "量子计算", "正文", "news", created_at=FIXED)
        self.assertEqual([item.id for item in self.engine.search("量子 计算")], ["both"])
        self.assertEqual(self.engine.search("量子 通信"), [])

    def test_any_term_matches(self):
        self.engine.add("q", "量子", "正文", "news", created_at=FIXED)
        self.engine.add("c", "通信", "正文", "news", created_at=FIXED)
        ids = {item.id for item in self.engine.search("量子 通信", match="any")}
        self.assertEqual(ids, {"q", "c"})

    def test_tag_filter_requires_all_by_default(self):
        self.engine.add("a", "A", "x", "news", tags=["AI", "模型"], created_at=FIXED)
        self.engine.add("b", "B", "x", "news", tags=["AI"], created_at=FIXED)
        self.assertEqual({i.id for i in self.engine.list_by_tags(["AI"])}, {"a", "b"})
        self.assertEqual([item.id for item in self.engine.list_by_tags(["AI", "模型"])], ["a"])
        self.assertEqual(
            {item.id for item in self.engine.list_by_tags(["AI", "模型"], match_all=False)},
            {"a", "b"},
        )

    def test_tag_counts(self):
        self.engine.add("a", "A", "x", "news", tags=["AI", "模型"], created_at=FIXED)
        self.engine.add("b", "B", "x", "news", tags=["AI"], created_at=FIXED)
        self.assertEqual(self.engine.tag_counts(), {"AI": 2, "模型": 1})


class StatsTests(EngineTestCase):
    def test_stats_counts_statuses_and_types(self):
        self.add_news()
        self.add_science()
        self.add_fake_pair()
        stats = self.engine.stats()
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["active"], 3)
        self.assertEqual(stats["by_status"]["deprecated"], 1)
        self.assertEqual(stats["by_type"]["news"], 1)
        self.assertEqual(stats["corrections"], 1)

    def test_integrity_is_clean_by_default(self):
        self.add_fake_pair()
        self.assertEqual(
            self.engine.integrity(),
            {"dangling_superseded_by": [], "dangling_corrects": []},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
