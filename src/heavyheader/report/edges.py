"""边级成本报告的渲染（Markdown / JSON）。

报告的第一句话必须是**免责与口径**：这些毫秒是「删掉这条 include 后少解析的
预估」，不是「一定能省这么多」。同一条边在 verify 里可能根本编译不过。
把口径写在表格之前，是为了让任何摘走这张表的人先看到它。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..attrib.edgecost import EdgeCost
from ..edgecollect import EdgeUnit

_CAVEAT = (
    "口径：毫秒为 `-ftime-trace` 自耗时按「独占闭包」求和，回答"
    "「删掉这条 include 后少解析多少」，**不是**「一定能省这么多」，"
    "也不代表能删（可行性由 `verify` 的 A/B 实测判定）。"
    "闭包只统计真的会消失的文件——被别处 include guard 拉住的头不计入。"
)


def _short(path: str) -> str:
    return Path(path).name


def render_edges_markdown(
    costs: Sequence[EdgeCost], units: Sequence[EdgeUnit], *, top: int = 15
) -> str:
    ok_units = [u for u in units if u.ok]
    failed = [u for u in units if not u.ok]
    total_seconds = sum(u.self_seconds for u in ok_units)

    lines = [
        "# 边级解析成本",
        "",
        f"{_CAVEAT}",
        "",
        f"- 编译单元：{len(ok_units)}/{len(units)} 成功",
        f"- 解析总耗时（自耗时合计）：{total_seconds:.1f}s",
        f"- 有边际成本的边：{len(costs)} 条",
    ]
    if failed:
        names = "、".join(u.source.name for u in failed[:5])
        more = "" if len(failed) <= 5 else f" 等 {len(failed)} 个"
        lines.append(f"- **采集失败的单元**：{names}{more}（失败不会被跳过，如实列出）")
    lines += [
        "",
        "| # | 父文件 | 被包含 | 编译单元 | 边际毫秒 | 边际行数 |",
        "|---|---|---|---|---|---|",
    ]
    for index, cost in enumerate(costs[:top], 1):
        lines.append(
            f"| {index} | `{_short(cost.parent)}` | `{_short(cost.child)}` "
            f"| {cost.tus} | {cost.marginal_seconds * 1000:.1f} "
            f"| {cost.marginal_lines:,} |"
        )
    if not costs:
        lines.append("| — | — | — | — | — | — |")
    lines += [
        "",
        "排序依据是边际秒数；边际行数并列给出，便于看出两者何时分歧"
        "（行数大不等于耗时大，见 experiments/edge-precision-20261019.md）。",
        "",
    ]
    return "\n".join(lines)


def edges_to_dict(costs: Sequence[EdgeCost], units: Sequence[EdgeUnit]) -> dict:
    return {
        "caveat": _CAVEAT,
        "units": [
            {
                "source": str(u.source),
                "ok": u.ok,
                "wall_seconds": round(u.wall_seconds, 3),
                "self_seconds": round(u.self_seconds, 3),
                "edges": u.edge_count,
                "note": u.note,
            }
            for u in units
        ],
        "edges": [
            {
                "parent": c.parent,
                "child": c.child,
                "tus": c.tus,
                "marginal_seconds": round(c.marginal_seconds, 4),
                "marginal_lines": c.marginal_lines,
            }
            for c in costs
        ],
    }
