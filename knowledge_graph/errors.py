"""领域异常。

设计原则：失败要响。旧实现里 ``PickleStore._load`` 遇到任何异常都
``self._data = {}``，等于把损坏的库静默清空；这在知识库场景是不可接受的。
"""

from __future__ import annotations


class KnowledgeGraphError(Exception):
    """所有领域异常的基类。"""


class ValidationError(KnowledgeGraphError):
    """输入不合法（字段缺失、类型不符、状态值未知等）。"""


class NotFoundError(KnowledgeGraphError):
    """按 id 找不到条目。"""


class ConflictError(KnowledgeGraphError):
    """状态冲突（重复 id、重复取代、悬空引用等）。"""


class StoreError(KnowledgeGraphError):
    """存储层失败（文件损坏、写入失败等）。"""
