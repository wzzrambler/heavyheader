"""Baselines, regression detection and the CI gate.

This is where HeavyHeader stops being a report generator and becomes part of
a build pipeline.  The contract is deliberately tiny:

* ``scan`` writes a baseline (or appends to a history);
* ``gate`` compares a fresh scan against a stored baseline and exits non-zero
  when the build got meaningfully more expensive.

Two properties matter for that to be trustworthy:

1. **The comparison is relative.**  Absolute timings move with the machine,
   the load and the phase of the moon.  Ratios and per-header attributed-line
   counts are stable enough to gate on, so those are what we compare.

2. **The gate explains itself.**  A failing build that says "build cost
   regression +12.3%" is useless.  A failing build that names the three
   headers responsible and the include lines that introduced them is
   actionable, and is the difference between a tool people keep and a tool
   they disable after a week.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .models import HeaderKind, ProjectScan
from .report.jsonio import read_json, scan_from_dict, scan_to_dict, write_json

__all__ = [
    "BaselineError",
    "load_baseline",
    "save_baseline",
    "Comparison",
    "compare_scans",
    "GateResult",
    "evaluate_gate",
]


class BaselineError(RuntimeError):
    """A baseline could not be read or written."""


def load_baseline(path: str | Path) -> ProjectScan:
    try:
        return scan_from_dict(read_json(path))
    except (FileNotFoundError, ValueError, KeyError, TypeError) as exc:
        raise BaselineError(str(exc)) from exc


def save_baseline(path: str | Path, scan: ProjectScan) -> Path:
    try:
        return write_json(path, scan_to_dict(scan))
    except OSError as exc:  # pragma: no cover - trivial I/O guard
        raise BaselineError(f"cannot write baseline to {path}: {exc}") from exc


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


@dataclass(slots=True)
class HeaderDelta:
    """How one header's cost changed between two scans."""

    path: Path
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before

    @property
    def percent(self) -> float:
        if self.before == 0:
            return 100.0 if self.after else 0.0
        return (self.after - self.before) / self.before * 100.0


@dataclass(slots=True)
class Comparison:
    """Everything that changed between two scans."""

    total_seconds_before: float
    total_seconds_after: float
    attributed_lines_before: int
    attributed_lines_after: int
    tu_count_before: int
    tu_count_after: int
    header_deltas: list[HeaderDelta] = field(default_factory=list)
    new_headers: list[Path] = field(default_factory=list)
    gone_headers: list[Path] = field(default_factory=list)
    candidate_savings: int = 0
    unguarded_before: int = 0
    unguarded_after: int = 0

    @property
    def seconds_percent(self) -> float:
        return _percent(self.total_seconds_before, self.total_seconds_after)

    @property
    def attributed_percent(self) -> float:
        return _percent(self.attributed_lines_before, self.attributed_lines_after)

    @property
    def regressed_headers(self) -> list[HeaderDelta]:
        """Headers whose cost grew, worst first."""
        return sorted(
            (d for d in self.header_deltas if d.delta > 0),
            key=lambda d: d.delta,
            reverse=True,
        )

    @property
    def improved_headers(self) -> list[HeaderDelta]:
        return sorted(
            (d for d in self.header_deltas if d.delta < 0),
            key=lambda d: d.delta,
        )


def _percent(before: float, after: float) -> float:
    if before == 0:
        return 0.0 if after == 0 else 100.0
    return (after - before) / before * 100.0


