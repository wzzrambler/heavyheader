"""Tests for the ``verify`` command's compile-option plumbing.

verify 会重新编译用户的代码，所以它拿到的编译选项必须和 scan 时真正生效的
那一份一致。这里有两类真实故障：

1. **基线编译没有布局兜底**：scan 在不传 ``-I`` 时会退回 ``include/`` 等常规
   目录，verify 却原样透传空 options，于是扫描能找到的头文件验证时找不到，
   基线直接编译失败——对最常见的 ``include/`` 布局，主打功能整体失效；
2. **打补丁那侧丢掉了 options 里的 -I**：它改用独立的 ``include_dirs`` kwarg，
   与基线口径不一致，于是本可安全删除的 include 被误报成「移除后编译失败」。

两类都是「选项传递」问题，用 mock 拦住真正的编译调用即可精确断言，不需要
真编译器，也就不会受机器负载影响。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.drivers.base import DriverOptions
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    ProjectScan,
    RemovalCandidate,
    TranslationUnit,
)
from heavyheader.verify import (
    _path_matches,
    find_include_line,
    find_include_lines,
    locate_include_line,
    verify_candidates,
)


def _header_stats(path: Path, lines: int, reach: int) -> HeaderStats:
    return HeaderStats(
        path=path,
        kind=HeaderKind.PROJECT,
        line_count=lines,
        tu_reach=reach,
        attributed_lines=lines * reach,
        inclusion_events=reach,
    )


class _RecordingDriver:
    """A driver that never compiles anything; ``_measure`` is mocked anyway."""

    family = CompilerFamily.GCC

    def __init__(self):
        self.executable = "g++"


def _project(tmp_path: Path) -> tuple[ProjectScan, DriverOptions, Path]:
    """An ``include/``-layout project with one removable include."""
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    header = tmp_path / "include" / "heavy.h"
    header.write_text("#pragma once\nstruct Heavy { int x; };\n", encoding="utf-8")
    source = tmp_path / "src" / "app.cpp"
    source.write_text('#include "heavy.h"\nint main() { return 0; }\n', encoding="utf-8")

    tu = TranslationUnit(source=source, dependencies=[header])
    scan = ProjectScan(
        root=tmp_path,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
        header_stats={header: _header_stats(header, 2, 1)},
    )
    scan.candidates = [
        RemovalCandidate(
            tu=source,
            header=header,
            line=1,
            kind=HeaderKind.PROJECT,
            exclusive_headers=[header],
            estimated_lines_saved=2,
            estimated_percent=100.0,
        )
    ]
    options = DriverOptions(standard="c++17", include_dirs=[])
    return scan, options, source


@pytest.fixture
def captured(monkeypatch):
    """Capture the options ``_measure`` is called with; return the list."""
    seen: list[tuple[str, DriverOptions]] = []

    def _fake_measure(driver, source_path, options, _extra, runs, timeout):
        seen.append((str(source_path), options))
        # (min_seconds, memory_bytes, error, discarded_timings)
        # ——与真实 _measure 的四元组一致。
        return 0.010, None, None, ()

    monkeypatch.setattr("heavyheader.verify._measure", _fake_measure)
    return seen


def _sides(captured, source):
    """Split the captured calls into (baseline, patched) by *path*, not order.

    The pipeline probes the patched copy first and only then measures the
    baseline, so "first call is the baseline" stopped being true the moment
    probing became a stage.  Indexing by position would make these tests
    assert the old call order rather than the property they exist to protect:
    that both sides are compiled against the same include paths.
    """
    baseline = patched = None
    for path, options in captured:
        if path == str(source):
            baseline = options
        else:
            patched = options
    return baseline, patched


def test_baseline_inherits_the_scan_layout_fallback(tmp_path, captured):
    """Failure mode 1: an include/ layout must resolve without passing -I."""
    scan, options, source = _project(tmp_path)

    results = verify_candidates(scan, _RecordingDriver(), options)

    assert results, "expected the candidate to be exercised"
    baseline_options, _patched = _sides(captured, source)
    assert baseline_options is not None
    # 兜底目录必须包含项目的 include/，否则基线编译找不到头文件。
    assert tmp_path / "include" in baseline_options.include_dirs
    assert tmp_path in baseline_options.include_dirs


def test_patched_side_keeps_include_dirs_from_options(tmp_path, captured):
    """Failure mode 2: dropping options.include_dirs fakes a compile error."""
    scan, options, source = _project(tmp_path)
    options = DriverOptions(
        standard="c++17", include_dirs=[tmp_path / "include", tmp_path / "third_party"]
    )

    verify_candidates(scan, _RecordingDriver(), options)

    _baseline, patched_options = _sides(captured, source)
    assert patched_options is not None
    assert tmp_path / "include" in patched_options.include_dirs
    assert tmp_path / "third_party" in patched_options.include_dirs
    # 引用的 include 相对于包含它的文件目录解析，因此原目录必须补上。
    assert source.parent in patched_options.include_dirs


def test_both_sides_are_compiled_with_identical_include_dirs(tmp_path, captured):
    """The comparison is only meaningful if both sides see the same world."""
    scan, options, source = _project(tmp_path)
    options = DriverOptions(standard="c++17", include_dirs=[tmp_path / "include"])

    verify_candidates(scan, _RecordingDriver(), options)

    baseline_options, patched_options = _sides(captured, source)
    assert baseline_options is not None and patched_options is not None
    # 打补丁那侧会额外带上原文件目录（ sibling 头解析需要），其余必须一致。
    assert set(patched_options.include_dirs) - {source.parent} == set(
        baseline_options.include_dirs
    )
    assert baseline_options.standard == patched_options.standard


def test_unreadable_source_is_reported_not_raised(tmp_path):
    """A source file that vanished between scan and verify must not crash."""
    scan, options, source = _project(tmp_path)
    source.unlink()

    results = verify_candidates(scan, _RecordingDriver(), options)

    assert len(results) == 1
    assert results[0].status == "error"
    assert "cannot read" in results[0].message


# --------------------------------------------------------------------------
# same-named headers
# --------------------------------------------------------------------------

_SOURCE_TWO_THINGS = (
    '#include "util/thing.h"\n'
    '#include "other/thing.h"\n'
    "int main() { return 0; }\n"
)


def test_include_text_is_matched_against_the_full_path():
    """``util/thing.h`` 与 ``other/thing.h`` 只比文件名时完全一样，
    比完整路径后缀就能分开。"""
    util = Path("/proj/util/thing.h")
    other = Path("/proj/other/thing.h")

    assert _path_matches("util/thing.h", util) is True
    assert _path_matches("util/thing.h", other) is False
    assert _path_matches("other/thing.h", other) is True
    assert _path_matches("other/thing.h", util) is False


def test_each_same_named_header_gets_its_own_line():
    """修复前两行都会被判给先出现的那个，于是删掉的是另一个文件。"""
    util = Path("/proj/util/thing.h")
    other = Path("/proj/other/thing.h")

    assert find_include_lines(_SOURCE_TWO_THINGS, other) == [2]
    assert find_include_lines(_SOURCE_TWO_THINGS, util) == [1]


def test_find_include_line_refuses_to_guess_between_same_named_headers():
    source = '#include "thing.h"\n#include "thing.h"\nint main(){return 0;}\n'

    assert find_include_line(source, Path("/proj/other/thing.h")) is None


def test_locate_reports_ambiguous_instead_of_picking_a_line(tmp_path):
    """删错一行的代价：verify 给出一条看起来可信、实则指向别处的结论。"""
    source = '#include "thing.h"\n#include "util/thing.h"\nint main(){return 0;}\n'
    location = locate_include_line(source, Path("/proj/util/thing.h"))

    assert location.line is None
    assert location.status == "ambiguous"
    assert location.matches >= 2


def test_verify_reports_ambiguous_without_touching_the_file(tmp_path):
    """歧义时不能照常编译——那等于替用户随机删一行。"""
    header = tmp_path / "util" / "thing.h"
    header.parent.mkdir()
    header.write_text("#pragma once\nstruct T{};\n", encoding="utf-8")
    source = tmp_path / "src" / "a.cpp"
    source.parent.mkdir()
    source.write_text(
        '#include "thing.h"\n#include "util/thing.h"\nint main(){return 0;}\n',
        encoding="utf-8",
    )

    tu = TranslationUnit(source=source, dependencies=[header])
    scan = ProjectScan(
        root=tmp_path,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
        header_stats={header: _header_stats(header, 2, 1)},
    )
    scan.candidates = [
        RemovalCandidate(
            tu=source,
            header=header,
            line=None,
            kind=HeaderKind.PROJECT,
            exclusive_headers=[header],
            estimated_lines_saved=2,
            estimated_percent=100.0,
        )
    ]

    results = verify_candidates(
        scan, _RecordingDriver(), DriverOptions(standard="c++17")
    )

    assert len(results) == 1
    assert results[0].status == "ambiguous"
    assert "无法判断该注释掉哪一行" in results[0].message
    # 原始文件一个字节都没动。
    assert source.read_text(encoding="utf-8").count("#include") == 2


# --------------------------------------------------------------------------
# the bug that produced false compile errors
# --------------------------------------------------------------------------


def test_probe_uses_the_unit_own_options_not_the_shared_ones(tmp_path, captured):
    """The scan must hand each unit's real flags to the probe stage.

    On a ``compile_commands.json`` project every unit has its own ``-I``.
    Rebuilding a unit from the shared ``ScanOptions`` drops them, the
    compiler cannot find the project's headers, and *every* candidate comes
    back "compile error" -- which reads as "nothing here is removable" when
    the truth is "we asked the wrong question".  This is the exact shape of
    the failure, and it is invisible in a test that only ever uses one unit
    with one set of flags.
    """
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    header = tmp_path / "include" / "heavy.h"
    header.write_text("#pragma once\nstruct Heavy { int x; };\n", encoding="utf-8")
    source = tmp_path / "src" / "app.cpp"
    source.write_text('#include "heavy.h"\nint main() { return 0; }\n', encoding="utf-8")

    tu = TranslationUnit(source=source, dependencies=[header])
    # 单元自己的参数带着真正的 -I；调用方传的共享参数是空的。
    tu.compile_options = DriverOptions(
        standard="c++17", include_dirs=[tmp_path / "include"]
    )
    scan = ProjectScan(
        root=tmp_path,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
        header_stats={header: _header_stats(header, 2, 1)},
    )
    scan.candidates = [
        RemovalCandidate(
            tu=source,
            header=header,
            line=1,
            kind=HeaderKind.PROJECT,
            exclusive_headers=[header],
            estimated_lines_saved=2,
            estimated_percent=100.0,
        )
    ]

    shared = DriverOptions(standard="c++17", include_dirs=[])
    results = verify_candidates(scan, _RecordingDriver(), shared)

    assert results and results[0].status != "compile-error"
    # 每一侧都必须带着单元自己的 -I，而不是调用方那份空的。
    for _path, options in captured:
        assert tmp_path / "include" in options.include_dirs
