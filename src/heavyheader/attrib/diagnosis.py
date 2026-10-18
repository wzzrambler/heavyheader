"""When there is nothing to delete: say where the cost is and why it stays.

Why this module exists
----------------------
G2 ran the removal pipeline on three projects.  doctest produced one adopted
change; nlohmann/json and zlib produced **zero** feasible candidates.  The
honest report for those two is an empty candidate table -- and an empty table
is routinely read as "your build is fine", which is the opposite of what
happened.  The pipeline found plenty of expensive ``#include`` lines and
rejected every single one of them.  *Why* it rejected them is the finding.

So when the feasibility gate rejects everything, the scan still has to answer
three questions:

* where does the parse cost actually sit -- which headers, and are they even
  ours to edit?
* which rejections were the expensive ones, and what specifically blocked
  each of them?
* what can the reader do next that is not "delete a line"?

What it deliberately does not do
--------------------------------
It never relaxes the gate to produce a suggestion.  A tool that answers "no
removals found" with "how about this one, it might break your build" has
traded a correct negative for a plausible positive, and the G2 measurements
say where that road ends: on nlohmann/json every ungated candidate failed to
compile.  The gate is the product; this module only explains it.

Evidence levels
---------------
The concentration numbers are measured (compiler-reported inclusion trees and
line counts).  The *reasons* are a textual heuristic, and the advice is
derived from those reasons -- it points at what to look at, never at what to
change without :mod:`heavyheader.verify`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import HeaderKind, ProjectScan

__all__ = [
    "BLOCK_REASONS",
    "BlockedSite",
    "ConcentrationRow",
    "SiteDiagnosis",
    "build_diagnosis",
]

#: 机器码 -> 面向读者的中文说明。新增拦下原因必须同步这里，否则报表里
#: 会出现一个读者看不懂的英文键。
BLOCK_REASONS: dict[str, str] = {
    "owner_uses": "拥有这一行的文件自己正在使用这些符号（真依赖，不是误判）",
    "owner_unknown": "文本判定给不出结论——命中的标识符是环境噪音，工具选择不猜",
    "system_site": "这一行位于系统/第三方头文件里，不属于本项目可改的范围",
    "low_reach": "只有 1 个编译单元会解析它（归入编译单元级候选处理）",
    "shadowed": "删掉它也不省钱：同一批头文件已经从别的 include 进来了",
    "unmeasured": "取不到这些头文件的行数，收益无法估算",
    "tiny": "收益低于展示门槛",
}


@dataclass(frozen=True, slots=True)
class BlockedSite:
    """One ``#include`` line that looked expensive and was rejected."""

    site_file: Path
    header: Path
    line: int | None = None
    #: ``独占行数 × 受影响单元数`` —— 与候选表同一口径，这样「被拦下的」和
    #: 「被推荐的」可以用同一个数字比较大小。
    amplified_lines: int = 0
    reach: int = 0
    reason: str = ""
    #: 具体到标识符的证据。留空说明没有可引用的证据，报表必须显示「无」
    #: 而不是补一句听起来合理的解释。
    detail: str = ""

    @property
    def reason_label(self) -> str:
        return BLOCK_REASONS.get(self.reason, self.reason)

    @property
    def rank_key(self) -> tuple[int, str, str]:
        # 路径作 tie-breaker 的理由与 HeaderStats.cost_rank_key 相同：
        # 并列顺序依赖集合迭代顺序会随哈希随机化变化，同一份数据两次运行
        # 给出不同的「最贵的一条」会让这份诊断无法复现。
        return (-self.amplified_lines, str(self.site_file), str(self.header))


@dataclass(frozen=True, slots=True)
class ConcentrationRow:
    """Where the parse cost sits: one header, and how much of the total."""

    path: Path
    kind: HeaderKind
    attributed_lines: int
    share: float

    @property
    def rank_key(self) -> tuple[int, str]:
        return (-self.attributed_lines, str(self.path))


@dataclass(slots=True)
class SiteDiagnosis:
    """The answer to "why is there nothing to delete"."""

    total_attributed_lines: int = 0
    concentration: list[ConcentrationRow] = field(default_factory=list)
    #: 归因行数里落在项目自有头文件上的比例。低 = 成本在别人的头里。
    project_share: float = 0.0
    #: 前 N 个头占的比例。高 = 成本高度集中，值得定向治理而不是逐行删。
    top_share: float = 0.0
    blocked_top: list[BlockedSite] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    #: 通过可行性闸门、真正进入候选表的条数（只统计头文件级）。
    candidate_count: int = 0
    advice: list[str] = field(default_factory=list)

    @property
    def is_empty_handed(self) -> bool:
        """闸门交了白卷：一条可推荐的改动都没有。"""
        return self.candidate_count == 0

    @property
    def top_share_percent(self) -> float:
        return round(self.top_share * 100.0, 1)

    @property
    def project_share_percent(self) -> float:
        return round(self.project_share * 100.0, 1)


