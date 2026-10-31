"""knowledge_graph —— 零依赖的知识条目生命周期与纠错引擎。

定位：管理「一条知识该留多久、过时了怎么办、被纠正后怎么追溯」，
**不是**图数据库。名字里的 graph 指条目之间由 ``superseded_by`` /
``corrects`` 构成的关系，而不是通用的属性图。

快速上手::

    from knowledge_graph import Engine, JsonStore

    engine = Engine(JsonStore("data/kb.json"))
    engine.add("news-001", "标题", "正文", "news")
    engine.add("fake-001", "喝柠檬水治癌", "……", "fake_science")
    engine.add("sup-001", "辟谣", "……", "supplement", corrects="fake-001")

    engine.get("fake-001").status   # Status.DEPRECATED，仍然可查
    engine.correction_chain("fake-001")["corrected_by"]  # [sup-001]
"""

from __future__ import annotations

from .audit import AuditLog
from .engine import CleanupReport, Engine, MUTABLE_FIELDS
from .errors import (
    ConflictError,
    KnowledgeGraphError,
    NotFoundError,
    StoreError,
    ValidationError,
)
from .models import CONTENT_TYPES, ContentItem, Status, as_utc, utc_now
from .policy import (
    DEFAULT_POLICIES,
    Action,
    PolicyBook,
    Retention,
    RetentionKind,
    RetentionPolicy,
    default_book,
)
from .stores import BaseStore, JsonStore, MemoryStore

__version__ = "2.0.0"

__all__ = [
    "__version__",
    # 核心
    "Engine",
    "CleanupReport",
    "MUTABLE_FIELDS",
    # 模型
    "ContentItem",
    "Status",
    "CONTENT_TYPES",
    "utc_now",
    "as_utc",
    # 策略
    "Action",
    "Retention",
    "RetentionKind",
    "RetentionPolicy",
    "PolicyBook",
    "DEFAULT_POLICIES",
    "default_book",
    # 存储与审计
    "BaseStore",
    "JsonStore",
    "MemoryStore",
    "AuditLog",
    # 异常
    "KnowledgeGraphError",
    "ValidationError",
    "NotFoundError",
    "ConflictError",
    "StoreError",
]
