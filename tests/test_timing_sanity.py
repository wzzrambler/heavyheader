"""Tests for the timing sanity check.

The fixture is not invented.  GCC 16.2.0 on MinGW-w64 really does print
``2**64 / 1e9`` seconds for ``phase parsing`` when compiling a file that
includes ``<windows.h>``.  A tool that reports that number looks
authoritative and is useless, which is why this check exists.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from heavyheader.parse.timereport import (
    PhaseTiming,
    TimeReportError,
    parse_time_report,
    sanitise_timings,
    summarise,
)


# The value GCC 16.2.0 on MinGW-w64 actually prints for a broken phase,
# verbatim from a run compiling a file that includes <windows.h>.  It is
# ~584 years, i.e. 2**64 nanoseconds expressed in seconds.
_GCC_BROKEN = 18446744070.35


def _row(name: str, seconds: float) -> PhaseTiming:
    return PhaseTiming(name=name, wall_seconds=seconds)


def test_impossible_timings_are_discarded():
    timings = [
        _row("phase setup", 0.01),
        _row("phase parsing", _GCC_BROKEN),
        _row("TOTAL", _GCC_BROKEN + 0.07),
    ]
    kept, report = sanitise_timings(timings, measured_wall_seconds=0.9)
    assert [t.name for t in kept] == ["phase setup"]
    assert report.total_was_discarded
    assert any("phase parsing" in d for d in report.discarded)


def test_plausible_timings_survive_untouched():
    timings = [
        _row("phase setup", 0.01),
        _row("phase parsing", 0.39),
        _row("preprocessing", 0.15),
        _row("TOTAL", 0.54),
    ]
    kept, report = sanitise_timings(timings, measured_wall_seconds=0.9)
    assert len(kept) == 4
    assert report.clean


def test_a_phase_may_not_outlast_its_process():
    """The physical bound: a phase cannot exceed the process containing it."""
    # Measured 0.9s wall; a phase claiming 0.95s is inside the slack and
    # survives, one claiming 5s cannot be real.
    kept, _ = sanitise_timings([_row("phase parsing", 0.95)], 0.9)
    assert len(kept) == 1
    kept, _ = sanitise_timings([_row("phase parsing", 5.0)], 0.9)
    assert not kept


def test_slack_absorbs_clock_skew():
    """Our perf_counter and the compiler's internal clock are not the same clock."""
    # 0.2s of slack covers the difference; do not reject legitimate rows
    # just because the two clocks disagree slightly.
    kept, report = sanitise_timings([_row("TOTAL", 1.05)], 0.9)
    assert len(kept) == 1
    assert report.clean


def test_discarded_rows_are_named_for_the_user():
    timings = [_row("phase parsing", _GCC_BROKEN)]
    _, report = sanitise_timings(timings, measured_wall_seconds=0.5)
    assert len(report.discarded) == 1
    assert "phase parsing" in report.discarded[0]
    assert "18446744070" in report.discarded[0]


def test_all_rows_discarded_leaves_a_usable_scan():
    """When every row is garbage the TU must still work, just with no timings."""
    timings = [_row("phase parsing", _GCC_BROKEN), _row("TOTAL", _GCC_BROKEN)]
    kept, report = sanitise_timings(timings, 0.4)
    assert kept == []
    assert report.total_was_discarded
    # A TU with no timings reports zero seconds rather than crashing.
    assert sum(t.wall_seconds for t in kept) == 0.0


def test_zero_measured_wall_does_not_reject_everything():
    """A very fast compile measures near-zero wall time; do not nuke its timings."""
    kept, report = sanitise_timings([_row("TOTAL", 0.02)], measured_wall_seconds=0.0)
    assert len(kept) == 1
    assert report.clean


# --------------------------------------------------------------------------
# the end-to-end path: real broken output in, sane numbers out
# --------------------------------------------------------------------------

_BROKEN_REPORT = """Time variable                                  wall           GGC
 phase setup                        :   0.00 (  0%)  1856k (  1%)
 phase parsing                      :18446744070.35 (100%)   158M ( 92%)
 phase lang. deferred               :   0.07 (  0%)    12M (  7%)
 preprocessing                      :   0.35 (  0%)    18M ( 10%)
 TOTAL                              :18446744070.42          172M
"""


