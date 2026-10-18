"""Tests for the include-tree and time-report parsers.

The ``-H`` fixture is copied verbatim from a real GCC 16 run on Windows; the
``-ftime-report`` fixture is a real table with the optional columns stripped
from some rows to exercise the parser's tolerance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.parse.includetree import (
    parse_include_guards,
    parse_include_tree,
    parse_show_includes,
)
from heavyheader.parse.timereport import TimeReportError, parse_time_report, summarise

TU = Path("main.cpp")

_H_OUTPUT = """. a.h
.. b.h
... c.h
.. d.h
Multiple include guards may be useful for:
e.h
"""


def test_include_tree_records_depth_and_parent():
    events = parse_include_tree(_H_OUTPUT, TU)
    assert [(e.included.name, e.depth) for e in events] == [
        ("a.h", 0),
        ("b.h", 1),
        ("c.h", 2),
        ("d.h", 1),
    ]
    assert events[0].includer == TU
    assert events[1].includer == Path("a.h")
    assert events[2].includer == Path("b.h")
    assert events[3].includer == Path("a.h")


def test_include_tree_skips_the_guard_trailer():
    events = parse_include_tree(_H_OUTPUT, TU)
    assert all(e.included.name != "e.h" for e in events)


def test_include_guards_trailer_is_parsed():
    guards = parse_include_guards(_H_OUTPUT)
    assert guards == [Path("e.h")]


def test_include_guards_absent_returns_empty():
    assert parse_include_guards(". a.h\n.. b.h\n") == []


def test_source_file_itself_is_not_an_event():
    events = parse_include_tree(". main.cpp\n.. a.h\n", TU)
    assert [e.included.name for e in events] == ["a.h"]


def test_deep_nesting_does_not_blow_the_stack():
    """1000+ levels of nesting is real in template-heavy code."""
    depth = 3000
    text = "".join("." * (i + 1) + f" h{i}.h\n" for i in range(depth))
    events = parse_include_tree(text, TU)
    assert len(events) == depth
    assert events[-1].depth == depth - 1


def test_show_includes_recovers_depth_from_indentation():
    # Real MSVC indents by one space per nesting level, starting at one.
    # Expected paths are written with forward slashes on purpose: that is the
    # normalised form the parser contract guarantees, and it compares equal
    # on Windows (pathlib parts) and on POSIX (same string).  Backslash
    # literals here would be Windows-only and silently fail the CI matrix.
    msvc = (
        "Note: including file: C:\\src\\a.h\n"
        "Note: including file:  C:\\src\\b.h\n"
        "Note: including file: C:\\src\\c.h\n"
    )
    events = parse_show_includes(msvc, TU)
    assert [(e.included.name, e.depth) for e in events] == [
        ("a.h", 0),
        ("b.h", 1),
        ("c.h", 0),
    ]
    assert events[1].includer == Path("C:/src/a.h")


def test_show_includes_handles_three_levels():
    msvc = (
        "Note: including file: C:\\src\\a.h\n"
        "Note: including file:  C:\\src\\b.h\n"
        "Note: including file:   C:\\src\\c.h\n"
        "Note: including file:  C:\\src\\d.h\n"
    )
    events = parse_show_includes(msvc, TU)
    assert [e.depth for e in events] == [0, 1, 2, 1]
    assert events[2].includer == Path("C:/src/b.h")
    # d.h sits at depth 1 again, so it is a *sibling* of b.h: its parent is
    # a.h, not b.h.  Getting this wrong would attribute cost to the wrong
    # header, which is exactly what the stack in the parser prevents.
    assert events[3].includer == Path("C:/src/a.h")


def test_show_includes_accepts_localised_chinese_prefix():
    """On a zh-CN install MSVC prints 「注意: 包含文件:」, not the English
    'Note: including file:'.  The regex must accept both, otherwise every
    edge is dropped and the include tree collapses to zero edges."""
    msvc = (
        "注意: 包含文件: C:\\src\\a.h\n"
        "注意: 包含文件:  C:\\src\\b.h\n"
        "注意: 包含文件:   C:\\src\\c.h\n"
        "注意: 包含文件:  C:\\src\\d.h\n"
    )
    events = parse_show_includes(msvc, TU)
    assert [e.depth for e in events] == [0, 1, 2, 1]
    assert events[0].included == Path("C:/src/a.h")
    assert events[2].includer == Path("C:/src/b.h")
    assert events[3].includer == Path("C:/src/a.h")


# --------------------------------------------------------------------------
# -ftime-report
# --------------------------------------------------------------------------

_TIME_REPORT = """Time variable                                  wall           GGC
 phase setup                        :   0.02 (  5%)  1856k (  4%)
 phase parsing                      :   0.28 ( 74%)    40M ( 83%)
 preprocessing                      :   0.14 ( 36%)  2723k (  5%)
 TOTAL                              :   0.37           49M
"""


def test_time_report_parses_all_rows():
    timings = parse_time_report(_TIME_REPORT)
    assert len(timings) == 4
    assert timings[0].name == "phase setup"
    assert timings[0].wall_seconds == 0.02
    assert timings[0].wall_percent == 5.0
    assert timings[0].memory_bytes == 1856 * 1024


def test_time_report_memory_units_are_binary():
    timings = parse_time_report(_TIME_REPORT)
    assert timings[1].memory_bytes == 40 * 1024 * 1024
    assert timings[3].memory_bytes == 49 * 1024 * 1024


def test_time_report_row_without_optional_columns():
    timings = parse_time_report(" TOTAL : 1.5\n")
    assert len(timings) == 1
    assert timings[0].wall_seconds == 1.5
    assert timings[0].wall_percent is None
    assert timings[0].memory_bytes is None


def test_time_report_phase_names_may_contain_spaces_and_dots():
    timings = parse_time_report(" phase lang. deferred : 0.02 ( 6%)\n")
    assert timings[0].name == "phase lang. deferred"


def test_time_report_ignores_unrelated_stderr_noise():
    text = _TIME_REPORT + "main.cpp:3:1: warning: unused variable 'x'\n\n"
    assert len(parse_time_report(text)) == 4


def test_time_report_with_no_rows_raises():
    with pytest.raises(TimeReportError):
        parse_time_report("nothing useful here\n")


def test_summarise_buckets_phases():
    """Each phase lands in the right bucket.

    ``TOTAL`` is kept as its own value rather than derived from the buckets:
    GCC's report omits some phases from the rows we see, and in a full report
    the phases overlap (``preprocessing`` is a subset of ``phase parsing``).
    Deriving a total by summing buckets would therefore be wrong twice over.
    """
    buckets = summarise(parse_time_report(_TIME_REPORT))
    assert buckets["total"] == 0.37
    assert buckets["preprocessing"] == 0.14
    assert buckets["parsing"] == 0.28
    assert buckets["other"] == pytest.approx(0.02)


def test_summarise_keeps_total_when_phases_are_missing():
    timings = parse_time_report(" TOTAL : 1.5\n")
    assert summarise(timings)["total"] == 1.5
