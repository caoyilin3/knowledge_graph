"""JSON 文件存储：可读、可 diff、原子写入。

为什么不是 pickle
----------------
pickle 能执行任意代码，用它加载来路不明的知识库等于开一个 RCE 后门；而且
不可读、跨版本脆弱。这里换成 UTF-8 JSON：人能看、git 能 diff、出问题能手工修。

写入策略
--------
先写同目录临时文件、``fsync``、再 ``os.replace`` 原子替换——中途崩溃不会留下
半个文件。默认 ``auto_flush=True``，每次写操作即落盘，避免 v1「忘了 flush 就
丢数据」的坑。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ..errors import StoreError
from ..models import ContentItem, utc_now
from .base import BaseStore, match_filters, validate_filters

logger = logging.getLogger(__name__)


class JsonStore(BaseStore):
    """单文件 JSON 存储。

    Parameters
    ----------
    path:
        数据文件路径，父目录不存在会自动创建。
    auto_flush:
        是否每次写操作立即落盘。批量导入时可关掉，最后手动 ``flush()``。
    """

    SCHEMA_VERSION = 2

    def __init__(self, path: "str | Path", *, auto_flush: bool = True) -> None:
        self.path = Path(path)
        self.auto_flush = auto_flush
        self._data: dict[str, ContentItem] = {}
        self._dirty = False
        self._load()

    # ---------- 读取 ----------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StoreError(f"无法读取存储文件 {self.path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise StoreError(
                f"存储文件不是合法 JSON: {self.path}（第 {exc.lineno} 行第 {exc.colno} 列）。"
                "请修复文件或从备份恢复，不要直接删除。"
            ) from exc

        if isinstance(raw, dict) and "items" in raw:
            version = raw.get("schema_version")
            if version is not None:
                try:
                    version_number = int(version)
                except (TypeError, ValueError) as exc:
                    raise StoreError(f"schema_version 非法: {version!r}") from exc
                if version_number > self.SCHEMA_VERSION:
                    raise StoreError(
                        f"存储文件版本 {version} 高于当前支持的 {self.SCHEMA_VERSION}，请升级程序"
                    )
            items = raw["items"]
        else:
            # v1：顶层就是 {id: item}
            logger.info("检测到 v1 存储格式，按兼容模式加载: %s", self.path)
            items = raw

        if not isinstance(items, dict):
            raise StoreError(f"存储文件结构错误：items 应为对象，实际是 {type(items).__name__}")

        for key, payload in items.items():
            if not isinstance(payload, dict):
                raise StoreError(f"条目 {key!r} 结构错误：应为对象")
            try:
                item = ContentItem.from_dict(payload)
            except Exception as exc:  # noqa: BLE001 - 统一包装成 StoreError
                raise StoreError(f"条目 {key!r} 反序列化失败: {exc}") from exc
            self._data[item.id] = item

    # ---------- 写入 ----------

    def save(self, item: ContentItem) -> None:
        if not isinstance(item, ContentItem):
            raise TypeError("save() 需要 ContentItem 实例")
        self._data[item.id] = item
        self._mark_dirty()

    def delete(self, item_id: str) -> bool:
        removed = self._data.pop(item_id, None) is not None
        if removed:
            self._mark_dirty()
        return removed

    def flush(self) -> bool:
        # 新库还没写过盘时也允许 flush：lite 的 `kg init` 靠它把空库落到文件。
        if not self._dirty and self.path.exists():
            return True
        self._persist()
        return True

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self.auto_flush:
            self.flush()

    def _persist(self) -> None:
        tmp_path: str | None = None
        try:
            payload = {
                "schema_version": self.SCHEMA_VERSION,
                "saved_at": utc_now().isoformat(),
                "items": {
                    item_id: item.to_dict() for item_id, item in sorted(self._data.items())
                },
            }
            parent = self.path.parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(parent), prefix=f".{self.path.name}.", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
            tmp_path = None
        except OSError as exc:
            raise StoreError(f"写入存储文件失败 {self.path}: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise StoreError(
                f"内容无法序列化为 JSON（检查 metadata 等字段）: {exc}"
            ) from exc
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:  # pragma: no cover - 清理失败不应掩盖原错误
                    logger.warning("临时文件清理失败: %s", tmp_path)
        self._dirty = False

    # ---------- 查询 ----------

    def get(self, item_id: str) -> ContentItem | None:
        return self._data.get(item_id)

    def list_all(self) -> list[ContentItem]:
        return list(self._data.values())

    def query(self, **filters: Any) -> list[ContentItem]:
        validate_filters(filters)
        return [item for item in self._data.values() if match_filters(item, filters)]

    def count(self, **filters: Any) -> int:
        return len(self.query(**filters))
