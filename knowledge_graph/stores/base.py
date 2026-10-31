"""存储抽象与通用过滤逻辑。

契约
----
- 所有方法失败时抛 :class:`~knowledge_graph.errors.StoreError` / ``ValidationError``，
  **不吞异常、不返回空库**；
- 存储持有条目对象的引用：修改字段后必须再调用 ``save()``，持久化实现才会写盘；
- ``query`` 只接受条目上真实存在的字段名，拼错会报错而不是静默返回空列表。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import fields
from typing import Any

from ..errors import ValidationError
from ..models import ContentItem, Status

_FIELD_NAMES = frozenset(f.name for f in fields(ContentItem))


def validate_filters(filters: dict[str, Any]) -> None:
    """在扫描前就拒绝拼错的字段名，避免「静默返回空列表」。"""
    for key in filters:
        if key not in _FIELD_NAMES:
            raise ValidationError(f"未知查询字段: {key}")


def match_filters(item: ContentItem, filters: dict[str, Any]) -> bool:
    """判断条目是否满足全部等值条件。未知字段直接报错。"""
    for key, expected in filters.items():
        if not hasattr(item, key):
            raise ValidationError(f"未知查询字段: {key}")
        actual = getattr(item, key)
        if key == "status":
            actual = Status.parse(actual)
            expected = Status.parse(expected)
        if actual != expected:
            return False
    return True


class BaseStore(ABC):
    """存储后端接口。"""

    @abstractmethod
    def save(self, item: ContentItem) -> None:
        """新增或覆盖一条内容。"""

    @abstractmethod
    def get(self, item_id: str) -> ContentItem | None:
        """按 id 读取，不存在返回 ``None``。"""

    @abstractmethod
    def delete(self, item_id: str) -> bool:
        """按 id 删除，返回是否真的删掉了。"""

    @abstractmethod
    def list_all(self) -> list[ContentItem]:
        """列出全部条目（含隐藏状态）。"""

    @abstractmethod
    def query(self, **filters: Any) -> list[ContentItem]:
        """按字段等值过滤。"""

    @abstractmethod
    def count(self, **filters: Any) -> int:
        """满足条件的条目数。"""

    @abstractmethod
    def flush(self) -> bool:
        """把内存状态刷到持久层；纯内存实现返回 ``True``。"""

    # 便于 ``len(store)`` 与 ``with`` 使用

    def close(self) -> None:
        self.flush()

    def __len__(self) -> int:
        return len(self.list_all())

    def __enter__(self) -> "BaseStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