def test_broken_gcc_output_yields_sane_numbers():
    """The whole point: parse real broken output, then reject the impossible rows."""
    parsed = parse_time_report(_BROKEN_REPORT)
    assert len(parsed) == 5
    # Before sanitising, the garbage is right there -- the parser is faithful.
    assert parsed[1].wall_seconds == _GCC_BROKEN

    kept, report = sanitise_timings(parsed, measured_wall_seconds=0.95)
    names = [t.name for t in kept]
    assert "phase parsing" not in names
    assert "TOTAL" not in names
    assert "preprocessing" in names
    assert not report.clean

    # And the surviving rows still summarise into something meaningful.
    buckets = summarise(kept)
    assert buckets["preprocessing"] == pytest.approx(0.35)


def test_summarise_ignores_a_missing_total():
    timings = [_row("preprocessing", 0.35), _row("phase parsing", 0.39)]
    buckets = summarise(timings)
    assert buckets["total"] == 0.0  # no TOTAL row -> zero, not a guess
    assert buckets["preprocessing"] == 0.35


# --------------------------------------------------------------------------
# wiring: broken compiler output must reach the user, not be silently fixed
# --------------------------------------------------------------------------


def test_driver_records_discarded_timings_on_the_tu():
    """The chain that matters: broken stderr -> TU.timing_discarded.

    Verified at the driver level rather than end-to-end because GCC's broken
    timer is intermittent -- an end-to-end test would be flaky, and a flaky
    test that sometimes passes teaches nobody anything.
    """
    from heavyheader.drivers.base import GccLikeDriver

    driver = GccLikeDriver("g++")
    source = Path("/proj/os.cc")

    # -H tree for one project header, followed by a broken timing table.
    stderr = (
        f". {source}\n"
        ".. /proj/os.h\n"
        + _BROKEN_REPORT
    )
    tu = driver.interpret(
        source=source,
        returncode=0,
        stdout="",
        stderr=stderr,
        wall_seconds=0.95,
    )

    assert tu.ok
    assert tu.timing_discarded, "broken timings were silently accepted"
    assert any("phase parsing" in d for d in tu.timing_discarded)
    # The include tree is unaffected: we still learned what was included.
    assert [e.included for e in tu.include_events] == [Path("/proj/os.h")]
    # And the surviving rows are still usable.
    assert any(t.name == "preprocessing" for t in tu.timings)


def test_driver_keeps_good_timings_clean():
    from heavyheader.drivers.base import GccLikeDriver

    driver = GccLikeDriver("g++")
    source = Path("/proj/a.cc")
    stderr = f". {source}\n.. /proj/a.h\n" + (
        " phase parsing : 0.30 (60%)\n"
        " TOTAL : 0.50\n"
    )
    tu = driver.interpret(source, 0, "", stderr, wall_seconds=0.9)
    assert tu.ok
    assert tu.timing_discarded == []
    assert tu.total_wall_seconds() == 0.50


# --------------------------------------------------------------------------
# wiring: broken compiler output must reach the user, not be silently fixed
# --------------------------------------------------------------------------


def test_driver_records_discarded_timings_on_the_tu():
    """The chain that matters: broken stderr -> TU.timing_discarded.

    Verified at the driver level rather than end-to-end because GCC's broken
    timer is intermittent -- an end-to-end test would be flaky, and a flaky
    test that sometimes passes teaches nobody anything.
    """
    from heavyheader.drivers.base import GccLikeDriver

    driver = GccLikeDriver("g++")
    source = Path("/proj/os.cc")

    # -H tree for one project header, followed by a broken timing table.
    stderr = (
        f". {source}\n"
        ".. /proj/os.h\n"
        + _BROKEN_REPORT
    )
    tu = driver.interpret(
        source=source,
        returncode=0,
        stdout="",
        stderr=stderr,
        wall_seconds=0.95,
    )

    assert tu.ok
    assert tu.timing_discarded, "broken timings were silently accepted"
    assert any("phase parsing" in d for d in tu.timing_discarded)
    # The include tree is unaffected: we still learned what was included.
    assert [e.included for e in tu.include_events] == [Path("/proj/os.h")]
    # And the surviving rows are still usable.
    assert any(t.name == "preprocessing" for t in tu.timings)


def test_driver_keeps_good_timings_clean():
    from heavyheader.drivers.base import GccLikeDriver

    driver = GccLikeDriver("g++")
    source = Path("/proj/a.cc")
    stderr = f". {source}\n.. /proj/a.h\n" + (
        " phase parsing : 0.30 (60%)\n"
        " TOTAL : 0.50\n"
    )
    tu = driver.interpret(source, 0, "", stderr, wall_seconds=0.9)
    assert tu.ok
    assert tu.timing_discarded == []
    assert tu.total_wall_seconds() == 0.50
