"""保留策略：保留规则 × 清理动作 × 优先级。

这是 v1 的 ``policy.py`` 补齐后的版本。v1 只有「天数 / 自动过期 / 可否被取代」
三个字段，等于只实现了设计稿里的 ``Retention`` 的一半，且丢掉了 ``Action`` 与
``Priority``。这里的模型对应设计稿的四元组::

    Policy(Category, Retention, Action, Priority)

三类保留规则
------------
``FIXED_DAYS``
    固定天数，``created_at + days`` 即到期。
``PERMANENT``
    永久保留，没有时间到期。
``UNTIL_SUPERSEDED``
    没有时间到期，只在被取代时到期（事件驱动）。

四种清理动作
------------
``DELETE``
    物理删除。适合低价值、高体积的条目（如新闻）。
``ARCHIVE``
    归档：保留但移出活跃视图。
``DEPRECATE``
    标记过时：保留、可追溯，默认查询不可见。**被取代的错误知识必须用这个**，
    否则纠错链会断。
``NONE``
    只标记为 ``expired``，保留。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import Enum

from .errors import ValidationError
from .models import as_utc


class Action(str, Enum):
    DELETE = "delete"
    ARCHIVE = "archive"
    DEPRECATE = "deprecate"
    NONE = "none"

    def __str__(self) -> str:  # pragma: no cover
        return self.value

    @classmethod
    def parse(cls, value: "Action | str") -> "Action":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            options = ", ".join(a.value for a in cls)
            raise ValidationError(f"未知清理动作 {value!r}，可选：{options}") from exc


class RetentionKind(str, Enum):
    FIXED_DAYS = "fixed_days"
    PERMANENT = "permanent"
    UNTIL_SUPERSEDED = "until_superseded"

    def __str__(self) -> str:  # pragma: no cover
        return self.value


@dataclass(frozen=True)
class Retention:
    """保留规则。``days`` 只在 ``FIXED_DAYS`` 下有意义。"""

    kind: RetentionKind = RetentionKind.PERMANENT
    days: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            try:
                object.__setattr__(self, "kind", RetentionKind(self.kind.strip().lower()))
            except ValueError as exc:
                options = ", ".join(k.value for k in RetentionKind)
                raise ValidationError(f"未知保留规则 {self.kind!r}，可选：{options}") from exc
        if not isinstance(self.days, int) or isinstance(self.days, bool):
            raise ValidationError("days 必须是整数")
        if self.kind is RetentionKind.FIXED_DAYS:
            if self.days <= 0:
                raise ValidationError("fixed_days 需要正整数 days")
        elif self.days != 0:
            raise ValidationError(f"{self.kind.value} 不接受 days（收到 {self.days}）")

    @property
    def is_time_based(self) -> bool:
        return self.kind is RetentionKind.FIXED_DAYS

    def expire_at(self, created_at: datetime) -> datetime | None:
        if not self.is_time_based:
            return None
        return as_utc(created_at) + timedelta(days=self.days)

    def describe(self) -> str:
        if self.kind is RetentionKind.FIXED_DAYS:
            return f"{self.days} 天后到期"
        if self.kind is RetentionKind.PERMANENT:
            return "永久保留"
        return "被取代时到期"


@dataclass(frozen=True)
class RetentionPolicy:
    """一类内容的完整管理策略。"""

    category: str
    retention: Retention = field(default_factory=Retention)
    action: Action = Action.NONE
    priority: int = 0
    can_be_superseded: bool = False

    def __post_init__(self) -> None:
        category = str(self.category or "").strip()
        if not category:
            raise ValidationError("策略的 category 不能为空")
        object.__setattr__(self, "category", category)
        if isinstance(self.action, str):
            object.__setattr__(self, "action", Action.parse(self.action))
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValidationError("priority 必须是整数")
        if not isinstance(self.can_be_superseded, bool):
            raise ValidationError("can_be_superseded 必须是布尔值")
        if self.can_be_superseded and self.action is Action.DELETE:
            raise ValidationError(
                "可被取代的类型不能配置 delete 动作：被取代时删除条目会切断纠错链，"
                "请改用 deprecate 或 archive"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "retention": {"kind": self.retention.kind.value, "days": self.retention.days},
            "action": self.action.value,
            "priority": self.priority,
            "can_be_superseded": self.can_be_superseded,
        }


class PolicyBook:
    """策略表 + 冲突仲裁。

    当一条知识同时命中多个类别时（多标签场景），取 ``priority`` 最大者；
    同优先级按类别名字典序，保证结果稳定可复现。
    """

    def __init__(
        self,
        policies: "Mapping[str, RetentionPolicy] | Iterable[RetentionPolicy] | None" = None,
        *,
        default: RetentionPolicy | None = None,
    ) -> None:
        self._policies: dict[str, RetentionPolicy] = {}
        if policies is not None:
            values = policies.values() if isinstance(policies, Mapping) else policies
            for policy in values:
                self.register(policy)
        self._default = default or RetentionPolicy(
            category="_default", retention=Retention(), action=Action.NONE, priority=-1
        )

    def register(self, policy: RetentionPolicy) -> None:
        if not isinstance(policy, RetentionPolicy):
            raise ValidationError("只能注册 RetentionPolicy 实例")
        self._policies[policy.category] = policy

    def get(self, category: str) -> RetentionPolicy:
        key = str(category or "").strip()
        found = self._policies.get(key)
        if found is not None:
            return found
        # 未登记的类型：永久保留、不清理，保证向前兼容
        return replace(self._default, category=key or "_unknown")

    def resolve(self, categories: Iterable[str]) -> RetentionPolicy:
        """多标签冲突仲裁：优先级最高者胜出。"""
        resolved = [self.get(c) for c in categories]
        if not resolved:
            return self._default
        return max(resolved, key=lambda p: (p.priority, p.category))

    def with_overrides(
        self, policies: "Mapping[str, RetentionPolicy] | Iterable[RetentionPolicy]"
    ) -> "PolicyBook":
        """返回叠加了覆盖项的新表，不修改自身。"""
        clone = PolicyBook(self._policies.values(), default=self._default)
        for policy in policies.values() if isinstance(policies, Mapping) else policies:
            clone.register(policy)
        return clone

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies))

    def as_list(self) -> list[dict[str, object]]:
        return [self._policies[c].as_dict() for c in self.categories]


#: 内置默认策略表。改动这里前先想清楚：它会改变所有使用方的清理行为。
DEFAULT_POLICIES: dict[str, RetentionPolicy] = {
    "news": RetentionPolicy(
        category="news",
        retention=Retention(RetentionKind.FIXED_DAYS, days=7),
        action=Action.DELETE,
        priority=10,
    ),
    "science": RetentionPolicy(
        category="science",
        retention=Retention(RetentionKind.PERMANENT),
        action=Action.NONE,
        priority=20,
    ),
    "fake_science": RetentionPolicy(
        category="fake_science",
        retention=Retention(RetentionKind.UNTIL_SUPERSEDED),
        action=Action.DEPRECATE,          # 关键：保留纠错链，不删
        priority=30,
        can_be_superseded=True,
    ),
    "supplement": RetentionPolicy(
        category="supplement",
        retention=Retention(RetentionKind.PERMANENT),
        action=Action.NONE,
        priority=40,
    ),
}


def default_book() -> PolicyBook:
    """内置策略表的新实例（调用方可以安全地覆盖）。"""
    return PolicyBook(DEFAULT_POLICIES.values())