def compare_scans(before: ProjectScan, after: ProjectScan) -> Comparison:
    """Diff two scans.

    Wall-clock seconds are reported but never trusted on their own: they are
    the noisiest number in the whole system.  Attributed lines are the
    headline metric because they are deterministic for a given source tree.
    """
    before_headers = before.header_stats
    after_headers = after.header_stats

    deltas: list[HeaderDelta] = []
    for path, stats in after_headers.items():
        prior = before_headers.get(path)
        deltas.append(
            HeaderDelta(
                path=path,
                before=prior.attributed_lines if prior else 0,
                after=stats.attributed_lines,
            )
        )
    # Headers that vanished still deserve a row so the report can show them.
    for path, stats in before_headers.items():
        if path not in after_headers:
            deltas.append(
                HeaderDelta(path=path, before=stats.attributed_lines, after=0)
            )

    new_headers = [p for p in after_headers if p not in before_headers]
    gone_headers = [p for p in before_headers if p not in after_headers]

    return Comparison(
        total_seconds_before=before.baseline_seconds,
        total_seconds_after=after.baseline_seconds,
        attributed_lines_before=before.total_included_lines(),
        attributed_lines_after=after.total_included_lines(),
        tu_count_before=len(before.ok_tus),
        tu_count_after=len(after.ok_tus),
        header_deltas=sorted(deltas, key=lambda d: abs(d.delta), reverse=True),
        new_headers=sorted(new_headers),
        gone_headers=sorted(gone_headers),
        candidate_savings=sum(c.estimated_lines_saved for c in after.candidates),
        unguarded_before=_unguarded_count(before),
        unguarded_after=_unguarded_count(after),
    )


def _unguarded_count(scan: ProjectScan) -> int:
    """Headers processed more often than there are TUs reaching them.

    That is only possible when a header lacks an include guard, so the count
    is a direct, deterministic measure of redundant re-parsing.
    """
    count = 0
    for stats in scan.header_stats.values():
        if stats.inclusion_events > stats.tu_reach:
            count += 1
    return count


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------


@dataclass(slots=True)
class GateResult:
    """Outcome of a gate evaluation."""

    passed: bool
    comparison: Comparison
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


def evaluate_gate(
    before: ProjectScan,
    after: ProjectScan,
    *,
    max_regression_percent: float = 5.0,
    max_new_heavy_headers: int = 0,
    heavy_header_lines: int = 2000,
    fail_on_new_unguarded: bool = True,
) -> GateResult:
    """Decide whether ``after`` is acceptable relative to ``before``.

    Defaults are conservative on purpose: a gate that fires on noise gets
    switched off, and a switched-off gate protects nothing.
    """
    comparison = compare_scans(before, after)
    reasons: list[str] = []
    warnings: list[str] = []

    regression = comparison.attributed_percent
    if regression > max_regression_percent:
        reasons.append(
            f"header cost regressed {regression:+.1f}% "
            f"({comparison.attributed_lines_before} -> "
            f"{comparison.attributed_lines_after} attributed lines), "
            f"budget is {max_regression_percent:+.1f}%"
        )
        for delta in comparison.regressed_headers[:3]:
            reasons.append(
                f"  worst offender: {delta.path} "
                f"{delta.before} -> {delta.after} lines ({delta.percent:+.1f}%)"
            )

    heavy_new = [
        p
        for p in comparison.new_headers
        if after.header_stats[p].line_count >= heavy_header_lines
    ]
    if len(heavy_new) > max_new_heavy_headers:
        reasons.append(
            f"{len(heavy_new)} new header(s) over {heavy_header_lines} lines "
            "entered the build"
        )
        for path in heavy_new[:3]:
            reasons.append(f"  new heavy header: {path}")

    if fail_on_new_unguarded and comparison.unguarded_after > comparison.unguarded_before:
        reasons.append(
            f"headers without include guards grew from "
            f"{comparison.unguarded_before} to {comparison.unguarded_after}"
        )

    if comparison.tu_count_after < comparison.tu_count_before:
        warnings.append(
            f"translation units dropped from {comparison.tu_count_before} to "
            f"{comparison.tu_count_after} -- did the scan miss files?"
        )

    # 一个编译单元都没编译成功时，这次扫描没有任何数据可依据：回归算出来是
    # -100%，三条判定全部落空，门禁会「通过」。构建坏得越彻底越容易通过——
    # 这把门禁的作用完全反转了。CI 上换工具链、漏传 -I、缺第三方头都会触发。
    if comparison.tu_count_after == 0:
        failed_after = len(after.failed_tus)
        reasons.append(
            "the scan produced no usable translation unit"
            + (f" ({failed_after} failed to compile)" if failed_after else "")
            + " -- a gate cannot conclude anything from an empty scan, so it "
            "fails rather than passing silently"
        )

    return GateResult(
        passed=not reasons,
        comparison=comparison,
        reasons=reasons,
        warnings=warnings,
    )
