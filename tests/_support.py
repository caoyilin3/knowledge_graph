"""测试共用的夹具。

约定：所有涉及时间的用例都用 ``FIXED`` 这个固定时刻，避免依赖本机时钟。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from knowledge_graph import AuditLog, Engine, MemoryStore, PolicyBook, default_book

#: 测试基准时刻（UTC）。所有 created_at / cleanup(now=) 都基于它。
FIXED = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class EngineTestCase(unittest.TestCase):
    """带内存存储与审计日志的引擎夹具。"""

    def setUp(self) -> None:
        self.store = MemoryStore()
        self.audit = AuditLog()
        self.engine = Engine(self.store, audit=self.audit)

    # ---- 便捷构造 ----

    def add_news(self, item_id: str = "news-001", *, days_old: int = 0, **kwargs):
        return self.engine.add(
            item_id,
            "某公司发布新产品",
            "正文内容",
            "news",
            created_at=FIXED - timedelta(days=days_old),
            **kwargs,
        )

    def add_science(self, item_id: str = "sci-001", *, days_old: int = 0, **kwargs):
        return self.engine.add(
            item_id,
            "什么是黑洞",
            "黑洞是引力极强的天体",
            "science",
            created_at=FIXED - timedelta(days=days_old),
            **kwargs,
        )

    def add_fake(self, item_id: str = "fake-001", **kwargs):
        return self.engine.add(
            item_id,
            "喝柠檬水能治癌",
            "网传柠檬水可以杀灭癌细胞",
            "fake_science",
            created_at=FIXED,
            **kwargs,
        )

    def add_supplement(self, item_id: str = "sup-001", *, corrects: str | None = "fake-001", **kwargs):
        return self.engine.add(
            item_id,
            "辟谣：柠檬水不能治癌",
            "柠檬水只是普通饮品，无法治癌",
            "supplement",
            corrects=corrects,
            created_at=FIXED,
            **kwargs,
        )

    def add_fake_pair(self, fake_id: str = "fake-001", supplement_id: str = "sup-001"):
        """建一对「假科普 + 辟谣」。"""
        self.add_fake(fake_id)
        return self.add_supplement(supplement_id, corrects=fake_id)


def custom_book(**overrides) -> PolicyBook:
    """在默认策略表基础上覆盖某几类，返回新的 PolicyBook。"""
    book = default_book()
    for category, policy in overrides.items():
        book.register(policy)
    return book
