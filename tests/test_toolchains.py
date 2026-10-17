"""多编译器对比的单元测试。

这里刻意**不依赖真实编译器**：跨工具链逻辑的核心是「怎么折叠、怎么比、怎么
渲染」，用一个能构造任意 header_stats 的假数据就能测透。真实编译器的可用性
由 ``scan_toolchains`` 的降级测试覆盖（缺一个编译器不能让整张矩阵失败）。

四条要守住的判据：

1. 一个编译单元都没编译成功 ≠「成本为 0、快得离谱」，必须标成失败；
2. 逐头文件差异**只统计项目头**（系统头路径跨工具链不同，按路径比是假差异）；
3. 数值一致的项目头不进差异表（列出来只会淹没真正的分歧）；
4. 没有可信耗时时不硬评「谁最快」（缺失 TOTAL 行就如实说没有）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.cli import build_parser
from heavyheader.drivers.base import CompilationError, CompilerDriver
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    ProjectScan,
    TranslationUnit,
)
from heavyheader.scanner import ScanOptions
from heavyheader.toolchains import (
    _Outcome,
    build_costs,
    compare_toolchains,
    render_toolchains,
    scan_toolchains,
    to_dict,
)


def _header(path: str, lines: int, kind: HeaderKind) -> HeaderStats:
    return HeaderStats(
        path=Path(path), kind=kind, line_count=lines, attributed_lines=lines
    )


def _scan(
    *,
    project: dict[str, int] | None = None,
    system: dict[str, int] | None = None,
    seconds: float = 1.0,
) -> ProjectScan:
    """A scan with one successful TU and a hand-built header table."""
    stats: dict[Path, HeaderStats] = {}
    for path, lines in (project or {}).items():
        stats[Path(path)] = _header(path, lines, HeaderKind.PROJECT)
    for path, lines in (system or {}).items():
        stats[Path(path)] = _header(path, lines, HeaderKind.SYSTEM)
    return ProjectScan(
        root=Path("/proj"),
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard=None,
        tus=[TranslationUnit(source=Path("a.cpp"))],
        header_stats=stats,
        baseline_seconds=seconds,
    )


def _failed_scan() -> ProjectScan:
    return ProjectScan(
        root=Path("/proj"),
        compiler="cl.exe",
        compiler_family=CompilerFamily.MSVC,
        standard=None,
        tus=[
            TranslationUnit(
                source=Path("a.cpp"), error="compiler 'cl.exe' not found on PATH"
            )
        ],
    )


# --------------------------------------------------------------------------
# build_costs
# --------------------------------------------------------------------------


def test_ok_scan_yields_consistent_totals():
    scan = _scan(
        project={"include/heavy.h": 1000},
        system={"/usr/include/c++/13/vector": 500},
        seconds=2.5,
    )

    cost = build_costs([_Outcome("g++", "gcc", scan, None)])[0]

    assert cost.ok
    assert cost.tu_count == 1
    assert cost.attributed_lines == 1500
    assert cost.project_attributed_lines == 1000
    assert cost.non_project_attributed_lines == 500
    assert cost.seconds == 2.5


def test_scan_with_zero_successful_tus_is_a_failure_not_a_zero_cost():
    """没编译成功的编译器绝不能显示成「成本 0」——那会读成「快得离谱」。"""
    cost = build_costs([_Outcome("cl.exe", "msvc", _failed_scan(), None)])[0]

    assert not cost.ok
    assert "not found" in cost.error
    assert cost.attributed_lines == 0


def test_driver_that_never_ran_is_recorded_with_its_error():
    cost = build_costs(
        [_Outcome("clang++", "unknown", None, "cannot probe compiler")]
    )[0]

    assert not cost.ok
    assert cost.error == "cannot probe compiler"


# --------------------------------------------------------------------------
# compare_toolchains: header spreads
# --------------------------------------------------------------------------


def test_header_spread_only_reports_project_headers_that_diverge():
    gcc = _scan(
        project={"include/h.h": 100, "include/same.h": 50},
        system={"/usr/include/c++/13/vector": 900},
        seconds=2.0,
    )
    clang = _scan(
        project={"include/h.h": 120, "include/same.h": 50},
        system={"/usr/lib/clang/17/vector": 1100},
        seconds=1.5,
    )

    costs = build_costs(
        [_Outcome("g++", "gcc", gcc, None), _Outcome("clang++", "clang", clang, None)]
    )
    comparison = compare_toolchains(Path("/proj"), costs)

    # 只有 h.h 有分歧；same.h 数值一致、系统头路径不同，都不该出现。
    assert [s.path for s in comparison.header_spreads] == [Path("include/h.h")]
    spread = comparison.header_spreads[0]
    assert spread.lines == {"g++": 100, "clang++": 120}
    assert spread.spread == 20
    assert spread.spread_percent == pytest.approx(20 / 120 * 100)


def test_spread_survives_a_compiler_that_never_reaches_the_header():
    """某编译器压根没包含该头（0 行）时，差异百分比不能除零。"""
    gcc = _scan(project={"include/opt.h": 100})
    clang = _scan(project={})
    costs = build_costs(
        [_Outcome("g++", "gcc", gcc, None), _Outcome("clang++", "clang", clang, None)]
    )

    comparison = compare_toolchains(Path("/proj"), costs)

    assert len(comparison.header_spreads) == 1
    assert comparison.header_spreads[0].lines["clang++"] == 0
    assert comparison.header_spreads[0].spread_percent == 100.0


def test_identical_project_headers_produce_no_spread_rows():
    gcc = _scan(project={"include/h.h": 100})
    clang = _scan(project={"include/h.h": 100})
    costs = build_costs(
        [_Outcome("g++", "gcc", gcc, None), _Outcome("clang++", "clang", clang, None)]
    )

    assert compare_toolchains(Path("/proj"), costs).header_spreads == []


# --------------------------------------------------------------------------
# fastest / comparability
# --------------------------------------------------------------------------


def test_fastest_ignores_a_compiler_without_a_trustworthy_timing():
    slow = _scan(seconds=3.0)
    no_timing = _scan(seconds=0.0)  # 编译器没报 TOTAL 行
    costs = build_costs(
        [_Outcome("g++", "gcc", slow, None), _Outcome("clang++", "clang", no_timing, None)]
    )

    comparison = compare_toolchains(Path("/proj"), costs)

    assert comparison.comparable
    assert comparison.fastest().compiler == "g++"


def test_fastest_is_none_when_no_timing_is_trustworthy():
    costs = build_costs(
        [
            _Outcome("g++", "gcc", _scan(seconds=0.0), None),
            _Outcome("clang++", "clang", _scan(seconds=0.0), None),
        ]
    )

    assert compare_toolchains(Path("/proj"), costs).fastest() is None


def test_single_successful_compiler_is_not_a_comparison():
    costs = build_costs(
        [_Outcome("g++", "gcc", _scan(), None), _Outcome("cl.exe", "msvc", _failed_scan(), None)]
    )

    comparison = compare_toolchains(Path("/proj"), costs)

    assert not comparison.comparable
    assert any("无法做横向对比" in w for w in comparison.warnings)


# --------------------------------------------------------------------------
# scan_toolchains: graceful degradation with real orchestration
# --------------------------------------------------------------------------


class _StubDriver(CompilerDriver):
    family = CompilerFamily.GCC

    def __init__(self) -> None:
        self.executable = "stubcc"

    def run(self, source, options, cwd=None, timeout=300.0) -> TranslationUnit:
        return TranslationUnit(source=Path(source))

    def build_command(self, source, options) -> list[str]:  # pragma: no cover
        return ["stubcc", str(source)]

    def interpret(self, source, returncode, stdout, stderr, wall_seconds):
        return TranslationUnit(source=Path(source))


def _project(tmp_path: Path) -> Path:
    (tmp_path / "a.cpp").write_text("int a() { return 1; }\n", encoding="utf-8")
    return tmp_path


def _options(tmp_path: Path):
    return lambda compiler: ScanOptions(root=tmp_path, compiler=compiler, jobs=1)


def test_missing_compiler_does_not_abort_the_matrix(tmp_path):
    _project(tmp_path)

    def factory(name: str) -> CompilerDriver:
        if name == "badcc":
            raise CompilationError("compiler 'badcc' not found on PATH")
        return _StubDriver()

    comparison = scan_toolchains(
        tmp_path,
        ["badcc", "goodcc"],
        options_factory=_options(tmp_path),
        driver_factory=factory,
    )

    assert [c.compiler for c in comparison.costs] == ["badcc", "goodcc"]
    assert [c.ok for c in comparison.costs] == [False, True]
    assert "not found" in comparison.costs[0].error
    assert any("badcc" in w for w in comparison.warnings)


def test_two_working_compilers_form_a_comparison(tmp_path):
    _project(tmp_path)

    comparison = scan_toolchains(
        tmp_path,
        ["good1", "good2"],
        options_factory=_options(tmp_path),
        driver_factory=lambda name: _StubDriver(),
    )

    assert comparison.comparable
    assert len(comparison.ok_costs) == 2


# --------------------------------------------------------------------------
# rendering / json
# --------------------------------------------------------------------------


def test_render_marks_a_failed_compiler_and_keeps_the_rest():
    costs = build_costs(
        [
            _Outcome("g++", "gcc", _scan(seconds=2.0), None),
            _Outcome("cl.exe", "msvc", _failed_scan(), None),
        ]
    )
    text = render_toolchains(compare_toolchains(Path("/proj"), costs))

    assert "`g++`" in text
    assert "`cl.exe`" in text
    assert "失败" in text
    assert "耗时" in text
    # 口径说明必须在，避免读者把本机耗时当绝对结论。
    assert "耗时只作参考" in text


def test_render_includes_the_header_spread_table():
    gcc = _scan(project={"include/h.h": 100}, seconds=2.0)
    clang = _scan(project={"include/h.h": 130}, seconds=1.0)
    costs = build_costs(
        [_Outcome("g++", "gcc", gcc, None), _Outcome("clang++", "clang", clang, None)]
    )

    text = render_toolchains(compare_toolchains(Path("/proj"), costs))

    assert "include/h.h" in text
    assert "| 30 |" in text  # 差值列
    assert "23.1%" in text  # 30 / 130
    assert "最快" in text


def test_to_dict_is_stable_and_complete():
    costs = build_costs(
        [
            _Outcome("g++", "gcc", _scan(project={"include/h.h": 100}), None),
            _Outcome("cl.exe", "msvc", _failed_scan(), None),
        ]
    )
    payload = to_dict(compare_toolchains(Path("/proj"), costs))

    assert payload["compilers"] == ["g++", "cl.exe"]
    assert payload["rows"][0]["ok"] is True
    assert payload["rows"][1]["ok"] is False
    assert "header_spreads" in payload
    assert "warnings" in payload


def test_cli_parser_accepts_toolchains():
    args = build_parser().parse_args(
        ["toolchains", ".", "--compilers", "g++,clang++", "--format", "json"]
    )

    assert args.command == "toolchains"
    assert args.compilers == "g++,clang++"
    assert args.format == "json"
    # 该子命令没有单数 --compiler，避免两种写法混淆。
    assert not hasattr(args, "compiler")
