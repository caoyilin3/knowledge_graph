"""合成场景集：用来验证「策略语义是否按设计执行」。

**这不是真实语料上的准确率评测。** 每条场景的期望结果都是按默认策略表
推导出来的，所以这套评测回答的是「引擎有没有按写下来的规则办事」，
而不是「这些规则在真实新闻流上效果如何」。两者不要混为一谈。

场景覆盖了几个容易写错的边界：
- 新闻恰好到期（第 7 天）与差一天到期；
- 三年以上的科学知识不得被清掉；
- 被取代的假科普必须保留、状态为 deprecated；
- 未登记类型走默认策略（永久保留）；
- 未纠正的假科普保持 active。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

#: 评测基准时刻，与 tests 里的 FIXED 保持同一语义（但独立定义，避免耦合）。
FIXED = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

ACTIVE = "active"
DEPRECATED = "deprecated"
MISSING = "missing"


@dataclass(frozen=True)
class Scenario:
    item_id: str
    content_type: str
    days_old: int
    expect: str
    corrects: str | None = None
    note: str = ""


def build_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []

    # 12 条新闻：1~6 天保留，7~12 天删除（第 7 天 expire_at 恰好等于 FIXED，属边界）
    for day in range(1, 13):
        scenarios.append(
            Scenario(
                item_id=f"news-{day:02d}",
                content_type="news",
                days_old=day,
                expect=MISSING if day >= 7 else ACTIVE,
                note="边界日" if day == 7 else "",
            )
        )

    # 8 条科研知识，最老的 8 年，全部永久保留
    for index, age in enumerate([1, 30, 365, 900, 1500, 2200, 3000, 2900], start=1):
        scenarios.append(
            Scenario(f"sci-{index:02d}", "science", age, ACTIVE, note="永久保留")
        )

    # 8 条假科普：前 5 条被纠正（deprecated），后 3 条未纠正（active）
    for index in range(1, 9):
        corrected = index <= 5
        scenarios.append(
            Scenario(
                f"fake-{index:02d}",
                "fake_science",
                10,
                DEPRECATED if corrected else ACTIVE,
                note="已被取代" if corrected else "未纠正",
            )
        )

    # 8 条补充科普：前 5 条纠正上面的假科普，后 3 条独立
    for index in range(1, 9):
        corrects = f"fake-{index:02d}" if index <= 5 else None
        scenarios.append(
            Scenario(
                f"sup-{index:02d}",
                "supplement",
                10,
                ACTIVE,
                corrects=corrects,
                note="纠错条目" if corrects else "独立条目",
            )
        )

    # 2 条未登记类型，极老，验证「默认策略 = 永久保留」
    for index in (1, 2):
        scenarios.append(
            Scenario(f"misc-{index:02d}", "misc", 4000, ACTIVE, note="未登记类型走默认策略")
        )

    return scenarios
