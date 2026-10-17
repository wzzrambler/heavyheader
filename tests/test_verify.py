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


# --------------------------------------------------------------------------
# UTF-8 BOM: the first include of a Windows-edited file
# --------------------------------------------------------------------------

_BOM_SOURCE = "\ufeff" + '#include "heavy.h"\nint main() { return 0; }\n'


def test_include_on_the_first_line_of_a_bom_file_is_found():
    """BOM 不是 \\s：``^\\s*#\\s*include`` 匹配不到带 BOM 的首行，
    于是 Windows 编辑器保存的源码里第一个 include 永远「找不到该注释哪一行」——
    结论从「建议删除」悄悄降级成「需人工判断」，而文件明明就在那儿。"""
    from heavyheader.verify import _comment_out, find_include_lines

    header = Path("/proj/include/heavy.h")
    assert find_include_lines(_BOM_SOURCE, header) == [1]

    patched = _comment_out(_BOM_SOURCE, 1)
    # BOM 必须留在文件最前面，注释标记插在它后面。
    assert patched.startswith("\ufeff// [heavyheader] #include")
    assert patched.count("#include") == 1  # 行数没变，只是被注释掉


def test_hint_line_also_survives_a_bom():
    """编译器上报的行号同样要过 BOM 这一关——否则带行号的工具链
    （未来的 /showIncludes 扩展）会在首行上静默失效。"""
    from heavyheader.verify import locate_include_line

    location = locate_include_line(_BOM_SOURCE, Path("/proj/include/heavy.h"), 1)

    assert location.line == 1
    assert location.status == ""


def test_verify_on_a_bom_project_does_not_report_not_found(tmp_path, captured):
    """端到端：带 BOM 的工程不能被报成「定位不到」。"""
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    header = tmp_path / "include" / "heavy.h"
    header.write_text("#pragma once\nstruct Heavy { int x; };\n", encoding="utf-8")
    source = tmp_path / "src" / "app.cpp"
    source.write_text(
        "\ufeff" + '#include "heavy.h"\nint main() { return 0; }\n', encoding="utf-8"
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

    results = verify_candidates(scan, _RecordingDriver(), DriverOptions(standard="c++17"))

    assert results[0].status != "not-found"


# --------------------------------------------------------------------------
# the test-runner evidence grade
# --------------------------------------------------------------------------


def test_verify_runs_baseline_tests_before_trusting_a_patched_one(tmp_path, captured):
    """先证伪自己再取证：基线版测试先跑，基线就不过记 error，
    不冤枉候选；基线过了才跑补丁版。"""
    from heavyheader.verify import VerifyOptions

    scan, options, source = _project(tmp_path)
    calls: list[str] = []

    def runner(path):
        calls.append(Path(path).name)
        return True, "ok"

    # 让基线 520ms、补丁 320ms，确保候选 improved 才会进测试阶段。
    def _fake_measure(driver, source_path, opts, _extra, runs, timeout):
        # 补丁副本与源文件同名（都叫 app.cpp），只能按完整路径区分两侧。
        baseline = str(source_path) == str(source)
        return (0.520 if baseline else 0.320), None, None, ()

    captured_monkey = None
    import heavyheader.verify as v

    original = v._measure
    v._measure = _fake_measure
    try:
        results = verify_candidates(
            scan,
            _RecordingDriver(),
            options,
            verify=VerifyOptions(test_runner=runner),
        )
    finally:
        v._measure = original

    assert results[0].status == "improved"
    assert results[0].test_status == "passed"
    # 基线（app.cpp 原文件）必须先于补丁（临时目录副本）被测试。
    assert calls[0] == source.name
    assert len(calls) == 2


def test_failing_patched_tests_demote_the_result(tmp_path):
    """补丁版测试失败：结果记 failed，advice 会把结论降级。"""
    from heavyheader.attrib.advice import build_advice
    from heavyheader.verify import VerifyOptions

    scan, options, source = _project(tmp_path)

    def runner(path):
        # 基线过、补丁挂——只有补丁失败才能走到「结论降级」这条路。
        # （基线也失败的话协议记 error：不冤枉候选，但也不取证。）
        ok = str(path) == str(source)
        return ok, "assertion mismatch" if not ok else "ok"

    import heavyheader.verify as v

    original = v._measure
    v._measure = lambda d, s, o, _e, r, t: (
        (0.520 if str(s) == str(source) else 0.320),
        None,
        None,
        (),
    )
    try:
        results = verify_candidates(
            scan,
            _RecordingDriver(),
            options,
            verify=VerifyOptions(test_runner=runner),
        )
    finally:
        v._measure = original

    assert results[0].test_status == "failed"
    advice = build_advice(results[0].candidate, results[0], scan)
    assert advice.verdict is not None and advice.verdict.value == "human"


def test_semantic_verdict_is_enriched_onto_candidate(tmp_path, monkeypatch):
    """开启 --semantic 时，每个候选应拿到 AST 交叉检查 verdict，并随候选走。"""
    from heavyheader.attrib.semantic import SemanticVerdict

    scan, options, source = _project(tmp_path)

    def _fake_measure(driver, source_path, opts, _extra, runs, timeout):
        baseline = str(source_path) == str(source)
        return (0.520 if baseline else 0.320), None, None, ()

    monkeypatch.setattr("heavyheader.verify._measure", _fake_measure)

    class _StubBackend:
        available = True

        def analyse(self, unit, build_path):
            return SemanticVerdict(unit.stem, frozenset(), True)

    results = verify_candidates(
        scan,
        _RecordingDriver(),
        options,
        semantic=_StubBackend(),
        build_path=tmp_path,
    )
    assert results[0].candidate.semantic is not None
    assert results[0].candidate.semantic.unit == source.stem


def test_semantic_is_skipped_when_build_path_missing(tmp_path, monkeypatch):
    """语义层可用但没给 build_path：静默跳过，不崩、不 enrich。"""
    from heavyheader.attrib.semantic import SemanticVerdict

    scan, options, source = _project(tmp_path)

    def _fake_measure(driver, source_path, opts, _extra, runs, timeout):
        baseline = str(source_path) == str(source)
        return (0.520 if baseline else 0.320), None, None, ()

    monkeypatch.setattr("heavyheader.verify._measure", _fake_measure)

    calls = []

    class _StubBackend:
        available = True

        def analyse(self, unit, build_path):
            calls.append(unit)
            return SemanticVerdict(unit.stem, frozenset(), True)

    results = verify_candidates(
        scan,
        _RecordingDriver(),
        options,
        semantic=_StubBackend(),
        build_path=None,  # 故意不给
    )
    assert not calls  # analyse 根本不该被调用
    assert results[0].candidate.semantic is None
