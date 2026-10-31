"""内存存储：测试、演示与一次性计算用。"""

from __future__ import annotations

from typing import Any, Iterable

from ..models import ContentItem
from .base import BaseStore, match_filters, validate_filters


class MemoryStore(BaseStore):
    """全内存实现，进程退出即丢失。"""

    def __init__(self, items: Iterable[ContentItem] | None = None) -> None:
        self._data: dict[str, ContentItem] = {}
        for item in items or ():
            self.save(item)

    def save(self, item: ContentItem) -> None:
        if not isinstance(item, ContentItem):
            raise TypeError("save() 需要 ContentItem 实例")
        self._data[item.id] = item

    def get(self, item_id: str) -> ContentItem | None:
        return self._data.get(item_id)

    def delete(self, item_id: str) -> bool:
        return self._data.pop(item_id, None) is not None

    def list_all(self) -> list[ContentItem]:
        return list(self._data.values())

    def query(self, **filters: Any) -> list[ContentItem]:
        validate_filters(filters)
        return [item for item in self._data.values() if match_filters(item, filters)]

    def count(self, **filters: Any) -> int:
        return len(self.query(**filters))

    def flush(self) -> bool:
        return True
