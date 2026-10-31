"""终端输出：等宽表格、CJK 宽度、时间本地化。

不引第三方表格库，几十行足够；关键是中文按双宽计算，否则列会错位。
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Sequence

from knowledge_graph import ContentItem


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def truncate(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    kept = ""
    for char in text:
        if display_width(kept + char) > width - 1:
            break
        kept += char
    return kept + "…"


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = []
    for index, header in enumerate(headers):
        width = display_width(header)
        for row in rows:
            width = max(width, display_width(str(row[index])))
        widths.append(width)

    lines = [
        "  ".join(pad(header, widths[i]) for i, header in enumerate(headers)).rstrip(),
        "  ".join("-" * width for width in widths),
    ]
    for row in rows:
        lines.append("  ".join(pad(str(cell), widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def format_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def item_row(item: ContentItem) -> list[str]:
    return [
        item.id,
        item.content_type,
        item.status.value,
        truncate(item.title, 40),
        format_time(item.created_at),
    ]


ITEM_HEADERS = ("ID", "类型", "状态", "标题", "创建时间")
