"""Header cost graph: the model behind every number HeavyHeader prints.

The insight this module is built on
-----------------------------------
A C++ project's preprocessing and parsing cost is, to a first approximation,
proportional to *how much header text the compiler is forced to read*::

    total_cost  ~=  sum over TUs  sum over headers processed in that TU  lines(h)

Every header the compiler actually parses contributes its own line count once
per translation unit that pulls it in.  That gives us a cost model with no
hand-waving in it -- it is just counting.

Two consequences fall straight out of the model and drive the whole tool:

1. **Attributed lines.**  ``lines(h) * reach(h)`` is how much text the
   project pays for header ``h``.  Sort by that and you get the headers that
   actually matter, rather than the ones that merely look big.

2. **Exclusive closure.**  Drop a direct ``#include`` and the only headers
   that stop being parsed are those reachable *through it and nowhere else*.
   Everything else was already being paid for by another include, so removing
   this one buys nothing.  Computing that exclusive set is what turns a vague
   "clean up your includes" suggestion into a ranked, quantified list.

The module is pure: it takes :class:`TranslationUnit` objects and header
sizes, and returns statistics and candidates.  It never touches a compiler.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..includes import find_include_line, find_include_lines
from ..models import (
    HeaderKind,
    HeaderStats,
    ProjectScan,
    RemovalCandidate,
    TranslationUnit,
    candidate_rank_key,
    tu_compile_seconds,
)
from .usage import analyse_usage

__all__ = [
    "HeaderSizes",
    "classify_headers",
    "build_header_stats",
    "find_removal_candidates",
    "redundant_reparse_warnings",
    "closure_of",
]


class HeaderSizes:
    """Line counts for headers, read lazily and cached.

    Reading is done once per path; a header that has vanished or is not
    decodable counts as zero lines rather than failing the whole scan.
    """

    def __init__(self) -> None:
        self._cache: dict[Path, int] = {}

    def lines(self, path: Path) -> int:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                count = sum(1 for _ in handle)
        except OSError:
            count = 0
        self._cache[path] = count
        return count

    def total(self, paths) -> int:
        return sum(self.lines(p) for p in paths)


def classify_headers(
    root: Path, all_headers: set[Path]
) -> dict[Path, HeaderKind]:
    """Split headers into project-owned and third-party.

    Anything living under ``root`` is ours; everything else -- the standard
    library, SDKs, vendored dependencies -- is reported for context but never
    proposed for removal.  We cannot fix someone else's headers, and pretending
    otherwise would make the report noise.
    """
    resolved_root = _resolved(root)
    kinds: dict[Path, HeaderKind] = {}
    for header in all_headers:
        try:
            resolved = header.resolve()
        except OSError:  # pragma: no cover - defensive
            resolved = header
        if _is_relative_to(resolved, resolved_root):
            kinds[header] = HeaderKind.PROJECT
        else:
            kinds[header] = HeaderKind.SYSTEM
    return kinds


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - defensive
        return path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _normalise_header_key(path: Path, root: Path) -> Path:
    """把一个头文件路径归一到用于统计的稳定 key。

    同一个文件，不同编译器（甚至同一个 MSVC 在不同 TU 里）会给出不同形式：
    GCC/Clang 是相对正斜杠，MSVC 可能是相对也可能是绝对反斜杠。若直接拿原始
    字符串当 key，同一个头会被当成多个，归因与跨编译器逐头对比都会错位。

    归一策略：先按系统分隔符统一，再把相对路径接到 ``root`` 上，最后
    ``resolve`` 成绝对真实路径。绝对路径对 ``classify_headers``（用路径是否在
    root 下来判定项目头）是无歧义的，也让 GCC/Clang 的相对路径与 MSVC 的
    绝对/相对多形态落到同一个 key 上。

    为什么相对路径必须接 ``root`` 而不是直接 ``resolve()``：``resolve()`` 以
    **进程当前目录**为基准。用户在仓库根运行 ``heavyheader scan src/`` 时，
    ``-H`` 给出的 ``include/core.h`` 会按仓库根解析成 ``<repo>/include/core.h``，
    而真实位置是 ``<repo>/src/include/core.h``——于是项目头被判成 SYSTEM，
    悄悄从成本表里消失。这类错误不报错、数字看起来正常，只会在换目录运行时
    才露出来，比崩溃更难发现。
    """
    normalised = Path(os.path.normpath(str(path).replace("\\", "/")))
    if not normalised.is_absolute():
        normalised = Path(root) / normalised
    try:
        return normalised.resolve()
    except OSError:
        return normalised


# --------------------------------------------------------------------------
# per-TU graph helpers
# --------------------------------------------------------------------------


def _build_adjacency(tu: TranslationUnit) -> dict[Path, list[Path]]:
    """``parent -> [children]`` for one TU, first-seen order, no duplicates.

    Because ``-H`` only reports inclusions the compiler actually processed,
    guarded headers appear exactly once and the graph we build here is the
    *effective* DAG -- which is precisely the graph that determines cost.
    """
    adjacency: dict[Path, dict[Path, None]] = defaultdict(dict)
    for event in tu.include_events:
        adjacency[event.includer][event.included] = None
    return {parent: list(children) for parent, children in adjacency.items()}


_INCLUDE_LINE = re.compile(
    r'^\s*#\s*include\s*[<"]([^">]+)[">]', re.MULTILINE
)


class IncludeMentions:
    """``file -> 它自己的 #include 行列出的头文件名``（按路径缓存的文本扫描）。

    为什么需要它：``-H`` 只报告**首次生效**的包含点。``type.h`` 里那句
    ``#include "prelude.h"`` 在 ``type.cpp`` 先包含过之后被 guard 吞掉，树里
    根本没有这条边。于是「删掉 ``type.cpp`` 里的直连 prelude.h」看起来会带走
    275 个头，实际上一个也带不走——实测 doctest 上 60 条候选里有 18 条属于
    这一类，全部浪费一次编译去证伪。

    成本核算必须继续用有效树（一个头只解析一次才叫成本）；只有**删除预测**
    需要这张补全图。同一个文件在多轮扫描间共享缓存。
    """

    __slots__ = ("_cache",)

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[str, ...]] = {}

    def of(self, path: Path) -> tuple[str, ...]:
        cached = self._cache.get(path)
        if cached is not None:
            return cached
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        names = tuple(match.group(1).strip() for match in _INCLUDE_LINE.finditer(text))
        self._cache[path] = names
        return names


def _resolve_mention(
    name: str, includer: Path, by_name: dict[str, list[Path]]
) -> list[Path]:
    """把一个 ``#include`` 里的名字对到本次真正解析过的文件。

    只认两种情形：① 相对包含者的目录能对上；② 这个名字在本次解析集合里
    **唯一**。同名多份且无法相对定位时返回空——宁可少补一条边（保守，
    闭包偏大），也不猜错把两个同名头混成一个。
    """
    candidates = by_name.get(Path(name).name)
    if not candidates:
        return []
    relative = (includer.parent / name).resolve()
    for path in candidates:
        try:
            if path.resolve() == relative:
                return [path]
        except OSError:  # pragma: no cover - 路径不可解析时退回唯一匹配
            break
    if len(candidates) == 1:
        return [candidates[0]]
    return []


def _potential_adjacency(
    tu: TranslationUnit, mentions: IncludeMentions, parsed: set[Path]
) -> dict[Path, list[Path]]:
    """有效树 + 文本补全的**潜在供给边**，用于「删掉这条会少解析什么」。

    与 :func:`heavyheader.attrib.edgecost.build_potential_graph` 同一思路、
    同一理由：被 include guard 抑制的第二次包含不会出现在 ``-H`` 里，但它
    确实提供了到达路径。差别是这里按**完整路径**建图（边级归因那侧按文件名
    聚合，因为它的输出是给人看的排名）。
    """
    adjacency: dict[Path, dict[Path, None]] = defaultdict(dict)
    for event in tu.include_events:
        adjacency[event.includer][event.included] = None

    by_name: dict[str, list[Path]] = defaultdict(list)
    for path in parsed:
        by_name[path.name].append(path)
    for paths in by_name.values():
        paths.sort()

    for path in parsed:
        for name in mentions.of(path):
            for target in _resolve_mention(name, path, by_name):
                if target != path:
                    adjacency[path][target] = None
    return {parent: list(children) for parent, children in adjacency.items()}


def closure_of(adjacency: dict[Path, list[Path]], start: Path) -> set[Path]:
    """Every header reachable from ``start``, excluding ``start`` itself.

    Iterative (no recursion) because real projects produce include chains
    thousands of levels deep and Python's default recursion limit is 1000.
    """
    seen: set[Path] = set()
    stack = list(adjacency.get(start, ()))
    while stack:
        current = stack.pop()
        if current in seen or current == start:
            continue
        seen.add(current)
        stack.extend(adjacency.get(current, ()))
    return seen


# --------------------------------------------------------------------------
# project-wide statistics
# --------------------------------------------------------------------------


def build_header_stats(scan: ProjectScan, sizes: HeaderSizes) -> None:
    """Fill ``scan.header_stats`` in place."""
    root = scan.root
    kinds = classify_headers(
        root,
        {_normalise_header_key(h, root) for tu in scan.ok_tus for h in tu.dependencies},
    )

    reach_count: dict[Path, int] = defaultdict(int)
    direct_count: dict[Path, int] = defaultdict(int)
    event_count: dict[Path, int] = defaultdict(int)
    max_depth: dict[Path, int] = defaultdict(int)
    closure_sizes: dict[Path, set[Path]] = {}
    closure_lines: dict[Path, int] = defaultdict(int)
    # 增量构建税：一个头文件改动后，所有「传递包含它」的 TU 都要重编。
    # 这里按 TU 的实测总耗时累加，得到「改这一行要等多少秒」。
    tax_seconds: dict[Path, float] = defaultdict(float)

    for tu in scan.ok_tus:
        # 邻接表与统计都用归一后的 key，确保同一文件的多形态被并成一条。
        adjacency: dict[Path, dict[Path, None]] = defaultdict(dict)
        for event in tu.include_events:
            adjacency[_normalise_header_key(event.includer, root)][
                _normalise_header_key(event.included, root)
            ] = None
        adjacency_map = {p: list(c) for p, c in adjacency.items()}

        seen_in_tu: set[Path] = set()
        for event in tu.include_events:
            canon = _normalise_header_key(event.included, root)
            event_count[canon] += 1
            if event.depth > max_depth[canon]:
                max_depth[canon] = event.depth
        for direct in tu.direct_includes():
            direct_count[_normalise_header_key(direct, root)] += 1
        # 增量税问的是「用户要等多久」，所以优先用驱动实测的墙钟；只有旧数据
        # /手工构造的 TU 没有墙钟时，才退回 -ftime-report 的 TOTAL（CPU 时间）。
        # 直接用 CPU 时间会在 Windows 上把税额低估一个数量级——那是把
        # 「改了要等 3 秒」报成「0.2 秒」，比不报更糟。
        tu_seconds = tu_compile_seconds(tu)
        # 排序后再迭代：header_stats 的插入顺序会决定 JSON 里 headers 的
        # 顺序，而集合迭代受哈希随机化影响，不排序会导致同输入不同输出。
        for header in sorted(
            {_normalise_header_key(e.included, root) for e in tu.include_events},
            key=str,
        ):
            if header in seen_in_tu:
                continue
            seen_in_tu.add(header)
            reach_count[header] += 1
            # 这个 TU 传递包含 header，所以 header 一改它就要重编 —— 税加一次。
            tax_seconds[header] += tu_seconds
            closure = closure_of(adjacency_map, header)
            closure_sizes[header] = closure
            closure_lines[header] += sizes.total(closure)

    for header, reach in reach_count.items():
        closure = closure_sizes.get(header, set())
        stats = HeaderStats(
            path=header,
            kind=kinds.get(header, HeaderKind.SYSTEM),
            line_count=sizes.lines(header),
            tu_reach=reach,
            tu_direct=direct_count.get(header, 0),
            inclusion_events=event_count.get(header, 0),
            max_depth=max_depth.get(header, 0),
            transitive_headers=len(closure),
            transitive_lines=sizes.total(closure),
            attributed_lines=sizes.lines(header) * reach,
            incremental_seconds=tax_seconds.get(header, 0.0),
        )
        scan.header_stats[header] = stats


# --------------------------------------------------------------------------
# removal candidates
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _TuAnalysis:
    """Per-TU derived facts, computed once and reused."""

    tu: TranslationUnit
    adjacency: dict[Path, list[Path]]
    closures: dict[Path, set[Path]]
    processed_lines: int


def _analyse_tus(scan: ProjectScan, sizes: HeaderSizes) -> list[_TuAnalysis]:
    """每个单元两张图：有效树管成本，潜在供给图管「删了会少什么」。

    这两个问题需要不同的图，混用会在两边都错：

    * ``-H`` 有效树里每个头只出现一次（第二次被 guard 吞掉），所以它正确
      回答「这次构建解析了哪些头、各多大代价」；
    * 但删掉一条 include 之后**还剩哪些路径可达**，必须把被吞掉的第二次包含
      算回来，否则会把「其实还有别的路」的头算成消失。

    实测代价：doctest 24 单元 60 条候选里 18 条因此报出「会带走 27 万行」，
    删掉后一个文件都没少解析。
    """
    analyses: list[_TuAnalysis] = []
    mentions = IncludeMentions()
    for tu in scan.ok_tus:
        processed = {e.included for e in tu.include_events}
        parsed = set(processed) | {tu.source}
        potential = _potential_adjacency(tu, mentions, parsed)
        analyses.append(
            _TuAnalysis(
                tu=tu,
                adjacency=potential,
                closures={h: closure_of(potential, h) for h in parsed},
                processed_lines=sizes.total(processed),
            )
        )
    return analyses


def find_removal_candidates(
    scan: ProjectScan,
    sizes: HeaderSizes,
    kinds: dict[Path, HeaderKind] | None = None,
    *,
    min_savings: int = 1,
    usage_index=None,
) -> list[RemovalCandidate]:
    """Rank direct ``#include`` lines by how much they cost, and how likely
    they are to be removable.

    Two corrections over the first implementation, both forced by the
    controlled experiment in ``experiments/control.py``:

    1. **Every direct include is a candidate, not just project-owned ones.**
       The first version skipped headers outside the project root, reasoning
       "we cannot edit ``<vector>``".  That confused the owner of the *header*
       with the owner of the *edit*: the line being deleted is always in our
       own source file, and deleting ``#include <iostream>`` from it is a
       perfectly ordinary change.  Worse, it dropped the exact region where
       every effective edit in the experiment was found -- 3 hits out of 3
       landed on standard library headers, all of them invisible to the tool.

    2. **The estimate is per translation unit and in seconds, not project-wide
       lines.**  Project-wide attributed lines reward headers that *every* unit
       shares, which are the ones nobody can remove; and a removal only ever
       saves time in the one unit it is applied to.  See
       :func:`_estimate_seconds` for the arithmetic and its known bias.

    Candidates come back ordered by :func:`heavyheader.models.candidate_rank_key`
    -- verified wins first, then plausibly-unused by expected seconds, then the
    ones the usage heuristic says are still in use.
    """
    from .usage import IdentifierIndex

    if kinds is None:
        kinds = classify_headers(
            scan.root, {h for tu in scan.ok_tus for h in tu.dependencies}
        )
    index = usage_index if usage_index is not None else IdentifierIndex()

    candidates: list[RemovalCandidate] = []
    # 同一份源码可能被编多次（静态库 + 动态库，同一份 compile_commands 里两条
    # 命令）。代价要按编译次数算，但**候选是同一条待改的代码行**——重复列出
    # 只会让读者以为有两个优化点。实测 zlib：87 条候选里有 4 条是这种重复。
    seen_sites: set[tuple[str, Path, int | None]] = set()
    for analysis in _analyse_tus(scan, sizes):
        tu = analysis.tu
        directs = tu.direct_includes()
        if not directs:
            continue

        # 这个单元当前能到达的全部头文件。用来回答一个用法判定必须回答、
        # 但旧实现没回答的问题：被判为「在用」的那个符号，删掉这条 include
        # 之后**还会不会存在**。只在到达集里出现一次的符号，删了就没了；
        # 出现在别处的符号，删了照样在——不构成保留理由。
        #
        # 只统计 TU 源码提到过的符号：其余符号永远不会成为命中，算了也是浪费。
        reach_all: set[Path] = set(directs)
        for other in directs:
            reach_all |= analysis.closures.get(other, set())
        source_ids = index.source_ids(tu.source) or frozenset()
        reachable_counts: dict[str, int] = {}
        if source_ids:
            for path in reach_all:
                for name in index.header_provider_ids(path) & source_ids:
                    reachable_counts[name] = reachable_counts.get(name, 0) + 1

        for header in directs:
            # Union of everything reachable from *other* direct includes.  A
            # header in this union is already being paid for, so removing this
            # include cannot save it.
            #
            # ``header`` itself must be excluded from this union.  Including it
            # would put the candidate's own closure into ``shared`` and make
            # every exclusive set empty -- which silently disables the entire
            # candidate detector.  A unit test guards against exactly this.
            shared: set[Path] = set()
            for other in directs:
                if other == header:
                    continue
                shared |= analysis.closures.get(other, set())
            # The source file itself is always parsed; its own text is never
            # attributable to an include.
            shared.discard(tu.source)

            closure = analysis.closures.get(header, set())
            exclusive = closure - shared
            # Dropping this include stops the compiler parsing ``header``
            # itself as well as its exclusive transitive closure.
            #
            # ``closure`` deliberately answers "what does this reach" and
            # excludes its start node, so without adding ``header`` back a
            # header that includes nothing -- a leaf -- reports zero savings
            # and is dropped entirely.  That blind spot hid the most common
            # optimisation there is: deleting an unused standalone header,
            # which ``verify`` would happily confirm improves build time
            # while ``scan`` insisted it was worth nothing.
            #
            # It only counts when no other direct include already reaches
            # ``header``; in that case it is in ``shared`` and keeps being
            # paid for, so removing this include saves nothing.
            if header not in shared:
                exclusive.add(header)
            saved = sizes.total(exclusive)
            if saved < min_savings:
                continue
            percent = 0.0
            if analysis.processed_lines:
                percent = saved / analysis.processed_lines * 100.0
            line = _include_line(tu, header)
            site = (str(tu.source).lower(), header, line)
            if site in seen_sites:
                continue
            seen_sites.add(site)
            # 行号有歧义时不猜（同名头出现多处），但也不能只给「行号未知」——
            # 把全部候选位置列出来，读者才知道要在哪些行之间做选择。
            ambiguous = (
                () if line is not None else _ambiguous_include_lines(tu, header)
            )
            # 用法判定必须覆盖「删掉这个 include 后不再被解析的那批头」，
            # 而不是候选头自己的那段文本——实验里 5 个失败候选全部栽在这里：
            # doctest_compatibility.h 自身没有 TEST_CASE，TEST_CASE 在它
            # 往下拉的 doctest.h 里。
            signal = analyse_usage(
                tu.source,
                header,
                closure=exclusive,
                index=index,
                reachable_counts=reachable_counts,
            )
            candidates.append(
                RemovalCandidate(
                    tu=tu.source,
                    header=header,
                    line=line,
                    kind=kinds.get(header, HeaderKind.SYSTEM),
                    exclusive_headers=sorted(exclusive),
                    estimated_lines_saved=saved,
                    estimated_percent=round(percent, 2),
                    estimated_seconds_saved=_estimate_seconds(
                        tu, analysis.processed_lines, saved
                    ),
                    measured_seconds_saved=None,
                    verified=False,
                    usage_label=signal.label,
                    usage_verdict=signal.verdict.value,
                    confidence=_confidence(exclusive, analysis, signal.verdict),
                    notes=_notes(tu, header, exclusive, sizes, signal)
                    + (
                        [
                            "同名 include 出现在第 "
                            + "、".join(str(n) for n in ambiguous)
                            + " 行（条件编译分支），请人工确认改哪一处"
                        ]
                        if ambiguous
                        else []
                    ),
                )
            )

    candidates.sort(key=candidate_rank_key)
    return candidates


def _estimate_seconds(tu: TranslationUnit, processed_lines: int, saved: int) -> float | None:
    """Expected compile-time saving for this unit, in seconds.

    ``tu.total_wall_seconds()`` is the compiler's own TOTAL row from
    ``-ftime-report`` for *this* unit, so the share below is anchored in a
    measurement instead of a guess::

        seconds_saved  ~=  tu_total  *  exclusive_lines / tu_processed_lines

    The known, documented bias: lines are not equally expensive.  Standard
    library headers are template-dense and cost more per line than project
    headers -- in the experiment, dropping ``<iostream>`` cut a unit's compile
    time by 15% while its line share was far smaller.  So this number is a
    *prior* for ordering, not a promise; ``verify`` replaces it with a
    measurement, and the report never shows the two as if they were the same
    quantity.  Returns ``None`` when the compiler reported no total, so an
    unmeasured unit is not silently ranked at zero.
    """
    total = tu.total_wall_seconds()
    if total <= 0 or processed_lines <= 0:
        return None
    return round(total * (saved / processed_lines), 4)



def _include_line(
    tu: TranslationUnit, header: Path, source_text: str | None = None
) -> int | None:
    """这条 include 在源文件的第几行。

    编译器**不报行号**：GCC/Clang 的 ``-H`` 只有缩进与路径，MSVC 的
    ``/showIncludes`` 同样只有层级。所以先看事件里有没有（某些工具链会填），
    没有就从源码文本定位——否则报告里每条候选都是「行号未知」，读者拿不到
    可以动手的那一行，下游的 verify / blame 也都得重算一遍。

    文本定位沿用 :mod:`heavyheader.includes` 的保守规则：同名头有歧义时返回
    ``None``，不猜——猜错会让人删掉另一个文件，而结论看起来完全可信。
    """
    for event in tu.include_events:
        if event.depth == 0 and event.included == header and event.line:
            return event.line
    text = source_text
    if text is None:
        try:
            text = tu.source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    return find_include_line(text, header)


def _ambiguous_include_lines(tu: TranslationUnit, header: Path) -> tuple[int, ...]:
    """同名 include 的**全部**出现位置（仅在唯一行号定不出来时调用）。

    典型形态是 zlib minigzip.c 的 ``<stdlib.h>``：两个条件编译分支里各有一处，
    删哪一处取决于哪个分支被激活——静态文本无法判定，但这不等于读者只该看到
    「行号未知」。
    """
    try:
        text = tu.source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    return tuple(find_include_lines(text, header))


def _confidence(exclusive: set[Path], analysis: _TuAnalysis, verdict) -> str:
    """How confident are we that removal is a *win*?

    Two independent axes, and the weaker one wins:

    * **Cost** -- does the include own a large exclusive closure?  If not,
      removing it removes no work even if it is unused.
    * **Usage** -- does the source still name something the closure declares?
      If yes, the edit will not compile, and no amount of saved text helps.

    The first version scored cost only, which is how a 20,000-line header
    that every unit needs ended up ranked first and failed to compile.  A
    cost-based "high" on an in-use include is not high confidence, it is a
    confident mistake.
    """
    from .usage import UsageVerdict

    if not exclusive:
        return "none"
    if verdict is UsageVerdict.IN_USE:
        return "blocked"
    if verdict is UsageVerdict.UNKNOWN:
        return "guarded"
    if len(exclusive) <= 2:
        return "low"
    if len(exclusive) <= 10:
        return "medium"
    return "high"


def _notes(
    tu: TranslationUnit,
    header: Path,
    exclusive: set[Path],
    sizes: HeaderSizes,
    signal=None,
) -> list[str]:
    notes: list[str] = []
    if (
        signal is not None
        and signal.cross_provided
        and signal.verdict.value == "plausibly_unused"
    ):
        # 说清「为什么判它没用」：命中的名字都还有别的供给者。不写这句，
        # 读者只能看到一个结论，没法复核它对不对。
        names = "、".join(signal.cross_provided[:3])
        notes.append(
            f"源码提到的 {names} 在保留下来的头文件里另有供给，"
            "删掉这条不会缺声明（文本层判断，最终以实测为准）"
        )
    if signal is not None and signal.provided_by is not None and signal.provided_by != header:
        # 说清符号到底由谁提供。只写「这个 include 在被用」，用户还是不知道
        # 依赖藏在哪一层；写出文件名才可操作（比如「该直接 include doctest.h」）。
        names = "、".join(signal.shared_identifiers[:3])
        notes.append(
            f"{names} 由 {signal.provided_by.name} 提供（在本 include 的下游），"
            "删除会编译失败"
        )
    if not exclusive:
        notes.append("该 include 的头文件均可由其他 include 到达，删除几乎无收益")
    heavy = sorted(exclusive, key=lambda h: sizes.lines(h), reverse=True)[:3]
    for path in heavy:
        if sizes.lines(path) >= 500:
            notes.append(f"独占闭包含大文件 {path.name}（{sizes.lines(path)} 行）")
    return notes


# --------------------------------------------------------------------------
# redundant re-parsing (missing include guards)
# --------------------------------------------------------------------------


def redundant_reparse_warnings(
    scan: ProjectScan,
) -> list[tuple[Path, int, int]]:
    """Headers the compiler parsed more than once, per TU count.

    Returns ``(header, times_processed, tu_count)``.  ``times_processed >
    tu_count`` means the header is being re-parsed inside a single
    translation unit, which for a guarded header is impossible -- so this is
    a direct, certain signal of a missing include guard.

    Unlike the "unused include" question, this one has no ambiguity: the fix
    (add ``#pragma once`` or a guard macro) is always safe and always a win.
    """
    processed: dict[Path, int] = defaultdict(int)
    tus: dict[Path, int] = defaultdict(int)

    for tu in scan.ok_tus:
        per_tu: dict[Path, int] = defaultdict(int)
        for event in tu.include_events:
            processed[event.included] += 1
            per_tu[event.included] += 1
        for header in per_tu:
            tus[header] += 1

    warnings = [
        (header, count, tus[header])
        for header, count in processed.items()
        if count > tus[header]
    ]
    warnings.sort(key=lambda item: (item[1] - item[2]), reverse=True)
    return warnings