def build_diagnosis(
    scan: ProjectScan,
    blocked,
    *,
    top: int = 10,
    blocked_top: int = 8,
    candidate_count: int = 0,
) -> SiteDiagnosis:
    """Summarise cost concentration and the rejections behind an empty list.

    ``blocked`` is the list filled in by
    :func:`heavyheader.attrib.sites.find_header_candidates`.  Passing ``None``
    is allowed and yields a diagnosis with no rejection rows -- the cost
    concentration is still computed, because it comes from the scan itself.

    All three orderings here carry a path tie-breaker; see
    :attr:`BlockedSite.rank_key` for why that is not optional.
    """
    total = scan.total_included_lines()
    rows: list[ConcentrationRow] = []
    if total > 0:
        for stats in sorted(
            scan.header_stats.values(), key=lambda h: h.cost_rank_key, reverse=True
        )[:top]:
            rows.append(
                ConcentrationRow(
                    path=stats.path,
                    kind=stats.kind,
                    attributed_lines=stats.attributed_lines,
                    share=stats.attributed_lines / total,
                )
            )
    project_lines = sum(
        stats.attributed_lines
        for stats in scan.header_stats.values()
        if stats.kind is HeaderKind.PROJECT
    )
    project_share = (project_lines / total) if total else 0.0
    top_share = sum(row.share for row in rows)

    rejected = sorted(blocked or [], key=lambda item: item.rank_key)
    counts: dict[str, int] = {}
    for item in rejected:
        counts[item.reason] = counts.get(item.reason, 0) + 1

    return SiteDiagnosis(
        total_attributed_lines=total,
        concentration=rows,
        project_share=project_share,
        top_share=top_share,
        blocked_top=rejected[:blocked_top],
        counts=counts,
        candidate_count=candidate_count,
        advice=_advice(scan, rows, counts, project_share, candidate_count),
    )


def _advice(
    scan: ProjectScan,
    rows: list[ConcentrationRow],
    counts: dict[str, int],
    project_share: float,
    candidate_count: int,
) -> list[str]:
    """Next steps that are not "delete a line".

    Every item is derived from a number in this diagnosis or in the scan, and
    every item names the lever it refers to.  Nothing here is a removal
    recommendation: the last line says so, and it is not boilerplate -- the
    whole point of this module is that an empty candidate list is a result,
    not a prompt to lower the bar.
    """
    out: list[str] = []
    total_considered = sum(counts.values())

    if candidate_count == 0:
        out.append(
            f"可行性闸门放行了 0 条改动（共审视 {total_considered} 条收益非零的 "
            "include 行）。这是结论不是故障：本轮没有一条「删掉仍能编译」的候选，"
            "工具不会为了给出建议而降低这道门槛。"
        )
    else:
        out.append(
            f"可行性闸门放行 {candidate_count} 条；以下成本分布说明其余 "
            f"{total_considered} 条为什么没进候选表。"
        )

    if project_share < 0.5:
        out.append(
            f"归因行数里只有 {project_share * 100:.1f}% 落在项目自有头文件上，"
            "其余来自系统/第三方头。删除 include 行对这部分没有用——能做的是减少"
            "**包含**它们的项目头数量（拆分伞头、改前向声明），或用 `--pch` 把它们"
            "预编译掉。这两项本工具都只给估算，收益仍需实测。"
        )
    else:
        out.append(
            f"归因行数里 {project_share * 100:.1f}% 落在项目自有头文件上——成本在"
            "自己手里，方向是拆头文件而不是删 include 行。"
        )

    owner_uses = counts.get("owner_uses", 0)
    if owner_uses:
        out.append(
            f"{owner_uses} 条被拦下的原因是「拥有该行的文件自己就在用这些符号」。"
            "这是真实依赖：删掉它编译不过，工具判对了。要动这类成本得换手段——"
            "把大头文件拆小（`--forward-decl` 找可改前向声明的位置）、把不常用的"
            "实现挪到 .cpp，而不是删这一行。"
        )
    unknown = counts.get("owner_unknown", 0)
    if unknown:
        out.append(
            f"{unknown} 条文本判定给不出结论：命中的标识符（`config`、`value` 这类）"
            "在几十个头里都出现，不能当作依赖证据，也不能当作没在用。"
            "这些需要人工看一眼，或用语义工具（本工具不假装替你判）。"
        )
    shadowed = counts.get("shadowed", 0)
    if shadowed:
        out.append(
            f"{shadowed} 条即使删掉也不省钱：同一批头文件已从其它 include 到达，"
            "属于「看起来冗余、实际由别处承担」。治理这类成本要从那个真正的入口下手。"
        )
    system = counts.get("system_site", 0)
    if system:
        out.append(
            f"{system} 条位于系统/第三方头文件内部。改别人的头文件不是本项目能"
            "承担的决策，故不列入候选（可加 `--include-system-sites` 强制列出，仅供查看）。"
        )
    if not rows:
        out.append("没有可用的头文件统计（可能全部编译单元失败），上述分布无法计算。")

    out.append(
        "本诊断不构成删除建议：任何删除都必须先过 `verify` 重新编译全部受影响"
        "单元的实测，估算省下的行数或秒数都不能作为安全性依据。"
    )
    return out
