"""Inclusion *sites*: the ``#include`` lines inside headers, not just in sources.

Why this module exists
----------------------
The first candidate generator (``graph.find_removal_candidates``) only looked
at a translation unit's own ``#include`` lines.  That is a small, mostly
non-removable set, and the G2 experiment measured exactly how small:
in nlohmann/json it produced 83 candidates of which **3** were plausibly
unused, and all 3 saved less than the 2% noise floor.  Not a ranking bug --
there was nothing to find, because a TU typically includes three files and
needs all three.

Headers are different.  A header is parsed by every TU that reaches it, so
one redundant line inside it is paid for over and over: the payoff of
removing it is ``exclusive lines x number of TUs that parse the file``.
That product is the whole reason this layer exists, and it is why
:attr:`RemovalCandidate.amplified_lines_saved` is a different number from
:attr:`RemovalCandidate.estimated_lines_saved` -- mixing them up
understates a header-level fix by the size of its audience.

What "feasible" means here
--------------------------
Enumerating every (file, include) pair produces hundreds of rows, nearly all
of them unremovable.  A list like that is not a recommendation, it is a
puzzle.  So this generator applies a **feasibility gate** before anything
enters the candidate list:

    the file that owns the ``#include`` must not use any identifier from the
    set that would disappear with it.

That is the same textual heuristic as :mod:`heavyheader.attrib.usage`,
applied at the right unit of analysis -- the *site file's own body*, not the
TU's (the closure that vanishes is what the file itself stops seeing).  It is
still a heuristic; :mod:`heavyheader.verify` settles it by recompiling every
affected unit.

Toolchain headers are skipped by default: "delete this line from
``windows.h``" is not actionable advice even when it is true.

Stated limitation
-----------------
A header-level removal can break a TU that never names the header's symbols
but relied on them arriving transitively.  The gate above cannot see that,
which is precisely why verification must compile **all** affected units, not
just one representative.  That is what :func:`verify` does for
:attr:`RemovalCandidate.is_header_site` candidates.
"""

from __future__ import annotations

from pathlib import Path

from ..models import (  # noqa: E402
    HeaderKind,
    ProjectScan,
    RemovalCandidate,
    tu_compile_seconds,
)
from .graph import HeaderSizes, classify_headers  # noqa: E402
from .usage import IdentifierIndex, UsageVerdict, analyse_usage  # noqa: E402

__all__ = [
    "build_include_graph",
    "tu_parsed_files",
    "find_header_candidates",
]


def build_include_graph(scan: ProjectScan) -> dict[Path, list[Path]]:
    """``file -> the files it includes directly``, merged across all units.

    The compiler reports one include tree per TU; the same header seen from
    two units yields the same edges, so they are merged.  Order is kept
    stable (first-seen) because it is printed in reports and used as a
    tie-breaker.
    """
    children: dict[Path, list[Path]] = {}
    for tu in scan.ok_tus:
        for event in tu.include_events:
            bucket = children.setdefault(event.includer, [])
            if event.included not in bucket:
                bucket.append(event.included)
    return children


def _closures(children: dict[Path, list[Path]]) -> dict[Path, frozenset[Path]]:
    """Transitive closure of every node, memoised.

    Include graphs are acyclic in practice (guards), but a cyclic or
    self-referential report from a compiler must not hang the scan, so a node
    is seeded with the empty set before its children are walked.
    """
    memo: dict[Path, frozenset[Path]] = {}

    def walk(node: Path) -> frozenset[Path]:
        cached = memo.get(node)
        if cached is not None:
            return cached
        memo[node] = frozenset()
        acc: set[Path] = set()
        for child in children.get(node, ()):
            acc.add(child)
            acc |= walk(child)
        result = frozenset(acc)
        memo[node] = result
        return result

    for node in list(children):
        walk(node)
    return memo


def tu_parsed_files(
    scan: ProjectScan, closures: dict[Path, frozenset[Path]]
) -> dict[Path, frozenset[Path]]:
    """``TU -> every file it parses``, itself included."""
    return {
        tu.source: closures.get(tu.source, frozenset()) | {tu.source}
        for tu in scan.ok_tus
    }


