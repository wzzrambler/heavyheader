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
from .diagnosis import BlockedSite  # noqa: E402
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
    costs=None,
    blocked: list | None = None,
) -> list[RemovalCandidate]:
    """Removal candidates whose ``#include`` line lives in a header.

    Returns candidates with :attr:`RemovalCandidate.site_file` set to the file
    being edited and :attr:`RemovalCandidate.affected_tus` holding every unit
    that parses it.  ``tu`` is the *representative* unit (the slowest one) --
    it anchors reports and the baseline cache, and it is never the file being
    patched.

    ``require_plausibly_unused`` 是**可行性闸门**本身的开关，留着它是为了
    做消融实验：闸门开 = 只推荐「拥有这一行的文件自己不用它」的改动；
    闸门关 = 按收益排前 K 条，不管删了编不编得过。G2 要报的是这两者的
    编译通过率之差——它才说明闸门是不是真的有用，而不是听起来有用。

    ``blocked`` 是一个可选的**收集器**：传一个 list，所有「看起来贵、但没进
    候选表」的 include 行会连同被拦下的原因追加进去，交给
    :func:`heavyheader.attrib.diagnosis.build_diagnosis` 汇总。它的存在是
    因为 G2 在三个工程里有两个是 0 条候选——空表会被读成「构建没问题」，而
    真实情况是每条都被判了「删了编不过」。**不传它时不做任何额外计算**，
    所以默认扫描的开销与加这个功能之前完全一样。
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

    def exclusive_of(header: Path, directs: list[Path]) -> set[Path]:
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
        return exclusive

    def reject(
        reason: str,
        *,
        header: Path,
        saved: int = 0,
        detail: str = "",
    ) -> None:
        """记录一条被拦下的 include 行；``blocked`` 为 None 时什么都不做。"""
        if blocked is None:
            return
        blocked.append(
            BlockedSite(
                site_file=site_file,
                header=header,
                line=lines_by_edge.get((site_file, header)),
                amplified_lines=saved * len(affected),
                reach=len(affected),
                reason=reason,
                detail=detail,
            )
        )

    candidates: list[RemovalCandidate] = []
    for site_file, directs in children.items():
        if site_file in tu_sources:
            continue  # TU 级候选由 graph.find_removal_candidates 负责

        affected = sorted(
            source for source, files in parsed.items() if site_file in files
        )
        if len(affected) < min_reach:
            # 只被 1 个单元解析：改公共头不划算，TU 级候选已经覆盖它。
            if blocked is not None:
                for header in directs:
                    reject(
                        "low_reach",
                        header=header,
                        saved=sizes.total(exclusive_of(header, directs)),
                    )
            continue
        if not include_system_sites and kinds.get(site_file) == HeaderKind.SYSTEM:
            if blocked is not None:
                for header in directs:
                    reject(
                        "system_site",
                        header=header,
                        saved=sizes.total(exclusive_of(header, directs)),
                        # detail 留空：原因标签已经说了同一件事，再重复一遍
                        # 会挤掉真正需要核对的信息（具体标识符）。
                        detail="",
                    )
            continue

        for header in directs:
            exclusive = exclusive_of(header, directs)
            if not exclusive:
                reject(
                    "shadowed",
                    header=header,
                    detail="同一批头文件已经从别的 include 到达，删掉这一行不省钱",
                )
                continue
            saved = sizes.total(exclusive)
            if saved <= 0:
                reject(
                    "unmeasured",
                    header=header,
                    detail="取不到这些头文件的行数（文件不可读或为空），无法估算收益",
                )
                continue

            # 可行性闸门：拥有这一行的文件自己不用它，才值得推荐。
            # 没有这道闸门，几百条「删了就编译不过」的建议会把真正可删的
            # 那几条埋掉——G2 的 0/10 里有一半就是这个原因。
            signal = analyse_usage(site_file, header, closure=exclusive, index=index)
            if (
                require_plausibly_unused
                and signal.verdict is not UsageVerdict.PLAUSIBLY_UNUSED
            ):
                reject(
                    "owner_uses"
                    if signal.verdict is UsageVerdict.IN_USE
                    else "owner_unknown",
                    header=header,
                    saved=saved,
                    detail=_block_detail(signal),
                )
                continue

            amplified = saved * len(affected)
            if amplified < min_amplified_lines:
                reject("tiny", header=header, saved=saved)
                continue

            # 成本口径优先：有 -ftime-trace 实测就用它，没有才回退行数换算。
            # 两者混用会静默改变排序，所以在 notes 里写明依据是哪一种。
            measured_us = costs.total_us(exclusive) if costs is not None else None
            covered: tuple[int, int] | None = None
            if measured_us is not None and measured_us > 0:
                seconds = round(measured_us * len(affected) / 1_000_000.0, 4)
                basis = "measured"
                covered = costs.coverage(exclusive)
            else:
                basis = "lines"

            representative = max(
                affected,
                key=lambda tu: (unit_seconds.get(tu) or 0.0, str(tu)),
            )
            total_lines = sum(processed_lines.get(tu, 0) for tu in affected)
            percent = (amplified / total_lines * 100.0) if total_lines else 0.0

            if basis == "lines":
                seconds = None
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
                    notes=_notes(site_file, header, saved, len(affected), basis, covered),
                    site_file=site_file,
                    affected_tus=list(affected),
                )
            )

    # 有实测成本时按秒排，没有才按行数排。两个口径都带路径做 tie-breaker，
    # 否则 Python 的哈希随机化会让「同样的数据」在两次运行间换顺序。
    if any(c.estimated_seconds_saved for c in candidates) and costs is not None:
        candidates.sort(
            key=lambda c: (
                -(c.estimated_seconds_saved or 0.0),
                str(c.site),
                str(c.header),
            )
        )
    else:
        candidates.sort(
            key=lambda c: (-c.amplified_lines_saved, str(c.site), str(c.header))
        )
    return candidates


def _block_detail(signal) -> str:
    """把「为什么拦下」写成一句话，尽量带具体标识符。

    没有可引用的证据时明说没有，而不是补一句听起来合理的解释——诊断的
    价值就在于它给的每条理由都能被读者自己核对。
    """
    if signal.verdict is UsageVerdict.IN_USE:
        names = "、".join(signal.shared_identifiers[:5])
        if not names:
            return "判定为在用，但没有可引用的具体标识符"
        provider = f"（由 {signal.provided_by.name} 提供）" if signal.provided_by else ""
        return f"该文件使用了 {names}{provider}"
    if signal.ambient_identifiers:
        names = "、".join(sorted(signal.ambient_identifiers)[:5])
        return f"只命中环境噪音（{names}）：不足以判定在用，也不足以判定没在用"
    if signal.header_identifier_count == 0:
        return "读不到即将消失的头文件正文，判定无法进行"
    return "文本判定给不出结论，工具选择不猜"


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


def _notes(
    site_file: Path,
    header: Path,
    saved: int,
    reach: int,
    basis: str = "lines",
    covered: tuple[int, int] | None = None,
) -> list[str]:
    notes = [
        f"这一行在头文件 {site_file.name} 里，被 {reach} 个编译单元解析",
        f"单单元约 {saved} 行，全项目累计约 {saved * reach} 行",
    ]
    if basis == "measured":
        # 覆盖不全时要说清楚，否则「实测」两个字会让人以为全测了。
        if covered and covered[0] < covered[1]:
            notes.append(
                f"排序依据：{covered[1]} 个独占头里有 {covered[0]} 个拿到 "
                "-ftime-trace 实测耗时，其余按未计入处理（可能低估）"
            )
        else:
            notes.append(
                "排序依据：-ftime-trace 实测的每个头文件自身耗时（已扣除嵌套子头）"
            )
    else:
        notes.append(
            "排序依据：归因行数估算（未采集 -ftime-trace，或该头没有实测数据）"
        )
    notes.append(
        "可行性判定基于「该文件自身正文不使用即将消失的标识符」；"
        "是否真的可删由 verify 重新编译全部受影响单元后给出"
    )
    return notes
