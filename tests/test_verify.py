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
from heavyheader.verify import verify_candidates


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
        return 0.010, None

    monkeypatch.setattr("heavyheader.verify._measure", _fake_measure)
    return seen


def test_baseline_inherits_the_scan_layout_fallback(tmp_path, captured):
    """Failure mode 1: an include/ layout must resolve without passing -I."""
    scan, options, source = _project(tmp_path)

    results = verify_candidates(scan, _RecordingDriver(), options)

    assert results, "expected the candidate to be exercised"
    baseline_path, baseline_options = captured[0]
    assert baseline_path == str(source)
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

    # First call is the baseline; the second is the patched recompile.
    assert len(captured) == 2
    _, patched_options = captured[1]
    assert tmp_path / "include" in patched_options.include_dirs
    assert tmp_path / "third_party" in patched_options.include_dirs
    # 引用的 include 相对于包含它的文件目录解析，因此原目录必须补上。
    assert source.parent in patched_options.include_dirs


def test_both_sides_are_compiled_with_identical_include_dirs(tmp_path, captured):
    """The comparison is only meaningful if both sides see the same world."""
    scan, options, source = _project(tmp_path)
    options = DriverOptions(standard="c++17", include_dirs=[tmp_path / "include"])

    verify_candidates(scan, _RecordingDriver(), options)

    _, baseline_options = captured[0]
    _, patched_options = captured[1]
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