def find_header_candidates(
    scan: ProjectScan,
    sizes: HeaderSizes,
    kinds: dict[Path, HeaderKind] | None = None,
    *,
    min_reach: int = 2,
    min_amplified_lines: int = 1,
    include_system_sites: bool = False,
    require_plausibly_unused: bool = True,
    usage_index=None,
) -> list[RemovalCandidate]:
    """...

    ``require_plausibly_unused`` 是**可行性闸门**本身的开关，留着它是为了
    做消融实验：闸门开 = 只推荐「拥有这一行的文件自己不用它」的改动；
    闸门关 = 按收益排前 K 条，不管删了编不编得过。G2 要报的是这两者的
    编译通过率之差——它才说明闸门是不是真的有用，而不是听起来有用。
    """
    """Removal candidates whose ``#include`` line lives in a header.

    Returns candidates with :attr:`RemovalCandidate.site_file` set to the file
    being edited and :attr:`RemovalCandidate.affected_tus` holding every unit
    that parses it.  ``tu`` is the *representative* unit (the slowest one) --
    it anchors reports and the baseline cache, and it is never the file being
    patched.
    """
    if kinds is None:
        kinds = classify_headers(
            scan.root, {h for tu in scan.ok_tus for h in tu.dependencies}
        )
    index = usage_index if usage_index is not None else IdentifierIndex()

    children = build_include_graph(scan)
    if not children:
        return []
    closures = _closures(children)
    parsed = tu_parsed_files(scan, closures)
    tu_sources = set(parsed)

    # 每个 TU 的「解析了多少行」与「编译了多久」，用于把行数换算成秒。
    processed_lines = {
        source: sizes.total(files) for source, files in parsed.items()
    }
    unit_seconds = {tu.source: tu_compile_seconds(tu) for tu in scan.ok_tus}

    # include 行号：编译器在 -H 里给了就用它，省一次读文件。
    lines_by_edge: dict[tuple[Path, Path], int | None] = {}
    for tu in scan.ok_tus:
        for event in tu.include_events:
            key = (event.includer, event.included)
            if key not in lines_by_edge or (lines_by_edge[key] is None and event.line):
                lines_by_edge[key] = event.line

    candidates: list[RemovalCandidate] = []
    for site_file, directs in children.items():
        if site_file in tu_sources:
            continue  # TU 级候选由 graph.find_removal_candidates 负责
        if not include_system_sites and kinds.get(site_file) == HeaderKind.SYSTEM:
            continue

        affected = sorted(
            source for source, files in parsed.items() if site_file in files
        )
        if len(affected) < min_reach:
            continue

        for header in directs:
            # 其它 direct include 已经拉进来的东西，删掉这一行也还在，
            # 所以不算在这条的收益里。
            shared: set[Path] = set()
            for other in directs:
                if other == header:
                    continue
                shared |= closures.get(other, frozenset())
            shared.discard(site_file)

            exclusive = set(closures.get(header, frozenset()))
            if header not in shared:
                exclusive.add(header)
            exclusive -= shared
            if not exclusive:
                continue
            saved = sizes.total(exclusive)
            if saved <= 0:
                continue

            # 可行性闸门：拥有这一行的文件自己不用它，才值得推荐。
            # 没有这道闸门，几百条「删了就编译不过」的建议会把真正可删的
            # 那几条埋掉——G2 的 0/10 里有一半就是这个原因。
            signal = analyse_usage(site_file, header, closure=exclusive, index=index)
            if (
                require_plausibly_unused
                and signal.verdict is not UsageVerdict.PLAUSIBLY_UNUSED
            ):
                continue

            amplified = saved * len(affected)
            if amplified < min_amplified_lines:
                continue

            representative = max(
                affected,
                key=lambda tu: (unit_seconds.get(tu) or 0.0, str(tu)),
            )
            total_lines = sum(processed_lines.get(tu, 0) for tu in affected)
            percent = (amplified / total_lines * 100.0) if total_lines else 0.0

            seconds: float | None = None
            total_seconds = 0.0
            counted = 0
            for tu in affected:
                unit_total = unit_seconds.get(tu) or 0.0
                lines = processed_lines.get(tu, 0)
                if unit_total <= 0 or lines <= 0:
                    continue
                total_seconds += unit_total * (saved / lines)
                counted += 1
            if counted:
                seconds = round(total_seconds, 4)

            candidates.append(
                RemovalCandidate(
                    tu=representative,
                    header=header,
                    line=lines_by_edge.get((site_file, header)),
                    kind=kinds.get(header, HeaderKind.SYSTEM),
                    exclusive_headers=sorted(exclusive),
                    estimated_lines_saved=saved,
                    estimated_percent=round(percent, 2),
                    estimated_seconds_saved=seconds,
                    measured_seconds_saved=None,
                    verified=False,
                    usage_label=signal.label,
                    usage_verdict=signal.verdict.value,
                    confidence=_confidence(saved, len(affected), signal),
                    notes=_notes(site_file, header, saved, len(affected)),
                    site_file=site_file,
                    affected_tus=list(affected),
                )
            )

    candidates.sort(key=lambda c: (-c.amplified_lines_saved, str(c.site), str(c.header)))
    return candidates


def _confidence(saved: int, reach: int, signal) -> str:
    """与 graph._confidence 同形的三档，但换成了头文件级的判据。

    门槛按「放大后行数」定，而不是单 TU 行数：一个 288 行的 include 在
    单个 TU 里微不足道，被 24 个 TU 各解析一遍就是近 7000 行。
    """
    amplified = saved * reach
    if signal.verdict is not UsageVerdict.PLAUSIBLY_UNUSED:
        return "low"
    if amplified >= 5000:
        return "high"
    if amplified >= 1000:
        return "medium"
    return "low"


def _notes(site_file: Path, header: Path, saved: int, reach: int) -> list[str]:
    return [
        f"这一行在头文件 {site_file.name} 里，被 {reach} 个编译单元解析",
        f"单单元约 {saved} 行，全项目累计约 {saved * reach} 行",
        "可行性判定基于「该文件自身正文不使用即将消失的标识符」；"
        "是否真的可删由 verify 重新编译全部受影响单元后给出",
    ]
