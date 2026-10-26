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

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..models import HeaderKind, ProjectScan

__all__ = ["PCHRecommendation", "estimate_pch", "macro_prefixed_units"]

# ``#define`` / ``#undef``，允许 ``#  define`` 这种带空格的写法。
_MACRO_DIRECTIVE = re.compile(r"^\s*#\s*(?:define|undef)\b")
# ``#include <x.h>`` / ``#include "x.h"``，只取引号里的部分。
_INCLUDE_DIRECTIVE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')


def macro_prefixed_units(
    scan: ProjectScan, headers: list[Path]
) -> list[tuple[Path, int, str]]:
    """找出「在包含某个候选头之前就已经改过宏」的编译单元。

    为什么这件事值得单独报：``-include`` 把 PCH 的内容插到每个单元的最前面，
    于是被预编译的头看到的是**它原本出现位置之前**那套宏状态。如果一个单元
    先 ``#define JSON_TESTS_PRIVATE`` 再 ``#include <json.hpp>``，头文件在
    这个单元里本该看到这个宏，但 PCH 已经用「宏没定义」的状态把它编译过一次了。
    结果要么编译失败（宏改变了类定义），要么更糟——编过了但语义不同。

    这是 2026-10-17 完整构建实验要靠「跑测试比对断言数」才能发现的坑，也是
    ``pch-verify`` 在 json 上实测到 24 个单元里 9 个编不过的原因。这里把它
    降级成一个**静态可算的预警**：不需要编译，只读源文件。

    判据刻意简单，且**只报风险、不报结论**：
    * 只看源文件里**直接**出现候选头的那一行之前；间接包含（经别的头拉进来）
      不判——那要先展开包含链，而链上的宏状态本来就是编译器的事。
    * 任何宏指令都算（``#define`` 和 ``#undef``），不区分它改的是配置宏还是
      普通宏。区分不了：要知道某个宏会不会影响头文件的类定义，得读头文件。

    实测精度（json 单元测试，24 个单元，见
    ``experiments/results/pch-verify-json63-20261025.md``）：命中 10 个，其中 9 个
    加 PCH 后编不过，**零漏报**——没被标记的 14 个全部编得过。所以它的正确用法是
    「把需要警惕的单元缩到 1/2」，不是「预言哪几个会炸」。
    """
    if not headers:
        return []
    wanted = {header.name for header in headers}
    out: list[tuple[Path, int, str]] = []
    for tu in scan.ok_tus:
        reached = {event.included for event in tu.include_events}
        if not any(header in reached for header in headers):
            continue
        found = _first_macro_before_source_include(tu.source, wanted)
        if found is not None:
            out.append((tu.source, found[0], found[1]))
    return out


