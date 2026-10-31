"""审计日志。

每个会改变状态的动作都留一条记录：``action / item_id / detail / timestamp``，
可选附加字段（如 ``reason``、``from``、``to``）。v1 的日志只在内存里，进程一退
就没了；这里可选地把每条记录同步追加到 JSONL 文件，便于事后追查。
"""

from __future__ import annotations

import copy
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, Deque

from .errors import ValidationError
from .models import utc_now

logger = logging.getLogger(__name__)

#: 设计上允许的动作名，仅用于文档与校验，不限制自定义动作。
KNOWN_ACTIONS = (
    "create",
    "update",
    "delete",
    "expire",
    "archive",
    "deprecate",
    "supersede",
    "unsupersede",
    "purge",
)


class AuditLog:
    """内存审计日志，可选 JSONL 落盘。

    Parameters
    ----------
    sink:
        JSONL 文件路径。为 ``None`` 时只保留在内存。
    max_entries:
        内存中最多保留多少条（防止长时间运行吃满内存）；落盘不受此限制。
    """

    def __init__(self, sink: "str | Path | None" = None, *, max_entries: int | None = None) -> None:
        if max_entries is not None and max_entries <= 0:
            raise ValidationError("max_entries 必须是正整数或 None")
        self.sink = Path(sink) if sink is not None else None
        self._entries: Deque[dict[str, Any]] = deque(maxlen=max_entries)
        if self.sink is not None:
            self.sink.parent.mkdir(parents=True, exist_ok=True)

    # ---------- 写入 ----------

    #: 这些键由 AuditLog 维护，附加字段不得覆盖。
    RESERVED_KEYS = frozenset({"timestamp", "action", "item_id", "detail"})

    def log(self, action: str, item_id: str, detail: str = "", **extra: Any) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "timestamp": utc_now().isoformat(),
            "action": str(action),
            "item_id": str(item_id),
            "detail": detail,
        }
        if extra:
            conflicts = self.RESERVED_KEYS & extra.keys()
            if conflicts:
                logger.warning("审计附加字段与保留字段冲突，已忽略: %s", sorted(conflicts))
                extra = {k: v for k, v in extra.items() if k not in conflicts}
            entry.update(extra)
        self._entries.append(entry)
        if self.sink is not None:
            self._append_to_sink(entry)
        return copy.deepcopy(entry)

    def _append_to_sink(self, entry: dict[str, Any]) -> None:
        # 审计是旁路：盘满/权限问题不应让主流程失败，但必须留下告警日志。
        try:
            with self.sink.open("a", encoding="utf-8", newline="\n") as handle:  # type: ignore[union-attr]
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover - 依赖外部环境
            logger.warning("审计日志落盘失败 %s: %s", self.sink, exc)

    # ---------- 查询 ----------

    def get_history(
        self, item_id: str | None = None, action: str | None = None
    ) -> list[dict[str, Any]]:
        entries = [copy.deepcopy(e) for e in self._entries]
        if item_id is not None:
            entries = [e for e in entries if e["item_id"] == item_id]
        if action is not None:
            entries = [e for e in entries if e["action"] == action]
        return entries

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._entries:
            counts[entry["action"]] = counts.get(entry["action"], 0) + 1
        return counts

    def export(self, path: "str | Path") -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(list(self._entries), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    def clear(self) -> None:
        self._entries.clear()

    @property
    def entries(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(e) for e in self._entries]

    def __len__(self) -> int:
        return len(self._entries)
