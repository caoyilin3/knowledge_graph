"""数据模型。

只用一个实体 ``ContentItem`` 承载全部知识条目。围绕它只有两件事：
状态（生命周期）与链接（纠错关系）。刻意不引入 ORM / pydantic，
以保持零依赖。

时间约定
--------
内部一律使用 **UTC aware** ``datetime``；写出时序列化为带偏移量的
ISO 8601（如 ``2026-10-22T12:00:00+00:00``）。旧数据里的 naive 时间
按 UTC 解释——这是唯一合理的假设，且要写进迁移说明。
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .errors import ValidationError

#: 内置内容类型。允许自定义类型；未登记的类型走默认策略（永久保留、不清理）。
CONTENT_TYPES: tuple[str, ...] = ("news", "science", "fake_science", "supplement")

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$")

#: 标签数量与单标签长度上限，避免一条内容把存储撑爆。
_TAG_MAX_COUNT = 100
_TAG_MAX_LENGTH = 100

#: 旧版本用过的状态值，读取时自动映射。
_STATUS_ALIASES = {"superseded": "deprecated"}


def utc_now() -> datetime:
    """当前时间（UTC aware）。"""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """把 datetime 统一成 UTC aware；naive 值按 UTC 解释。"""
    if not isinstance(value, datetime):
        raise ValidationError(f"需要 datetime，收到 {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Status(str, Enum):
    """条目状态。默认查询只返回 ``ACTIVE``。"""

    ACTIVE = "active"
    EXPIRED = "expired"          # 已到期、但策略动作是 none：保留但隐藏
    DEPRECATED = "deprecated"    # 被取代 / 已过时：保留、可追溯、不在活跃视图
    ARCHIVED = "archived"        # 归档：保留、不在活跃视图

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志可读性
        return self.value

    @classmethod
    def parse(cls, value: "Status | str") -> "Status":
        if isinstance(value, cls):
            return value
        raw = str(value).strip().lower()
        raw = _STATUS_ALIASES.get(raw, raw)
        try:
            return cls(raw)
        except ValueError as exc:
            options = ", ".join(s.value for s in cls)
            raise ValidationError(f"未知状态 {value!r}，可选：{options}") from exc


def validate_id(value: Any) -> str:
    """id 只允许字母数字开头、限长 128，避免路径/键注入类问题。"""
    text = str(value or "").strip()
    if not _ID_PATTERN.match(text):
        raise ValidationError(
            f"非法 id {value!r}：需以字母或数字开头，只含字母数字与 . _ : -，最长 128"
        )
    return text


def require_text(value: Any, name: str, *, max_length: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} 必须是字符串，收到 {type(value).__name__}")
    text = value.strip() if not allow_empty else value
    if not allow_empty and not text:
        raise ValidationError(f"{name} 不能为空")
    if len(text) > max_length:
        raise ValidationError(f"{name} 超长：{len(text)} > {max_length}")
    return text


@dataclass
class ContentItem:
    """一条知识条目。

    字段
    ----
    id
        唯一标识，也是纠错链的指针。
    content_type
        内容类型，决定套用哪条保留策略。
    status
        生命周期状态，见 :class:`Status`。
    expire_at
        过期时间；``None`` 表示没有时间维度的到期。
    superseded_by
        指向“取代它的条目”，即纠错链的**正向**指针（错误 → 更正）。
    corrects
        指向“它纠正的条目”，即纠错链的**反向**指针（更正 → 错误）。
    revision
        每次 update 自增，便于审计比对。
    """

    id: str
    title: str
    content: str
    content_type: str
    created_at: datetime = field(default_factory=utc_now)
    status: Status = Status.ACTIVE
    tags: list[str] = field(default_factory=list)
    expire_at: datetime | None = None
    superseded_by: str | None = None
    corrects: str | None = None
    related_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    revision: int = 1

    def __post_init__(self) -> None:
        self.id = validate_id(self.id)
        self.title = require_text(self.title, "title", max_length=500)
        self.content = require_text(self.content, "content", max_length=200_000, allow_empty=True)
        self.content_type = require_text(self.content_type, "content_type", max_length=64)
        self.created_at = as_utc(self.created_at)
        self.expire_at = as_utc(self.expire_at) if self.expire_at is not None else None
        self.status = Status.parse(self.status)

        if not isinstance(self.revision, int) or self.revision < 1:
            raise ValidationError(f"revision 必须是 >=1 的整数，收到 {self.revision!r}")

        self.tags = _clean_str_list(self.tags, "tags")
        self.related_ids = _clean_str_list(self.related_ids, "related_ids")
        if len(self.tags) > _TAG_MAX_COUNT:
            raise ValidationError(f"tags 最多 {_TAG_MAX_COUNT} 个")
        too_long = [tag for tag in self.tags if len(tag) > _TAG_MAX_LENGTH]
        if too_long:
            raise ValidationError(f"单个 tag 最长 {_TAG_MAX_LENGTH} 字符: {too_long[0]!r}")

        if not isinstance(self.metadata, dict):
            raise ValidationError("metadata 必须是 dict")
        self.metadata = copy.deepcopy(self.metadata)
        _validate_json_metadata(self.metadata)

        if self.superseded_by is not None:
            self.superseded_by = validate_id(self.superseded_by)
            if self.superseded_by == self.id:
                raise ValidationError("条目不能取代自身")
        if self.corrects is not None:
            self.corrects = validate_id(self.corrects)
            if self.corrects == self.id:
                raise ValidationError("条目不能纠正自身")

    # ---------- 查询辅助 ----------

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expire_at is None:
            return False
        return as_utc(now or utc_now()) >= self.expire_at

    @property
    def is_visible(self) -> bool:
        return self.status is Status.ACTIVE

    @property
    def is_deprecated(self) -> bool:
        return self.status is Status.DEPRECATED

    @property
    def is_replaced(self) -> bool:
        return self.superseded_by is not None

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "content_type": self.content_type,
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "tags": list(self.tags),
            "expire_at": self.expire_at.isoformat() if self.expire_at else None,
            "superseded_by": self.superseded_by,
            "corrects": self.corrects,
            "related_ids": list(self.related_ids),
            "metadata": copy.deepcopy(self.metadata),
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContentItem":
        """从存储结构还原。忽略未知字段，兼容 v1 的 ``superseded`` 状态。"""
        payload = dict(data or {})
        for key in ("id", "title", "content", "content_type"):
            if key not in payload:
                raise ValidationError(f"缺少必填字段 {key}")

        for key in ("created_at", "expire_at"):
            value = payload.get(key)
            if isinstance(value, str):
                try:
                    payload[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValidationError(f"{key} 不是合法时间: {value!r}") from exc

        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in known})

    def copy(self) -> "ContentItem":
        return ContentItem.from_dict(copy.deepcopy(self.to_dict()))


def _clean_str_list(values: Any, name: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValidationError(f"{name} 必须是字符串列表")
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValidationError(f"{name} 只能包含字符串")
        text = value.strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _validate_json_metadata(metadata: dict[str, Any]) -> None:
    """metadata 必须能原样写进 JSON，否则会在落盘时才炸。"""
    for key in metadata:
        if not isinstance(key, str):
            raise ValidationError("metadata 的键必须是字符串")
    try:
        json.dumps(metadata, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"metadata 必须可 JSON 序列化: {exc}") from exc