def _first_macro_before_source_include(
    source: Path, wanted_names: set[str]
) -> tuple[int, str] | None:
    """读一个源文件，返回「任一候选头的 include 之前」的第一条宏指令。

    **必须查完所有候选头，不能停在第一个。** PCH 里往往不止一个头（json 上贪心
    选出了 ``doctest_compatibility.h`` 与 ``json.hpp`` 两个），而单元通常先
    include 前者、再 ``#define``、再 include 后者。第一版在遇到第一个候选头时
    就返回（那时前缀还是空的），于是 json 上一条预警都没报出来——而这个函数存在的
    全部理由就是抓那 9 个单元。
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # 读不到源文件就不猜。少报一条预警远好于因为 IO 错误编一条出来。
        return None
    prefix: tuple[int, str] | None = None
    for lineno, line in enumerate(text.splitlines(), 1):
        include = _INCLUDE_DIRECTIVE.match(line)
        if include and Path(include.group(1)).name in wanted_names and prefix is not None:
            return prefix
        if prefix is None and _MACRO_DIRECTIVE.match(line):
            prefix = (lineno, line.strip())
    return None


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
    #: 维护税：闭包里被最多单元包含的那个头——它是「最可能被改、且改一次
    #: 代价最大」的代表。只报「省多少」不报这个税，建议就不完整。
    rebuild_header: Path | None = None
    #: **有 PCH 时**，改动 ``rebuild_header`` 会导致多少个单元重编。
    #: 不是这个头自己触达的单元数：.gch 作废后，每个使用它的单元都要重新
    #: 生成 .gch 再编译自己，所以是所有「包含至少一个候选头」的单元。
    rebuild_units: int = 0
    #: **无 PCH 时**，改动 ``rebuild_header`` 只需重编多少个单元
    #: （= 它自己的触达数）。与 ``rebuild_units`` 的差就是放进 PCH 的净增风险。
    baseline_rebuild_units: int = 0
    #: ``rebuild_units`` 个单元一次重编的耗时合计（逐单元实测墙钟之和）。
    rebuild_seconds: float = 0.0
    #: 闭包里触达**小于**使用 PCH 的单元数的项目头，按放大倍数降序：
    #: ``(头, 它自己的触达数, 放大倍数)``。这些头只被一部分单元使用，却因为
    #: 被固化进 .gch 而让全体使用 PCH 的单元陪着重编。空列表表示这个 PCH
    #: 没有这类风险（所有项目头都是全工程公共依赖）。
    fragile_headers: list[tuple[Path, int, float]] = field(default_factory=list)
    #: 在包含某个候选头**之前**就改过宏的单元：``(源文件, 行号, 宏指令原文)``。
    #: 这些单元一旦采用 PCH，被预编译的头看到的宏状态就与它原本该看到的不同——
    #: 轻则编不过，重则编过但语义变了。静态可算，不需要编译。
    #: 实测精度见 :func:`macro_prefixed_units`。
    macro_prefixed_units: list[tuple[Path, int, str]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def macro_prefixed_count(self) -> int:
        return len(self.macro_prefixed_units)

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
    def rebuild_amplification(self) -> float:
        """放进 PCH 让「改一次」的代价放大多少倍。

        ``rebuild_units / baseline_rebuild_units``。1.0 表示放进 PCH 不增加
        风险（这个头本来就被所有会用 PCH 的单元包含）；远大于 1 表示这个头
        只被少数单元包含，但改它会让全体使用 PCH 的单元陪着重编。
        """
        if self.baseline_rebuild_units <= 0:
            return 0.0
        return self.rebuild_units / self.baseline_rebuild_units

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

    The maintenance tax is reported alongside the saving (see
    :attr:`PCHRecommendation.fragile_headers`).  It is deliberately *not*
    folded into the ranking: an earlier version scored candidates by
    ``reach - (reach - min reach in closure)``, which is identically zero
    because ``tu_reach`` counts transitive includes, so every header in a
    candidate's closure is included by at least as many TUs as the candidate
    itself.  A ranking term that can never fire is worse than no term.
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
    #
    # Do NOT add a "maintenance tax" term here.  ``tu_reach`` counts
    # transitive includes, so every header in a candidate's closure is
    # pulled in by at least as many TUs as the candidate itself; any
    # ``reach - min(reach in closure)`` term is identically zero.  The tax
    # is a property of the *set* (see ``fragile_headers`` below), not of a
    # single candidate, so it is reported after the set is chosen.
    ranked: list[tuple[Path, int, int]] = []
    for stats in project_headers:
        closure = closure_of(stats.path)
        closure_lines = sum(line_counter(p) for p in closure)
        ranked.append((stats.path, stats.tu_reach, closure_lines))
    ranked.sort(key=lambda item: (item[1], item[2]), reverse=True)

    # Greedily add the header that increases the net saving the most, and
    # stop as soon as no remaining header adds anything.  Taking a plain
    # ``top_k`` slice is a real bug on partially-covered projects: a header
    # included by half the units enlarges the closure (so the one-time PCH
    # build grows) while cutting ``eligible`` in half, and the net saving
    # *drops*.  A 10-unit demo corpus measures 13,527 lines for
    # ``{common.h}`` versus 12,024 for ``{common.h, special.h}`` -- the
    # top-2 slice picks the worse set.  A unit test pins this.
    #
    # The ranking above is only the tie-break/seed order; the objective is
    # the net saving itself, which is what makes the maintenance tax show
    # up here without any extra weight: a header that drags a rarely-used
    # header into the closure is exactly the one that stops paying.
    chosen: list[Path] = []
    best_net = 0
    remaining = list(ranked)
    while remaining and len(chosen) < max(1, top_k):
        best_index = None
        best_candidate_net = best_net
        for index, (path, _, _) in enumerate(remaining):
            trial = chosen + [path]
            trial_closure = set(trial)
            stack = list(trial)
            while stack:
                current = stack.pop()
                for child in adjacency.get(current, ()):
                    if child not in trial_closure:
                        trial_closure.add(child)
                        stack.append(child)
            trial_eligible = sum(
                1
                for tu in scan.ok_tus
                if all(
                    p in {event.included for event in tu.include_events}
                    for p in trial
                )
            )
            trial_lines = sum(line_counter(p) for p in trial_closure)
            trial_net = trial_lines * trial_eligible - trial_lines
            if trial_net > best_candidate_net:
                best_candidate_net = trial_net
                best_index = index
        if best_index is None:
            break
        chosen.append(remaining.pop(best_index)[0])
        best_net = best_candidate_net

    chosen_paths = list(chosen)

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
    # That is the conservative *benefit*口径：only these TUs are certain to
    # need everything the PCH would contain.
    eligible_tus: list = []
    for tu in scan.ok_tus:
        reached = {event.included for event in tu.include_events}
        if all(path in reached for path in chosen_paths):
            eligible_tus.append(tu)
    eligible = len(eligible_tus)

    # The maintenance tax has a different口径：.gch invalidation hits every TU
    # that *uses* the PCH, which is any TU including at least one chosen
    # header -- not just the eligible ones, and not just the TUs that
    # included the header that changed.
    pch_users: list = []
    for tu in scan.ok_tus:
        reached = {event.included for event in tu.include_events}
        if any(path in reached for path in chosen_paths):
            pch_users.append(tu)

    closure_lines = sum(line_counter(p) for p in closure)
    gross = closure_lines * eligible
    net = gross - closure_lines
    project_total = scan.total_included_lines() or 1

    # 维护税：PCH 的代价不是零。.gch 失效后，**每一个使用 PCH 的单元**都要
    # 重新生成 .gch 再编译自己，与是哪个头触发失效无关。所以影响面按
    # pch_users 计；无 PCH 时改同一个头只影响它自己触达的单元，两者的差
    # 就是放进 PCH 的净增风险。
    rebuild_header: Path | None = None
    rebuild_reach = 0
    rebuild_lines = -1
    for path, stats in scan.header_stats.items():
        if path not in closure:
            continue
        # 只算**项目**头：系统头（bits/stl_algo.h 之类）谁都改不了，
        # 把它算成维护税只会把风险说大。
        if stats.kind is not HeaderKind.PROJECT:
            continue
        # 先比触达单元数，平局时比自身行数：全触达时，用户实际会改的是
        # 那个大而常改的头（json.hpp），不是顺带被拉进来的小适配头。
        key = (stats.tu_reach, stats.line_count)
        if key > (rebuild_reach, rebuild_lines):
            rebuild_header, rebuild_reach, rebuild_lines = path, *key

    # 脆弱头清单：闭包里触达**小于** pch_users 的项目头。这些头只被一部分
    # 单元使用，却因为被固化进 .gch 而让全体使用 PCH 的单元陪着重编——
    # 放大倍数就是 users / reach。这是集合层面的税，单个候选头身上量不到
    # （tu_reach 含传递包含，闭包头触达必然不小于候选头自身）。
    fragile: list[tuple[Path, int, float]] = []
    for path, stats in scan.header_stats.items():
        if path not in closure:
            continue
        if stats.kind is not HeaderKind.PROJECT or stats.tu_reach <= 0:
            continue
        if stats.tu_reach >= len(pch_users):
            continue
        fragile.append((path, stats.tu_reach, len(pch_users) / stats.tu_reach))
    fragile.sort(key=lambda item: (-item[2], -item[1], str(item[0])))

    rebuild_units = len(pch_users) if rebuild_header is not None else 0
    rebuild_seconds = 0.0
    if rebuild_header is not None:
        for tu in pch_users:
            rebuild_seconds += _tu_seconds(tu)

    prefixed = macro_prefixed_units(scan, chosen_paths)

    caveats = [
        "基于行数代理，不是秒数。模板重型头文件的实际成本会被低估。",
        "上限估计：真实 PCH 必须是每个使用它的 TU 的第一个 include，"
        "且各 TU 间字节一致。include 顺序不一致的项目实际收益会更低。",
        "维护税（rebuild_seconds）按「所有使用 PCH 的单元都要重新生成 .gch」计，"
        "不含 .gch 自身的构建时间；它衡量的是「改一次要付多少」，不是「每次构建都要付」。",
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
        baseline_rebuild_units=rebuild_reach,
        rebuild_seconds=rebuild_seconds,
        fragile_headers=[(path, reach, amp) for path, reach, amp in fragile],
        macro_prefixed_units=prefixed,
        caveats=caveats,
    )
