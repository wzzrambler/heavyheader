"""Tests for template instantiation cost attribution.

The fixture in ``test_parses_real_gcc_template_row`` is copied verbatim from a
real GCC 16.2 run compiling a file that includes ``nlohmann/json.hpp`` --
0.44s of 1.13s, i.e. 39%.  That figure is the reason this module exists, so
the test pins it rather than a made-up number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.attrib.templates import (
    TemplateProfile,
    count_template_declarations,
    profile_project,
)
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    ProjectScan,
    TranslationUnit,
)
from heavyheader.parse.timereport import PhaseTiming, parse_time_report

# Real GCC 16.2 output for a file including nlohmann/json.hpp.
_REAL_REPORT = """Time variable                                  wall           GGC
 phase setup                        :   0.00 (  0%)  1856k (  1%)
 phase parsing                      :   0.77 ( 68%)   162M ( 69%)
 phase lang. deferred               :   0.36 ( 32%)    69M ( 30%)
 preprocessing                      :   0.28 ( 25%)    10M (  5%)
 template instantiation             :   0.44 ( 39%)    96M ( 41%)
 TOTAL                              :   1.13          233M
"""


def _tu(source: str, rows: str) -> TranslationUnit:
    tu = TranslationUnit(source=Path(source))
    tu.timings = parse_time_report(rows)
    return tu


# --------------------------------------------------------------------------
# counting template declarations
# --------------------------------------------------------------------------


def test_counts_template_declarations(tmp_path):
    header = tmp_path / "a.h"
    header.write_text(
        "template <typename T> struct A {};\n"
        "template <class T, class U> void f(T, U) {}\n"
        "// template <typename T> mentioned in a comment only\n"
        "template<typename T>\nT identity(T v) { return v; }\n",
        encoding="utf-8",
    )
    assert count_template_declarations(header) == 3


def test_ignores_templates_inside_comments(tmp_path):
    header = tmp_path / "b.h"
    header.write_text(
        "/* template <typename T> in a block comment */\n"
        "// template <typename T> on a line comment\n"
        "int plain();\n",
        encoding="utf-8",
    )
    assert count_template_declarations(header) == 0


def test_ignores_templates_inside_string_literals(tmp_path):
    header = tmp_path / "c.h"
    header.write_text(
        'const char* doc = "template <typename T> appears in a string";\n'
        "int plain();\n",
        encoding="utf-8",
    )
    assert count_template_declarations(header) == 0


def test_unreadable_header_counts_zero(tmp_path):
    assert count_template_declarations(tmp_path / "nope.h") == 0


# --------------------------------------------------------------------------
# per-TU attribution (exact, from -ftime-report)
# --------------------------------------------------------------------------


def test_parses_real_gcc_template_row():
    """The fixture is verbatim GCC output: 0.44s of 1.13s is 39%."""
    tu = _tu("/proj/main.cpp", _REAL_REPORT)
    assert tu.template_instantiation_seconds() == pytest.approx(0.44)
    assert tu.total_wall_seconds() == pytest.approx(1.13)
    assert tu.template_instantiation_percent() == pytest.approx(38.94, abs=0.1)


def test_percent_is_zero_without_a_total():
    """No TOTAL row means the total is unknown, not "sum the phases".

    GCC's phases overlap, so summing them would double-count.  The honest
    answer for a missing total is 0.0, and a percentage against an unknown
    total is likewise 0.0 rather than a confident-looking wrong number.
    """
    tu = TranslationUnit(source=Path("/proj/a.cpp"))
    tu.timings = [PhaseTiming(name="template instantiation", wall_seconds=0.2)]
    assert tu.template_instantiation_seconds() == 0.2
    assert tu.total_wall_seconds() == 0.0
    assert tu.template_instantiation_percent() == 0.0


def test_percent_handles_zero_total():
    tu = TranslationUnit(source=Path("/proj/a.cpp"))
    tu.timings = [
        PhaseTiming(name="template instantiation", wall_seconds=0.0),
        PhaseTiming(name="TOTAL", wall_seconds=0.0),
    ]
    assert tu.template_instantiation_percent() == 0.0


# --------------------------------------------------------------------------
# project-wide profile
# --------------------------------------------------------------------------


def _scan(tus: list[TranslationUnit], root: Path) -> ProjectScan:
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=tus,
    )
    scan.header_stats = {
        root / "heavy.h": HeaderStats(
            path=root / "heavy.h",
            kind=HeaderKind.PROJECT,
            line_count=100,
            tu_reach=3,
        ),
        Path("/usr/include/type_traits"): HeaderStats(
            path=Path("/usr/include/type_traits"),
            kind=HeaderKind.SYSTEM,
            tu_reach=3,
        ),
    }
    return scan


def test_profile_separates_heavy_tus(tmp_path):
    heavy = _tu("/proj/heavy.cpp", _REAL_REPORT)
    light_rows = (
        " phase parsing : 0.05 ( 80%)\n"
        " template instantiation : 0.01 ( 10%)\n"
        " TOTAL : 0.08\n"
    )
    light = _tu("/proj/light.cpp", light_rows)

    scan = _scan([heavy, light], tmp_path)
    profile = profile_project(scan)

    assert profile.heavy_tus == [Path("/proj/heavy.cpp")]
    assert profile.per_tu[0][0] == Path("/proj/heavy.cpp")  # sorted by percent
    assert profile.total_seconds == pytest.approx(0.45)
    assert profile.is_significant


def test_profile_marks_insignificant_projects(tmp_path):
    rows = (
        " phase parsing : 0.10 ( 90%)\n"
        " template instantiation : 0.01 (  5%)\n"
        " TOTAL : 0.20\n"
    )
    scan = _scan([_tu("/proj/a.cpp", rows)], tmp_path)
    profile = profile_project(scan)
    assert not profile.is_significant


def test_profile_computes_template_surface(tmp_path):
    heavy = tmp_path / "heavy.h"
    heavy.write_text(
        "".join(f"template <typename T{i}> struct S{i} {{}};\n" for i in range(5)),
        encoding="utf-8",
    )
    scan = _scan([_tu("/proj/a.cpp", _REAL_REPORT)], tmp_path)
    profile = profile_project(scan)

    stats = scan.header_stats[tmp_path / "heavy.h"]
    assert stats.template_declarations == 5
    assert stats.template_surface == 15  # 5 declarations x 3 TUs

    # by_surface is sorted by declarations * reach, descending.
    assert profile.by_surface[0][0] == tmp_path / "heavy.h"


def test_profile_tolerates_zero_build_time(tmp_path):
    """A scan where every TU timed out must not divide by zero."""
    tu = TranslationUnit(source=Path("/proj/a.cpp"))
    scan = _scan([tu], tmp_path)
    profile = profile_project(scan)
    assert profile.total_seconds == 0.0
    assert profile.overall_percent == 0.0
    assert not profile.is_significant
    assert profile.per_tu == []
