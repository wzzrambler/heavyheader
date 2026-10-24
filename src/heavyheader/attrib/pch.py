"""Precompiled-header benefit estimation.

A PCH is the highest-leverage single change available to a slow C++ build:
the headers in it are parsed once instead of once per translation unit.  But
deciding *what* to put in it is guesswork in most teams -- usually "the
headers everyone includes", which is often wrong.

This module answers it from the include graph we already have.

The model
---------
For a candidate PCH containing header set ``P``::

    pch_closure(P)     = P ∪ ⋃ closure(h)  for h in P
    eligible_tus(P)    = TUs whose closure contains P
    gross_saving       = |eligible_tus| × Σ lines(h) for h in pch_closure(P)
    net_saving         = gross_saving − Σ lines(h)   (the one-time PCH build)

``gross_saving`` is the header text that stops being re-parsed; the build cost
is that same text parsed exactly once.  Everything is expressed in *lines*,
the same proxy the rest of the tool uses.

Honesty boundaries -- read before quoting these numbers
-------------------------------------------------------
1. **Lines are a proxy, not seconds.**  A 100-line template-heavy header can
   cost more than a 1000-line declaration-only one.  Treat the estimate as a
   ranking, not a promise.
2. **The model ignores PCH ordering.**  A real PCH must be the *first* include
   in every TU that uses it, and must be byte-identical across those TUs.
   ``eligible_tus`` here only checks reachability, so the estimate is an
   **upper bound**.  A project where headers are included in varying orders
   will realise less.
3. **It does not account for PCH invalidation.**  Touching one header in the
   PCH invalidates it for every TU, so the PCH set should contain *stable*
   headers.  The recommendation reports each header's churn risk qualitatively
   but does not measure it.
4. **No PCH is generated.**  This is an estimate, not a build change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..models import HeaderKind, ProjectScan

__all__ = ["PCHRecommendation", "estimate_pch"]


@dataclass(slots=True)
class PCHRecommendation:
    """What to put in a precompiled header, and what it would buy."""

    headers: list[Path]
    #: Headers in the PCH plus everything they transitively pull in.
    closure_headers: list[Path]
    closure_lines: int
    #: TUs that include every recommended header.
    eligible_tus: int
    total_tus: int
    #: Lines that stop being re-parsed, minus the one-time PCH build.
    net_lines_saved: int
    #: That saving as a percentage of the project's attributed lines.
    percent_of_project: float
    #: 维护税：闭包里被最多单元包含的那个头——改动它会让整份 .gch 作废，
    #: 这些单元必须全部重编。只报「省多少」不报这个税，建议就不完整。
    rebuild_header: Path | None = None
    rebuild_units: int = 0
    #: 上述单元一次重编的耗时合计（逐单元实测墙钟之和）。
    rebuild_seconds: float = 0.0
    caveats: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        if self.total_tus == 0:
            return 0.0
        return self.eligible_tus / self.total_tus * 100.0

    @property
    def rebuild_share(self) -> float:
        """维护税覆盖了多少比例的编译单元。1.0 = 改一次等于全量重编。"""
        if self.total_tus == 0:
            return 0.0
        return self.rebuild_units / self.total_tus

    @property
    def is_worthwhile(self) -> bool:
        """Whether the estimate is big enough to be worth acting on.

        A PCH that saves 2% of the build is not worth the ordering
        constraints it imposes.  The threshold is deliberately high.
        """
        return self.percent_of_project >= 10.0 and self.eligible_tus >= 2


def _tu_seconds(unit: object) -> float:
    """一个单元的编译耗时：优先用驱动实测墙钟，退化到 ``-ftime-report`` 的 TOTAL。

    两者不可混用（墙钟含进程启动，TOTAL 是编译器自报 CPU），但都是「这一单元
    要花多久」的量级答案；维护税只需要量级。
    """
    wall = getattr(unit, "wall_seconds", 0.0) or 0.0
    if wall:
        return float(wall)
    for timing in getattr(unit, "timings", ()) or ():
        if getattr(timing, "is_total", False):
            return float(timing.wall_seconds)
    return 0.0


def estimate_pch(
    scan: ProjectScan,
    *,
    top_k: int = 5,
    min_reach: int = 2,
    line_counter=None,
) -> PCHRecommendation:
    """Estimate the benefit of a PCH built from the most-included headers.

    ``top_k`` caps how many headers go into the PCH -- a bigger PCH is not
    automatically better, because every header added must then be included
    first by every TU that wants to use it.
    """
    if line_counter is None:
        from .graph import HeaderSizes

        sizes = HeaderSizes()
        line_counter = sizes.lines

    project_headers = [
        stats
        for stats in scan.header_stats.values()
        if stats.kind is HeaderKind.PROJECT and stats.tu_reach >= min_reach
    ]
    if not project_headers:
        return PCHRecommendation(
            headers=[],
            closure_headers=[],
            closure_lines=0,
            eligible_tus=0,
            total_tus=len(scan.ok_tus),
            net_lines_saved=0,
            percent_of_project=0.0,
            caveats=["没有找到被多个编译单元引用的项目头文件，PCH 无适用场景。"],
        )

    # Build the include adjacency once; every candidate's closure is needed.
    adjacency: dict[Path, set[Path]] = {}
    for tu in scan.ok_tus:
        for event in tu.include_events:
            adjacency.setdefault(event.includer, set()).add(event.included)

    def closure_of(start: Path) -> set[Path]:
        seen: set[Path] = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for child in adjacency.get(current, ()):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    # Rank by (reach, closure lines) -- NOT by the header's own line count.
    #
    # Ranking by own size is a real mistake: if top.h (10 lines) pulls in
    # mid.h and deep.h, its closure is 100 lines, while deep.h alone is 70.
    # With equal reach, top.h is the strictly better PCH candidate, and a
    # size-based ranking picks the worse one.  A unit test pins this.
    ranked: list[tuple[Path, int, int]] = []
    for stats in project_headers:
        closure = closure_of(stats.path)
        closure_lines = sum(line_counter(p) for p in closure)
        ranked.append((stats.path, stats.tu_reach, closure_lines))
    ranked.sort(key=lambda item: (item[1], item[2]), reverse=True)

    chosen = ranked[: max(1, top_k)]
    chosen_paths = [path for path, _, _ in chosen]

    # Union of the chosen headers' transitive closures.
    closure: set[Path] = set(chosen_paths)
    stack = list(chosen_paths)
    while stack:
        current = stack.pop()
        for child in adjacency.get(current, ()):
            if child not in closure:
                closure.add(child)
                stack.append(child)

    # A TU is eligible only if it already pulls in *every* chosen header.
    eligible = 0
    for tu in scan.ok_tus:
        reached = {e.included for e in tu.include_events}
        if all(path in reached for path in chosen_paths):
            eligible += 1

    closure_lines = sum(line_counter(p) for p in closure)
    gross = closure_lines * eligible
    net = gross - closure_lines
    project_total = scan.total_included_lines() or 1

    # 维护税：PCH 的代价不是零。改动闭包里任何一个头，整份 .gch 失效，
    # 包含它的单元全部重编——最坏情况就是闭包里被最多单元包含的那个头。
    # 这一项以前只写在 caveat 里（「未计入」），现在给出量化：
    # 省多少是收益，税多少是风险，两者必须同时出现才是一个可采纳的建议。
    rebuild_header: Path | None = None
    rebuild_units = 0
    rebuild_lines = -1
    for path, stats in scan.header_stats.items():
        if path not in closure:
            continue
        # 只算**项目**头：系统头（bits/stl_algo.h 之类）谁都改不了，
        # 把它算成维护税只会把风险说大。项目头为空时才退回系统头。
        if stats.kind is not HeaderKind.PROJECT:
            continue
        # 先比触达单元数，平局时比自身行数：63/63 全触达时，用户实际会改的
        # 是那个大而常改的头（json.hpp），不是顺带被拉进来的小适配头。
        key = (stats.tu_reach, stats.line_count)
        if key > (rebuild_units, rebuild_lines):
            rebuild_header, rebuild_units, rebuild_lines = path, *key
    rebuild_seconds = 0.0
    if rebuild_header is not None:
        for tu in scan.ok_tus:
            if rebuild_header in {event.included for event in tu.include_events}:
                rebuild_seconds += _tu_seconds(tu)

    caveats = [
        "基于行数代理，不是秒数。模板重型头文件的实际成本会被低估。",
        "上限估计：真实 PCH 必须是每个使用它的 TU 的第一个 include，"
        "且各 TU 间字节一致。include 顺序不一致的项目实际收益会更低。",
        "维护税（rebuild_seconds）只计受影响单元的重编耗时，不含 .gch 自身的重建时间；"
        "它衡量的是「改一次要付多少」，不是「每次构建都要付」。",
        "本工具不生成 PCH，只做估算。",
    ]

    return PCHRecommendation(
        headers=chosen_paths,
        closure_headers=sorted(closure),
        closure_lines=closure_lines,
        eligible_tus=eligible,
        total_tus=len(scan.ok_tus),
        net_lines_saved=net,
        percent_of_project=net / project_total * 100.0,
        rebuild_header=rebuild_header,
        rebuild_units=rebuild_units,
        rebuild_seconds=rebuild_seconds,
        caveats=caveats,
    )
