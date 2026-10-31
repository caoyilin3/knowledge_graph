"""把 v1 的 pickle 存储迁移成 v2 的 JSON 存储。

用法::

    python scripts/migrate_v1_pickle.py knowledge_graph/data/test.pkl data/demo.json

说明
----
- 只读取你本地的旧文件；pickle 本身不安全，不要用它加载来路不明的数据；
- 旧状态 ``superseded`` 会映射为 ``deprecated``，条目保留，纠错链不丢；
- 迁移是只读源文件的，原 pickle 不会被删除。
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

# 允许直接从仓库根目录运行，无需先 pip install
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_graph import ContentItem, JsonStore, Status  # noqa: E402


def migrate(source: Path, target: Path) -> int:
    with source.open("rb") as handle:
        raw = pickle.load(handle)  # noqa: S301 - 明确针对用户自己的本地数据
    if not isinstance(raw, dict):
        raise SystemExit(f"不认识的 v1 结构：顶层应为 dict，实际是 {type(raw).__name__}")

    store = JsonStore(target)
    migrated = 0
    for item_id, payload in raw.items():
        if not isinstance(payload, dict):
            print(f"跳过 {item_id}: 不是对象")
            continue
        item = ContentItem.from_dict(payload)
        store.save(item)
        migrated += 1
        flag = " (已映射 superseded → deprecated)" if item.status is Status.DEPRECATED else ""
        print(f"迁移 {item.id}{flag}")

    # v1 只有 superseded_by，没有反向的 corrects；这里补齐，否则纠错链只有半边。
    repaired = 0
    for item in store.list_all():
        if not item.superseded_by:
            continue
        supplement = store.get(item.superseded_by)
        if supplement is not None and supplement.corrects != item.id:
            supplement.corrects = item.id
            supplement.revision += 1
            store.save(supplement)
            repaired += 1
    if repaired:
        print(f"补齐反向指针 corrects：{repaired} 条")

    store.flush()
    return migrated


def main() -> int:
    parser = argparse.ArgumentParser(description="v1 pickle → v2 JSON 迁移工具")
    parser.add_argument("source", type=Path, help="旧 pickle 文件")
    parser.add_argument("target", type=Path, help="新 JSON 文件")
    args = parser.parse_args()

    if not args.source.exists():
        parser.error(f"源文件不存在: {args.source}")
    count = migrate(args.source, args.target)
    print(f"\n完成：{count} 条 → {args.target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
