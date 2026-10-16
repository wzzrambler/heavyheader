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
    caveats: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        if self.total_tus == 0:
            return 0.0
        return self.eligible_tus / self.total_tus * 100.0

    @property
    def is_worthwhile(self) -> bool:
        """Whether the estimate is big enough to be worth acting on.

        A PCH that saves 2% of the build is not worth the ordering
        constraints it imposes.  The threshold is deliberately high.
        """
        return self.percent_of_project >= 10.0 and self.eligible_tus >= 2


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

    caveats = [
        "基于行数代理，不是秒数。模板重型头文件的实际成本会被低估。",
        "上限估计：真实 PCH 必须是每个使用它的 TU 的第一个 include，"
        "且各 TU 间字节一致。include 顺序不一致的项目实际收益会更低。",
        "未计入 PCH 失效：改动 PCH 中任何一个头文件都会让所有 TU 的 PCH 作废，"
        "因此 PCH 只应放稳定的头文件（第三方库、极少改动的内部基础库）。",
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
        caveats=caveats,
    )
