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

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .usage import analyse_usage
from ..models import (
    HeaderKind,
    HeaderStats,
    ProjectScan,
    RemovalCandidate,
    TranslationUnit,
)

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
    kinds = classify_headers(scan.root, {h for tu in scan.ok_tus for h in tu.dependencies})

    reach_count: dict[Path, int] = defaultdict(int)
    direct_count: dict[Path, int] = defaultdict(int)
    event_count: dict[Path, int] = defaultdict(int)
    max_depth: dict[Path, int] = defaultdict(int)
    closure_sizes: dict[Path, set[Path]] = {}
    closure_lines: dict[Path, int] = defaultdict(int)

    for tu in scan.ok_tus:
        adjacency = _build_adjacency(tu)
        seen_in_tu: set[Path] = set()
        for event in tu.include_events:
            event_count[event.included] += 1
            if event.depth > max_depth[event.included]:
                max_depth[event.included] = event.depth
        for direct in tu.direct_includes():
            direct_count[direct] += 1
        for header in {e.included for e in tu.include_events}:
            if header in seen_in_tu:
                continue
            seen_in_tu.add(header)
            reach_count[header] += 1
            closure = closure_of(adjacency, header)
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
    analyses: list[_TuAnalysis] = []
    for tu in scan.ok_tus:
        adjacency = _build_adjacency(tu)
        processed = {e.included for e in tu.include_events}
        analyses.append(
            _TuAnalysis(
                tu=tu,
                adjacency=adjacency,
                closures={h: closure_of(adjacency, h) for h in processed},
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
) -> list[RemovalCandidate]:
    """Rank direct ``#include`` lines by how much they cost the project.

    Only *direct* includes of project-owned headers are proposed.  Removing a
    transitive include is never our call -- the owner of the including header
    has to make that change, and suggesting it would be noise.
    """
    if kinds is None:
        kinds = classify_headers(
            scan.root, {h for tu in scan.ok_tus for h in tu.dependencies}
        )

    candidates: list[RemovalCandidate] = []
    for analysis in _analyse_tus(scan, sizes):
        tu = analysis.tu
        directs = tu.direct_includes()
        if not directs:
            continue

        for header in directs:
            # The contract, stated twice elsewhere (HeaderKind and
            # classify_headers): only project-owned headers are ever proposed
            # for removal.  We cannot edit <vector>, and a ranked list topped
            # by "delete <vector>" buries everything actionable.
            if kinds.get(header, HeaderKind.SYSTEM) is not HeaderKind.PROJECT:
                continue

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
            percent = (saved / analysis.processed_lines * 100.0) if analysis.processed_lines else 0.0
            line = _include_line(tu, header)
            candidates.append(
                RemovalCandidate(
                    tu=tu.source,
                    header=header,
                    line=line,
                    kind=kinds.get(header, HeaderKind.SYSTEM),
                    exclusive_headers=sorted(exclusive),
                    estimated_lines_saved=saved,
                    estimated_percent=round(percent, 2),
                    measured_seconds_saved=None,
                    verified=False,
                    usage_label=analyse_usage(tu.source, header).label,
                    confidence=_confidence(exclusive, analysis),
                    notes=_notes(tu, header, exclusive, sizes),
                )
            )

    # Biggest absolute saving first; ties broken by the wider-reaching header.
    candidates.sort(
        key=lambda c: (c.estimated_lines_saved, c.estimated_percent), reverse=True
    )
    return candidates


def _include_line(tu: TranslationUnit, header: Path) -> int | None:
    for event in tu.include_events:
        if event.depth == 0 and event.included == header:
            return event.line
    return None


def _confidence(exclusive: set[Path], analysis: _TuAnalysis) -> str:
    """How confident are we that removal is a *win*?

    This is deliberately not a claim that the include is unused -- that needs
    semantic analysis we do not do.  It is a claim about cost: if the include
    owns a large exclusive closure, removing it removes real work.
    """
    if not exclusive:
        return "none"
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
) -> list[str]:
    notes: list[str] = []
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
