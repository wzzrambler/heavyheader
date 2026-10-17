"""Tests for baselines, comparison and the CI gate.

The gate is the part of HeavyHeader that runs unattended in a pipeline, so
its failure modes matter more than its features: a gate that fires on noise
gets disabled, and a gate that misses a real regression protects nothing.
These tests pin down both directions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heavyheader.baseline import (
    BaselineError,
    compare_scans,
    evaluate_gate,
    load_baseline,
    save_baseline,
)
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    ProjectScan,
    TranslationUnit,
)
from heavyheader.report.jsonio import scan_from_dict, scan_to_dict


def _header(path: str, lines: int, reach: int) -> HeaderStats:
    return HeaderStats(
        path=Path(path),
        kind=HeaderKind.PROJECT,
        line_count=lines,
        tu_reach=reach,
        attributed_lines=lines * reach,
        inclusion_events=reach,
    )


def _scan(headers: list[HeaderStats], tus: int = 2, seconds: float = 1.0) -> ProjectScan:
    tu_list = [
        TranslationUnit(source=Path(f"/proj/t{i}.cpp"), dependencies=[])
        for i in range(tus)
    ]
    for tu in tu_list:
        # A TU with no timings reports zero seconds; give each one a total so
        # baseline_seconds is non-trivial.
        from heavyheader.models import PhaseTiming

        tu.timings = [PhaseTiming(name="TOTAL", wall_seconds=seconds / tus)]
    return ProjectScan(
        root=Path("/proj"),
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=tu_list,
        header_stats={h.path: h for h in headers},
        baseline_seconds=seconds,
    )


# --------------------------------------------------------------------------
# serialisation round-trip
# --------------------------------------------------------------------------


def test_scan_survives_a_json_round_trip(tmp_path):
    scan = _scan([_header("/proj/a.h", 10, 2)], seconds=1.5)
    payload = scan_to_dict(scan)
    restored = scan_from_dict(payload)

    assert restored.baseline_seconds == pytest.approx(1.5)
    assert restored.total_included_lines() == 20
    assert restored.header_stats[Path("/proj/a.h")].attributed_lines == 20
    assert len(restored.ok_tus) == 2


def test_round_trip_keeps_candidates(tmp_path):
    from heavyheader.models import RemovalCandidate

    scan = _scan([_header("/proj/a.h", 10, 2)])
    scan.candidates = [
        RemovalCandidate(
            tu=Path("/proj/t0.cpp"),
            header=Path("/proj/a.h"),
            line=3,
            kind=HeaderKind.PROJECT,
            exclusive_headers=[Path("/proj/b.h")],
            estimated_lines_saved=42,
            estimated_percent=12.5,
            usage_label="疑似未使用",
            confidence="high",
        )
    ]
    restored = scan_from_dict(scan_to_dict(scan))
    assert len(restored.candidates) == 1
    candidate = restored.candidates[0]
    assert candidate.estimated_lines_saved == 42
    assert candidate.line == 3
    assert candidate.usage_label == "疑似未使用"


def test_baseline_file_round_trip(tmp_path):
    scan = _scan([_header("/proj/a.h", 10, 2)])
    path = tmp_path / "baseline.json"
    save_baseline(path, scan)
    assert path.is_file()
    assert load_baseline(path).total_included_lines() == 20


def test_missing_baseline_raises_a_clear_error(tmp_path):
    with pytest.raises(BaselineError, match="does not exist"):
        load_baseline(tmp_path / "nope.json")


def test_malformed_baseline_raises_a_clear_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(BaselineError, match="not valid JSON"):
        load_baseline(path)


# --------------------------------------------------------------------------
# comparison
# --------------------------------------------------------------------------


def test_compare_reports_growth():
    before = _scan([_header("/proj/a.h", 10, 2)])
    after = _scan([_header("/proj/a.h", 10, 4)])
    comparison = compare_scans(before, after)
    assert comparison.attributed_lines_before == 20
    assert comparison.attributed_lines_after == 40
    assert comparison.attributed_percent == pytest.approx(100.0)


def test_compare_identifies_the_offending_header():
    before = _scan([_header("/proj/a.h", 10, 2), _header("/proj/b.h", 5, 2)])
    after = _scan([_header("/proj/a.h", 10, 2), _header("/proj/b.h", 5, 6)])
    comparison = compare_scans(before, after)
    worst = comparison.regressed_headers[0]
    assert worst.path == Path("/proj/b.h")
    assert worst.delta == 20


def test_compare_lists_new_and_removed_headers():
    before = _scan([_header("/proj/a.h", 10, 2)])
    after = _scan([_header("/proj/b.h", 10, 2)])
    comparison = compare_scans(before, after)
    assert comparison.new_headers == [Path("/proj/b.h")]
    assert comparison.gone_headers == [Path("/proj/a.h")]


def test_compare_percent_is_zero_when_baseline_is_empty():
    before = _scan([])
    after = _scan([_header("/proj/a.h", 10, 2)])
    comparison = compare_scans(before, after)
    assert comparison.attributed_percent == 100.0


def test_unguarded_count_uses_events_minus_reach():
    """A header processed more often than there are TUs reaching it is unguarded."""
    from heavyheader.models import IncludeEvent

    source = Path("/proj/main.cpp")
    tu = TranslationUnit(
        source=source,
        include_events=[
            IncludeEvent(includer=source, included=Path("/proj/u.h"), depth=0),
            IncludeEvent(includer=Path("/proj/p.h"), included=Path("/proj/u.h"), depth=1),
        ],
    )
    scan = ProjectScan(
        root=Path("/proj"),
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
        header_stats={
            # Processed twice, reached by one TU -> unguarded.
            Path("/proj/u.h"): HeaderStats(
                path=Path("/proj/u.h"),
                kind=HeaderKind.PROJECT,
                inclusion_events=2,
                tu_reach=1,
            ),
            # Processed once, reached once -> guarded, not flagged.
            Path("/proj/g.h"): HeaderStats(
                path=Path("/proj/g.h"),
                kind=HeaderKind.PROJECT,
                inclusion_events=1,
                tu_reach=1,
            ),
        },
    )
    assert _unguarded(scan) == 1


def _unguarded(scan: ProjectScan) -> int:
    from heavyheader.baseline import _unguarded_count

    return _unguarded_count(scan)


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------


def test_gate_passes_when_cost_is_flat():
    before = _scan([_header("/proj/a.h", 10, 2)])
    after = _scan([_header("/proj/a.h", 10, 2)])
    result = evaluate_gate(before, after, max_regression_percent=5.0)
    assert result.passed
    assert result.exit_code == 0
    assert not result.reasons


def test_gate_fails_on_regression_and_names_the_culprit():
    before = _scan([_header("/proj/a.h", 10, 2)])
    after = _scan([_header("/proj/a.h", 10, 5)])  # +150%
    result = evaluate_gate(before, after, max_regression_percent=5.0)
    assert not result.passed
    assert result.exit_code == 1
    assert any("regressed" in reason for reason in result.reasons)
    assert any("a.h" in reason for reason in result.reasons)


def test_gate_passes_an_improvement():
    before = _scan([_header("/proj/a.h", 10, 5)])
    after = _scan([_header("/proj/a.h", 10, 2)])
    result = evaluate_gate(before, after, max_regression_percent=5.0)
    assert result.passed


def test_gate_fails_when_a_heavy_header_appears():
    before = _scan([_header("/proj/a.h", 10, 2)])
    after = _scan(
        [_header("/proj/a.h", 10, 2), _header("/proj/huge.h", 5000, 1)]
    )
    result = evaluate_gate(before, after, max_regression_percent=5.0)
    assert not result.passed
    assert any("heavy header" in reason.lower() for reason in result.reasons)


def test_gate_fails_when_unguarded_headers_grow():
    before = _scan([_header("/proj/a.h", 10, 2)])
    after = _scan([_header("/proj/a.h", 10, 2), _header("/proj/u.h", 5, 1)])
    # Force the unguarded signal: u.h is processed twice but reached by one TU.
    after.header_stats[Path("/proj/u.h")].inclusion_events = 2
    result = evaluate_gate(before, after, max_regression_percent=5.0)
    assert not result.passed
    assert any("include guards" in reason for reason in result.reasons)


def test_gate_warns_when_translation_units_disappear():
    before = _scan([_header("/proj/a.h", 10, 2)], tus=4)
    after = _scan([_header("/proj/a.h", 10, 2)], tus=2)
    result = evaluate_gate(before, after, max_regression_percent=5.0)
    assert result.passed  # not a failure, but it must not pass silently
    assert any("translation units dropped" in w for w in result.warnings)


def test_gate_defaults_are_conservative():
    """A 1% wobble must not fail the build; a gate that fires on noise dies."""
    before = _scan([_header("/proj/a.h", 1000, 2)])
    after = _scan([_header("/proj/a.h", 1005, 2)])
    result = evaluate_gate(before, after)
    assert result.passed


# --------------------------------------------------------------------------
# the gate's own failure modes
# --------------------------------------------------------------------------


def _failed_scan() -> ProjectScan:
    """A scan where every TU failed to compile — no usable data at all."""
    tu = TranslationUnit(source=Path("/proj/t0.cpp"), dependencies=[])
    tu.error = "fatal error: missing header"
    return ProjectScan(
        root=Path("/proj"),
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
    )


def test_gate_fails_when_the_scan_produced_nothing():
    """The inverted gate.

    With zero successful TUs the regression computes to -100%, so all three
    checks miss and ``passed`` came out True: the more completely the build
    was broken, the easier it was to pass.  A gate that passes on an empty
    scan protects nothing, so it must fail instead.
    """
    before = _scan([_header("/proj/a.h", 100, 1)], tus=1)
    after = _failed_scan()

    result = evaluate_gate(before, after, max_regression_percent=5.0)

    assert result.passed is False
    assert any("no usable translation unit" in r for r in result.reasons)


def test_gate_still_passes_a_legitimate_improvement():
    """Guarding against over-correction: a real drop in cost is still a pass."""
    before = _scan([_header("/proj/a.h", 100, 2)], tus=2)
    after = _scan([_header("/proj/a.h", 40, 2)], tus=2)

    result = evaluate_gate(before, after, max_regression_percent=5.0)

    assert result.passed is True
    assert result.reasons == []


def test_gate_warns_when_units_disappeared_but_still_passes():
    """Deleting a source file legitimately lowers cost; it must not be
    reported as a regression, but the coverage change must be visible."""
    before = _scan([_header("/proj/a.h", 100, 2)], tus=2)
    after = _scan([_header("/proj/a.h", 50, 1)], tus=1)

    result = evaluate_gate(before, after, max_regression_percent=5.0)

    assert result.passed is True
    assert any("translation units dropped" in w for w in result.warnings)
