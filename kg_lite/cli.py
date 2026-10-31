"""knowledge-graph-lite：个人知识库命令行。

设计原则：CLI 只依赖 ``knowledge_graph`` 的公开 API，不碰内部实现。
这样以后把它拆成独立发行版时，只需要搬目录 + 改 pyproject。

数据文件位置优先级：``--data`` > ``KG_DATA`` > ``~/.knowledge-graph/kb.json``。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from knowledge_graph import (
    ContentItem,
    Engine,
    JsonStore,
    KnowledgeGraphError,
    NotFoundError,
    Status,
    ValidationError,
    __version__ as core_version,
    utc_now,
)

from .backup import DEFAULT_KEEP, create_backup
from .config import resolve_data_path
from .render import ITEM_HEADERS, display_width, format_time, item_row, pad, table

# --------------------------------------------------------------------------- 辅助


def _split_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"--created-at 不是合法 ISO 时间: {raw!r}") from exc


def _generate_id(content_type: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", content_type.lower()).strip("-") or "item"
    stamp = utc_now().strftime("%Y%m%d%H%M%S")
    return f"{safe}-{stamp}-{os.urandom(2).hex()}"


def _open(args: argparse.Namespace, *, create: bool = False) -> tuple[Path, Engine]:
    path = resolve_data_path(args.data)
    if not create and not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {path}（先运行 kg init）")
    return path, Engine(JsonStore(path))


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _handle_error(exc: Exception) -> int:
    print(f"错误：{exc}", file=sys.stderr)
    return 1


def _backup_before_write(args: argparse.Namespace, path: Path) -> None:
    """破坏性操作前先留一份文件级快照；备份失败就中止，不冒丢数据的险。"""
    if getattr(args, "no_backup", False) or not path.exists():
        return
    target = create_backup(path)
    if target is not None:
        print(f"已备份: {target}", file=sys.stderr)


# --------------------------------------------------------------------------- 命令


def _cmd_init(args: argparse.Namespace) -> int:
    path = resolve_data_path(args.data)
    if path.exists():
        print(f"数据文件已存在: {path}")
        return 0
    JsonStore(path).flush()
    print(f"已初始化: {path}")
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    _, engine = _open(args, create=True)
    item_id = args.id or _generate_id(args.type)
    item = engine.add(
        item_id,
        args.title,
        args.content or "",
        args.type,
        tags=_split_tags(args.tags),
        corrects=args.corrects,
        created_at=_parse_time(args.created_at),
        overwrite=args.overwrite,
    )
    print(f"已添加 {item.id}（{item.content_type}，{item.status.value}）")
    if item.corrects:
        print(f"已建立纠错链: {item.corrects} ← {item.id}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    _, engine = _open(args)
    if args.tag:
        items = engine.list_by_tags(
            args.tag,
            match_all=not args.any_tag,
            include_hidden=args.all,
            content_type=args.type,
        )
    elif args.all:
        items = engine.store.list_all()
        if args.type:
            items = [item for item in items if item.content_type == args.type]
        items.sort(key=lambda item: item.created_at, reverse=True)
    else:
        items = engine.list_active(args.type)

    total = len(items)
    shown = items[: args.limit] if args.limit and args.limit > 0 else items
    if args.json:
        _print_json([item.to_dict() for item in shown])
        return 0
    if not shown:
        print("（没有匹配的条目）")
        return 0
    print(table(ITEM_HEADERS, [item_row(item) for item in shown]))
    if len(shown) < total:
        print(f"\n共 {total} 条，仅显示前 {len(shown)} 条（用 --limit 调整）")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    _, engine = _open(args)
    item = engine.get(args.id)
    if item is None:
        raise NotFoundError(f"条目不存在: {args.id}")
    chain = engine.correction_chain(item.id)

    if args.json:
        payload = item.to_dict()
        payload["chain"] = {
            "corrects": [node.id for node in chain["corrects"]],
            "corrected_by": [node.id for node in chain["corrected_by"]],
        }
        _print_json(payload)
        return 0

    def title_of(item_id: str | None) -> str:
        if not item_id:
            return "—"
        linked = engine.get(item_id)
        return f"{item_id}（{linked.title}）" if linked else f"{item_id}（已失效）"

    rows = [
        ("ID", item.id),
        ("标题", item.title),
        ("类型", item.content_type),
        ("状态", item.status.value),
        ("创建", format_time(item.created_at)),
        ("过期", format_time(item.expire_at)),
        ("标签", "、".join(item.tags) if item.tags else "—"),
        ("纠正", title_of(item.corrects)),
        ("被纠正", title_of(item.superseded_by)),
        ("关联", "、".join(item.related_ids) if item.related_ids else "—"),
        ("修订", str(item.revision)),
    ]
    width = max(display_width(label) for label, _ in rows)
    for label, value in rows:
        print(f"{pad(label, width)} : {value}")
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    _, engine = _open(args)
    items = engine.search(
        args.keyword,
        include_hidden=args.all,
        match="any" if args.any else "all",
        ranked=True,
    )
    if args.tag:
        wanted = {tag.lower() for tag in args.tag}
        items = [item for item in items if wanted <= {tag.lower() for tag in item.tags}]
    total = len(items)
    shown = items[: args.limit] if args.limit and args.limit > 0 else items
    if args.json:
        _print_json([item.to_dict() for item in shown])
        return 0
    if not shown:
        print(f"（没有匹配「{args.keyword}」的条目）")
        return 0
    print(table(ITEM_HEADERS, [item_row(item) for item in shown]))
    if len(shown) < total:
        print(f"\n共 {total} 条，仅显示前 {len(shown)} 条（用 --limit 调整）")
    return 0


def _cmd_tags(args: argparse.Namespace) -> int:
    _, engine = _open(args)
    counts = engine.tag_counts(include_hidden=args.all)
    if args.json:
        _print_json(counts)
        return 0
    if not counts:
        print("（还没有标签）")
        return 0
    print(table(("标签", "数量"), [[tag, str(count)] for tag, count in counts.items()]))
    return 0


def _cmd_backup(args: argparse.Namespace) -> int:
    path = resolve_data_path(args.data)
    if not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {path}")
    target = create_backup(path, keep=args.keep)
    print(f"已备份: {target}")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    _, engine = _open(args)
    fields: dict[str, Any] = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.content is not None:
        fields["content"] = args.content
    if args.tags is not None:
        fields["tags"] = _split_tags(args.tags)
    if args.type is not None:
        fields["content_type"] = args.type
    if args.status is not None:
        fields["status"] = args.status
    if not fields:
        raise ValidationError("至少提供一个可更新字段（--title/--content/--tags/--type/--status）")
    item = engine.update(args.id, **fields)
    print(f"已更新 {item.id}（revision {item.revision}）")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    path, engine = _open(args)
    _backup_before_write(args, path)
    engine.delete(args.id, force=args.force)
    print(f"已删除 {args.id}")
    return 0


def _cmd_cleanup(args: argparse.Namespace) -> int:
    path, engine = _open(args)
    if not args.dry_run:
        _backup_before_write(args, path)
    report = engine.cleanup(dry_run=args.dry_run)
    if args.json:
        _print_json(report.as_dict())
        return 0

    prefix = "预演" if args.dry_run else "清理完成"
    print(f"{prefix}：共处理 {report.handled} 条")
    for label, ids in (
        ("删除", report.deleted),
        ("归档", report.archived),
        ("下架", report.deprecated),
        ("标记过期", report.expired),
    ):
        if ids:
            print(f"  {label} {len(ids)} 条: {', '.join(ids)}")
    print(f"剩余 active: {report.total_active}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.yes:
        print("批量清除不可逆：确认请加 --yes（或先用 --dry-run 预演）", file=sys.stderr)
        return 2
    path, engine = _open(args)
    if not args.dry_run:
        _backup_before_write(args, path)
    victims = engine.purge(
        status=args.status,
        older_than_days=args.older_than,
        dry_run=args.dry_run,
        force=args.force,
    )
    if args.json:
        _print_json({"victims": victims, "dry_run": args.dry_run})
    else:
        prefix = "将清除" if args.dry_run else "已清除"
        print(f"{prefix} {len(victims)} 条: {', '.join(victims) if victims else '（无）'}")
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    _, engine = _open(args)
    stats = engine.stats()
    if args.json:
        _print_json(stats)
        return 0
    print(f"总数      : {stats['total']}")
    print(f"active    : {stats['active']}")
    for status, count in sorted(stats["by_status"].items()):
        if status != "active":
            print(f"{status:<10}: {count}")
    types = "、".join(f"{name} {count}" for name, count in sorted(stats["by_type"].items()))
    print(f"类型分布  : {types or '—'}")
    print(f"纠错对    : {stats['corrections']}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    _, engine = _open(args)
    items = sorted(engine.store.list_all(), key=lambda item: item.created_at)
    target = Path(args.path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([item.to_dict() for item in items], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已导出 {len(items)} 条 → {target}")
    return 0


def _read_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValidationError(f"第 {lineno} 行不是合法 JSON: {exc}") from exc
        return records

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and "items" in payload:
        return list(payload["items"].values())
    if isinstance(payload, dict):
        return [payload]
    raise ValidationError("导入文件应为 JSON 数组、{items: ...} 或 JSONL")


def _prepare_records(
    records: Sequence[Any], existing: set[str], *, skip_existing: bool
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    skipped: list[str] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ValidationError(f"第 {index} 条不是对象")
        for field in ("id", "title", "content_type"):
            if not record.get(field):
                raise ValidationError(f"第 {index} 条缺少字段 {field}")
        item_id = str(record["id"])
        if item_id in seen:
            raise ValidationError(f"导入文件里 id 重复: {item_id}")
        seen.add(item_id)
        if item_id in existing:
            if skip_existing:
                skipped.append(item_id)
                continue
            raise ValidationError(f"条目已存在: {item_id}（可用 --skip-existing 跳过）")
        prepared.append(dict(record))
    if skipped:
        print(f"跳过已存在条目: {', '.join(skipped)}")
    return prepared


def _cmd_import(args: argparse.Namespace) -> int:
    source = Path(args.path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"导入文件不存在: {source}")

    path, engine = _open(args, create=True)
    existing = {item.id for item in engine.store.list_all()}
    records = _prepare_records(
        _read_records(source), existing, skip_existing=args.skip_existing
    )
    if args.dry_run:
        print(f"将导入 {len(records)} 条（预演，未写入）")
        return 0

    if path.exists():
        _backup_before_write(args, path)

    if args.restore:
        # 忠实还原：状态、过期时间、纠错指针一律照搬，适合备份回滚
        for record in records:
            item = ContentItem.from_dict(record)
            engine.store.save(item)
            engine.audit.log("create", item.id, f"导入（restore）: {item.title}")
        issues = engine.integrity()
        dangling = issues["dangling_superseded_by"] + issues["dangling_corrects"]
        if dangling:
            print(f"警告：以下条目引用了不存在的 id: {', '.join(dangling)}", file=sys.stderr)
        print(f"已还原 {len(records)} 条 → {path}")
        return 0

    # 默认：走策略重建，过期时间按当前策略重新计算，适合手写 JSONL 批量入库
    for record in records:
        engine.add(
            str(record["id"]),
            str(record["title"]),
            str(record.get("content") or ""),
            str(record["content_type"]),
            tags=record.get("tags"),
            created_at=_parse_time(record.get("created_at")),
            metadata=record.get("metadata"),
        )
    for record in records:
        if record.get("corrects"):
            engine.supersede(str(record["corrects"]), str(record["id"]))

    print(f"已导入 {len(records)} 条 → {path}")
    return 0


# --------------------------------------------------------------------------- 参数


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data",
        default=argparse.SUPPRESS,
        help="数据文件路径（默认 ~/.knowledge-graph/kb.json，可用 KG_DATA 覆盖）",
    )
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="以 JSON 输出"
    )
    common.add_argument(
        "--no-backup",
        action="store_true",
        default=argparse.SUPPRESS,
        help="破坏性操作前不自动备份",
    )

    parser = argparse.ArgumentParser(
        prog="kg",
        description="knowledge-graph-lite：个人知识库的「知识新陈代谢」命令行",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="数据文件路径（默认 ~/.knowledge-graph/kb.json，可用 KG_DATA 覆盖）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "--no-backup", action="store_true", help="破坏性操作前不自动备份"
    )
    parser.add_argument(
        "--version", action="version", version=f"knowledge-graph-lite 0.1.0（内核 {core_version}）"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", parents=[common], help="初始化数据文件")
    init.set_defaults(func=_cmd_init)

    add = sub.add_parser("add", parents=[common], help="添加一条内容")
    add.add_argument("--type", required=True, help="内容类型：news / science / fake_science / supplement")
    add.add_argument("--title", required=True, help="标题")
    add.add_argument("--content", default="", help="正文")
    add.add_argument("--id", help="自定义 id（默认自动生成）")
    add.add_argument("--tags", help="标签，逗号分隔")
    add.add_argument("--corrects", help="要纠正的条目 id（建立纠错链）")
    add.add_argument("--created-at", help="创建时间，ISO 8601（默认当前时间）")
    add.add_argument("--overwrite", action="store_true", help="覆盖同 id 条目（不能是纠错链上的）")
    add.set_defaults(func=_cmd_add)

    listing = sub.add_parser("list", parents=[common], help="列出条目")
    listing.add_argument("--type", help="只看某个类型")
    listing.add_argument("--all", action="store_true", help="包含已下架 / 归档 / 过期")
    listing.add_argument(
        "--tag", action="append", metavar="TAG", help="按标签筛选，可重复（默认要求全部命中）"
    )
    listing.add_argument("--any-tag", action="store_true", help="标签命中任一即可")
    listing.add_argument("--limit", type=int, default=20, help="最多显示多少条（0 = 全部）")
    listing.set_defaults(func=_cmd_list)

    show = sub.add_parser("show", parents=[common], help="查看一条内容与纠错链")
    show.add_argument("id", help="条目 id")
    show.set_defaults(func=_cmd_show)

    search = sub.add_parser("search", parents=[common], help="按关键词搜索")
    search.add_argument("keyword", help="关键词，空格分隔多个词")
    search.add_argument("--all", action="store_true", help="包含已下架条目")
    search.add_argument("--any", action="store_true", help="多个词命中任一即可（默认全部）")
    search.add_argument("--tag", action="append", metavar="TAG", help="再按标签过滤，可重复")
    search.add_argument("--limit", type=int, default=20, help="最多显示多少条（0 = 全部）")
    search.set_defaults(func=_cmd_search)

    tags = sub.add_parser("tags", parents=[common], help="列出标签及使用次数")
    tags.add_argument("--all", action="store_true", help="包含已下架条目")
    tags.set_defaults(func=_cmd_tags)

    backup = sub.add_parser("backup", parents=[common], help="手动备份数据文件")
    backup.add_argument(
        "--keep", type=int, default=DEFAULT_KEEP, help="保留最近几份（0 = 全部保留）"
    )
    backup.set_defaults(func=_cmd_backup)

    update = sub.add_parser("update", parents=[common], help="修改条目")
    update.add_argument("id", help="条目 id")
    update.add_argument("--title")
    update.add_argument("--content")
    update.add_argument("--tags", help="标签，逗号分隔")
    update.add_argument("--type", help="新的内容类型")
    update.add_argument("--status", choices=[status.value for status in Status])
    update.set_defaults(func=_cmd_update)

    delete = sub.add_parser("delete", parents=[common], help="删除条目")
    delete.add_argument("id", help="条目 id")
    delete.add_argument("--force", action="store_true", help="参与纠错链时强制拆链")
    delete.set_defaults(func=_cmd_delete)

    cleanup = sub.add_parser("cleanup", parents=[common], help="执行一次生命周期清理")
    cleanup.add_argument("--dry-run", action="store_true", help="只预演，不写盘")
    cleanup.set_defaults(func=_cmd_cleanup)

    purge = sub.add_parser("purge", parents=[common], help="批量清除某个状态的条目")
    purge.add_argument("--status", choices=[s.value for s in Status], default=Status.DEPRECATED.value)
    purge.add_argument("--older-than", type=int, help="只清除创建超过 N 天的")
    purge.add_argument("--force", action="store_true", help="强制拆断仍被引用的链")
    purge.add_argument("--dry-run", action="store_true", help="只预演")
    purge.add_argument("--yes", action="store_true", help="确认执行（不可逆）")
    purge.set_defaults(func=_cmd_purge)

    stats = sub.add_parser("stats", parents=[common], help="统计信息")
    stats.set_defaults(func=_cmd_stats)

    export = sub.add_parser("export", parents=[common], help="导出为 JSON")
    export.add_argument("path", help="输出文件")
    export.set_defaults(func=_cmd_export)

    import_cmd = sub.add_parser("import", parents=[common], help="从 JSON / JSONL 导入")
    import_cmd.add_argument("path", help="输入文件")
    import_cmd.add_argument("--dry-run", action="store_true", help="只校验，不写入")
    import_cmd.add_argument("--skip-existing", action="store_true", help="跳过已存在的 id")
    import_cmd.add_argument(
        "--restore", action="store_true", help="忠实还原状态/过期时间/纠错指针（备份回滚用）"
    )
    import_cmd.set_defaults(func=_cmd_import)

    return parser


def _ensure_utf8_output() -> None:
    """重定向/管道时统一用 UTF-8，避免 Windows 的 locale 编码把中文写坏。

    交互式终端不动：Windows 控制台本身走 Unicode API，不会乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or stream.isatty():
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):  # pragma: no cover - 依赖运行环境
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KnowledgeGraphError as exc:
        return _handle_error(exc)
    except OSError as exc:
        return _handle_error(exc)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
