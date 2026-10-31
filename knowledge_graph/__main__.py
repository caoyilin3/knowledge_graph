"""命令行入口：``python -m knowledge_graph``。

三个子命令：
- ``demo``     内存里跑一遍完整流程（默认）
- ``inspect``  打印数据文件的统计
- ``cleanup``  对数据文件执行一次清理（支持 --dry-run）
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Sequence

from .engine import Engine
from .errors import KnowledgeGraphError
from .models import utc_now
from .stores import JsonStore


def _demo() -> int:
    with tempfile.TemporaryDirectory(prefix="kg-demo-") as tmp:
        store = JsonStore(Path(tmp) / "demo.json")
        engine = Engine(store)

        print("=" * 56)
        print("  knowledge-graph 演示（数据写入临时文件，不污染工作区）")
        print("=" * 56)

        nine_days_ago = utc_now() - timedelta(days=9)
        engine.add("news-001", "OpenAI 发布 GPT-5", "……", "news", created_at=nine_days_ago)
        engine.add("sci-001", "什么是黑洞", "黑洞是引力极强的天体……", "science")
        engine.add("fake-001", "喝柠檬水能治癌", "网传柠檬水可以杀灭癌细胞……", "fake_science")
        engine.add(
            "sup-001",
            "辟谣：柠檬水不能治癌",
            "柠檬水只是普通饮品，无法治癌……",
            "supplement",
            corrects="fake-001",
        )

        fake = engine.get("fake-001")
        print(f"\n[1] 纠错发生时，假科普状态 = {fake.status.value}（仍然保留，可追溯）")

        chain = engine.correction_chain("fake-001")
        print(f"[2] 纠错链：{fake.id} ← {[i.id for i in chain['corrected_by']]}")

        preview = engine.cleanup(dry_run=True)
        print(f"[3] 清理预演：{preview.as_dict()}")

        report = engine.cleanup()
        print(f"[4] 执行清理：删除 {len(report.deleted)} 条，保留 {report.total_active} 条活跃")
        print(f"    被删除的是过期新闻、被保留的是纠错链：{report.deleted}")

        again = engine.cleanup()
        print(f"[5] 再跑一次：处理 {again.handled} 条（幂等）")

        stats = engine.stats()
        print(f"[6] 统计：total={stats['total']} active={stats['active']} "
              f"corrections={stats['corrections']}")

        print("\n演示结束。数据文件随临时目录一起删除。\n")
    return 0


def _load_store(path: str) -> JsonStore:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"文件不存在: {target}")
    if target.is_dir():
        raise IsADirectoryError(f"这是目录，不是数据文件: {target}")
    return JsonStore(target)


def _inspect(path: str) -> int:
    store = _load_store(path)
    engine = Engine(store)
    print(json.dumps(engine.stats(), ensure_ascii=False, indent=2))
    return 0


def _cleanup(path: str, dry_run: bool) -> int:
    store = _load_store(path)
    engine = Engine(store)
    report = engine.cleanup(dry_run=dry_run)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-graph",
        description="知识条目生命周期与纠错引擎",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("demo", help="在临时目录跑一遍完整流程")

    inspect = sub.add_parser("inspect", help="打印数据文件统计")
    inspect.add_argument("path", help="JSON 数据文件路径")

    cleanup = sub.add_parser("cleanup", help="执行一次生命周期清理")
    cleanup.add_argument("path", help="JSON 数据文件路径")
    cleanup.add_argument("--dry-run", action="store_true", help="只预演，不写盘")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "demo"

    try:
        if command == "demo":
            return _demo()
        if command == "inspect":
            return _inspect(args.path)
        if command == "cleanup":
            return _cleanup(args.path, args.dry_run)
    except KnowledgeGraphError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    parser.error(f"未知命令: {command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
