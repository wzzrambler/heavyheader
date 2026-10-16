"""Core data model shared by every HeavyHeader stage.

The model is deliberately boring: plain dataclasses, no framework, no
side effects.  Everything the tool knows about a compilation run flows
through these types, which makes the pipeline easy to test in isolation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class CompilerFamily(enum.Enum):
    """Which dialect of compiler output we are talking to."""

    GCC = "gcc"
    CLANG = "clang"
    MSVC = "msvc"


class HeaderKind(enum.Enum):
    """Where a header came from.

    ``PROJECT`` headers are the ones a team can actually edit, and they are
    the only ones HeavyHeader ever proposes to remove.  Everything else is
    reported for context so the numbers add up.
    """

    PROJECT = "project"
    SYSTEM = "system"
    EXTERNAL = "external"


# --------------------------------------------------------------------------
# raw, per-translation-unit facts
# --------------------------------------------------------------------------


@dataclass(slots=True)
class IncludeEvent:
    """One ``#include`` edge, exactly as the compiler reported it.

    ``depth`` is the nesting level of the include (0 = included directly by
    the translation unit's source file).  ``line`` is the source line of the
    ``#include`` directive when the compiler tells us; it is ``None`` for
    toolchains that do not report it.
    """

    includer: Path
    included: Path
    depth: int
    line: int | None = None


@dataclass(slots=True)
class PhaseTiming:
    """One ``-ftime-report`` row: a compiler phase and its wall-clock cost."""

    name: str
    wall_seconds: float
    wall_percent: float | None = None
    memory_bytes: int | None = None

    @property
    def is_total(self) -> bool:
        return self.name.strip().lower() == "total"


@dataclass(slots=True)
class TranslationUnit:
    """Everything HeavyHeader learned about compiling one source file."""

    source: Path
    #: Dependency list produced by ``-M``/``-MM`` (flat, deduplicated, order
    #: preserved).  The source file itself is *not* part of this list.
    dependencies: list[Path] = field(default_factory=list)
    #: The include *tree*, as reported by ``-H`` (GCC/Clang) or
    #: ``/showIncludes`` (MSVC).  Kept separate from ``dependencies`` because
    #: the tree carries the nesting information the graph needs.
    include_events: list[IncludeEvent] = field(default_factory=list)
    #: Per-phase timings from ``-ftime-report``.
    timings: list[PhaseTiming] = field(default_factory=list)
    #: Headers the compiler flagged as missing an include guard.
    unguarded_headers: list[Path] = field(default_factory=list)
    #: Non-empty when the compiler refused to compile this file.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def total_wall_seconds(self) -> float:
        for timing in self.timings:
            if timing.is_total:
                return timing.wall_seconds
        return sum(t.wall_seconds for t in self.timings)

    def phase_seconds(self, name: str) -> float:
        wanted = name.strip().lower()
        for timing in self.timings:
            if timing.name.strip().lower() == wanted:
                return timing.wall_seconds
        return 0.0

    def direct_includes(self) -> list[Path]:
        """Headers included *directly* by the source file, first-seen order."""
        seen: dict[Path, None] = {}
        for event in self.include_events:
            if event.depth == 0:
                seen.setdefault(event.included)
        return list(seen)

    def children_of(self, header: Path) -> list[Path]:
        """Headers included directly by ``header`` inside this TU."""
        out: dict[Path, None] = {}
        for event in self.include_events:
            if event.includer == header and event.depth > 0:
                out.setdefault(event.included)
        return list(out)


# --------------------------------------------------------------------------
# derived, project-wide facts
# --------------------------------------------------------------------------


@dataclass(slots=True)
class HeaderStats:
    """Aggregated facts about one header across the whole project."""

    path: Path
    kind: HeaderKind = HeaderKind.PROJECT
    line_count: int = 0
    #: Number of translation units that pull this header in, directly or not.
    tu_reach: int = 0
    #: Number of translation units that ``#include`` it directly.
    tu_direct: int = 0
    #: Total number of inclusion events (a header included three times by
    #: different parents counts as three).
    inclusion_events: int = 0
    #: Deepest nesting level at which it was ever included.
    max_depth: int = 0
    #: Headers reachable from this one, excluding itself.
    transitive_headers: int = 0
    #: Lines of source in its transitive closure, excluding itself.
    transitive_lines: int = 0
    #: ``line_count * tu_reach`` -- how much text the project parses because
    #: of this header.
    attributed_lines: int = 0

    @property
    def cost_rank_key(self) -> tuple[int, int]:
        """Sort key: biggest attributed cost first, then widest reach."""
        return (self.attributed_lines, self.tu_reach)


@dataclass(slots=True)
class RemovalCandidate:
    """A ``#include`` line that might be safe to delete.

    ``estimated_lines_saved`` comes from the exclusive-closure computation;
    ``measured_seconds_saved`` is filled in later by ``heavyheader verify``
    and is the number that actually ends up in the report.
    """

    tu: Path
    header: Path
    line: int | None
    kind: HeaderKind
    #: Headers that become unreachable if this include is dropped.
    exclusive_headers: list[Path] = field(default_factory=list)
    estimated_lines_saved: int = 0
    estimated_percent: float = 0.0
    measured_seconds_saved: float | None = None
    verified: bool = False
    #: Result of the textual usage heuristic: "疑似未使用" / "正在使用" /
    #: "无法判定".  A *heuristic*, never a safety guarantee -- see
    #: :mod:`heavyheader.attrib.usage`.
    usage_label: str = "无法判定"
    #: Why we think it is (or is not) safe to remove.
    confidence: str = "unknown"
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProjectScan:
    """The whole result of a ``scan`` run."""

    root: Path
    compiler: str
    compiler_family: CompilerFamily
    standard: str | None
    tus: list[TranslationUnit] = field(default_factory=list)
    header_stats: dict[Path, HeaderStats] = field(default_factory=dict)
    candidates: list[RemovalCandidate] = field(default_factory=list)
    #: Sum of ``tu.total_wall_seconds()`` -- the baseline the savings
    #: percentages are relative to.
    baseline_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    # -- convenience accessors -------------------------------------------

    @property
    def ok_tus(self) -> list[TranslationUnit]:
        return [tu for tu in self.tus if tu.ok]

    @property
    def failed_tus(self) -> list[TranslationUnit]:
        return [tu for tu in self.tus if not tu.ok]

    def headers_by_cost(self) -> list[HeaderStats]:
        return sorted(
            self.header_stats.values(),
            key=lambda h: h.cost_rank_key,
            reverse=True,
        )

    def total_dependency_edges(self) -> int:
        return sum(len(tu.include_events) for tu in self.ok_tus)

    def total_included_lines(self) -> int:
        """Sum of ``attributed_lines`` -- total header text parsed project-wide."""
        return sum(h.attributed_lines for h in self.header_stats.values())

    def candidates_by_estimate(self) -> list[RemovalCandidate]:
        """Candidates ordered by how likely they are to be worth acting on.

        Two signals, in priority order:

        1. Whether the usage heuristic thinks the include is unused.  A
           header that is still in use is a *refactoring* signal (forward
           declarations, precompiled headers), not a deletion signal, and
           burying it under genuinely removable ones helps nobody.
        2. Estimated lines saved.

        Nothing is hidden by this ordering -- the report shows the usage
        verdict for every row -- it just puts the actionable ones first.
        """
        return sorted(
            self.candidates,
            key=lambda c: (
                c.usage_label == "疑似未使用",
                c.measured_seconds_saved if c.verified and c.measured_seconds_saved else c.estimated_lines_saved,
            ),
            reverse=True,
        )
