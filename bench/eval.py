"""跑一遍合成场景，输出一致性指标并生成 ``docs/EVALUATION.md``。

用法::

    python bench/eval.py            # 打印结果 + 写 docs/EVALUATION.md
    python bench/eval.py --no-write # 只打印

指标含义见文件末尾的「局限」一节——它们是**规则实现一致性**，不是真实
语料上的准确率。
"""

from __future__ import annotations

import argparse
import platform
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from knowledge_graph import Engine, JsonStore  # noqa: E402
from bench.scenarios import ACTIVE, DEPRECATED, FIXED, MISSING, Scenario, build_scenarios  # noqa: E402

REPORT_PATH = Path(__file__).resolve().parents[1] / "docs" / "EVALUATION.md"


def run(scenarios: list[Scenario]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="kg-bench-") as tmp:
        store = JsonStore(Path(tmp) / "bench.json")
        engine = Engine(store)

        for scenario in scenarios:
            engine.add(
                scenario.item_id,
                f"场景 {scenario.item_id}",
                f"{scenario.content_type} 场景正文",
                scenario.content_type,
                corrects=scenario.corrects,
                created_at=FIXED - timedelta(days=scenario.days_old),
            )

        preview = engine.cleanup(now=FIXED, dry_run=True)
        report = engine.cleanup(now=FIXED)
        second = engine.cleanup(now=FIXED)

        states: dict[str, str] = {}
        for scenario in scenarios:
            item = engine.get(scenario.item_id)
            states[scenario.item_id] = MISSING if item is None else item.status.value

        mismatches = [
            {
                "id": s.item_id,
                "type": s.content_type,
                "expected": s.expect,
                "actual": states[s.item_id],
                "note": s.note,
            }
            for s in scenarios
            if states[s.item_id] != s.expect
        ]

        expected_deleted = {s.item_id for s in scenarios if s.expect == MISSING}
        actual_deleted = set(report.deleted)
        correct_deleted = expected_deleted & actual_deleted
        precision = len(correct_deleted) / len(actual_deleted) if actual_deleted else 1.0
        recall = len(correct_deleted) / len(expected_deleted) if expected_deleted else 1.0

        core_ids = {s.item_id for s in scenarios if s.content_type in ("science", "supplement")}
        core_kept = sum(1 for cid in core_ids if states[cid] == ACTIVE)

        corrected_fakes = [
            s for s in scenarios if s.content_type == "fake_science" and s.expect == DEPRECATED
        ]
        corrected_ids = {s.item_id for s in corrected_fakes}
        chains = [c for c in engine.corrections() if c["fake"] in corrected_ids]
        healthy_chains = sum(1 for c in chains if c["supplement_exists"] and c["backlink_ok"])

        return {
            "total": len(scenarios),
            "states": states,
            "mismatches": mismatches,
            "deleted": sorted(actual_deleted),
            "preview_deleted": sorted(preview.deleted),
            "preview_consistent": sorted(preview.deleted) == sorted(actual_deleted),
            "deletion_precision": precision,
            "deletion_recall": recall,
            "core_total": len(core_ids),
            "core_kept": core_kept,
            "corrected_total": len(corrected_ids),
            "chains_ok": healthy_chains,
            "idempotent": second.handled == 0,
            "integrity": engine.integrity(),
        }


def render_markdown(result: dict[str, object]) -> str:
    total = result["total"]
    mismatches = result["mismatches"]
    passed = total - len(mismatches)
    lines = [
        "# 场景一致性评测",
        "",
        "> 本文件由 `python bench/eval.py` 生成，请勿手工编辑。",
        "",
        "**这不是真实语料上的准确率评测。** 场景期望值按默认策略表推导，",
        "衡量的是「引擎是否按写下来的规则办事」。真实语料评测需要人工标注集，",
        "本仓库不声称拥有这样的数据。",
        "",
        "## 环境",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Python：{platform.python_version()}",
        f"- 场景总数：{total}",
        "",
        "## 结果",
        "",
        "| 指标 | 数值 | 说明 |",
        "| --- | --- | --- |",
        f"| 状态一致率 | {passed}/{total} | 清理后的实际状态与期望一致 |",
        f"| 删除精确率 | {result['deletion_precision']:.3f} | 被删条目里有多少是该删的 |",
        f"| 删除召回率 | {result['deletion_recall']:.3f} | 该删的条目里删掉了多少 |",
        f"| 核心知识保留率 | {result['core_kept']}/{result['core_total']} | science + supplement 一条不丢 |",
        f"| 纠错链完整率 | {result['chains_ok']}/{result['corrected_total']} | 双向指针齐全、假科普仍可查 |",
        f"| dry-run 一致性 | {'一致' if result['preview_consistent'] else '不一致'} | 预演结果等于实际结果 |",
        f"| 清理幂等 | {'是' if result['idempotent'] else '否'} | 连跑两次，第二次处理 0 条 |",
        f"| 悬空引用 | {len(result['integrity']['dangling_superseded_by']) + len(result['integrity']['dangling_corrects'])} | 应为 0 |",
        "",
        "## 明细",
        "",
        f"- 本次删除：{', '.join(result['deleted']) or '（无）'}",
        f"- 预演删除：{', '.join(result['preview_deleted']) or '（无）'}",
    ]
    if mismatches:
        lines += ["", "### 不一致条目", "", "| id | 类型 | 期望 | 实际 | 备注 |", "| --- | --- | --- | --- | --- |"]
        lines += [
            f"| {m['id']} | {m['type']} | {m['expected']} | {m['actual']} | {m['note']} |"
            for m in mismatches
        ]
    else:
        lines += ["", "未发现不一致条目。"]
    lines += [
        "",
        "## 局限",
        "",
        "- 场景是合成的，容量小，只覆盖默认策略表；",
        "- 不衡量抽取/分类质量，也不衡量真实新闻流上的清理收益；",
        "- 若要对外报告效果，必须补充人工标注集与标注协议，另行测量。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="合成场景一致性评测")
    parser.add_argument("--no-write", action="store_true", help="只打印，不写 docs/EVALUATION.md")
    args = parser.parse_args()

    result = run(build_scenarios())
    markdown = render_markdown(result)
    print(markdown)

    if not args.no_write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(markdown, encoding="utf-8", newline="\n")
        print(f"\n已写入 {REPORT_PATH}")

    failed = bool(result["mismatches"]) or not result["idempotent"] or not result["preview_consistent"]
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
