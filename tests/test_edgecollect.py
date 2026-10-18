"""边级成本采集层与报告渲染的测试。

真编译那段只在有 clang++ 的机器上跑（CI 的 ubuntu/macos 有；Windows 开发机
可能没有），其余路径用桩驱动覆盖——重要的是**失败要如实报出来**，而不是
悄悄产出一张空表。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from heavyheader.attrib.edgecost import aggregate_edge_costs
from heavyheader.drivers.base import CompilerDriver, DriverOptions, detect_driver
from heavyheader.edgecollect import EdgeCostError, EdgeUnit, collect_edge_units, collect_unit
from heavyheader.models import CompilerFamily
from heavyheader.report.edges import edges_to_dict, render_edges_markdown


class _StubDriver(CompilerDriver):
    """不做真实编译的驱动：build_command 只回放一个必定失败的进程。"""

    def __init__(self, family: CompilerFamily, exit_code: int = 1) -> None:
        self.family = family
        self.exit_code = exit_code

    def build_command(self, source: Path, options: DriverOptions) -> list[str]:
        return [
            "python",
            "-c",
            f"import sys; sys.exit({self.exit_code})",
            "-o",
            str(options.output_path),
        ]

    def interpret(self, source, returncode, stdout, stderr, wall_seconds):  # pragma: no cover
        raise NotImplementedError


def test_collect_rejects_non_clang_toolchains():
    """GCC/MSVC 没有逐文件自耗时，必须明确报错而不是退化成估算。"""
    with pytest.raises(EdgeCostError, match="Clang"):
        collect_edge_units([], _StubDriver(CompilerFamily.GCC))


def test_collect_reports_failed_compile_instead_of_empty_table():
    unit = collect_unit(
        Path("x.cpp"), _StubDriver(CompilerFamily.CLANG), DriverOptions(), Path("."), "u0"
    )
    assert unit.ok is False
    assert unit.note.startswith("编译失败")


def test_render_lists_caveat_before_numbers():
    """口径必须出现在表格之前——摘走这张表的人要先看到它。"""
    unit = EdgeUnit(source=Path("a.cpp"), ok=True, self_seconds=1.0)
    costs = aggregate_edge_costs([[("a.cpp", "big.h", 0.5, 1234)]])
    text = render_edges_markdown(costs, [unit], top=5)
    assert "不是" in text and "verify" in text
    assert text.index("口径") < text.index("| # |")
    assert "big.h" in text and "1,234" in text
    assert "1/1 成功" in text


def test_render_lists_failed_units_explicitly():
    good = EdgeUnit(source=Path("a.cpp"), ok=True, self_seconds=0.2)
    bad = EdgeUnit(source=Path("b.cpp"), ok=False, note="编译失败：boom")
    text = render_edges_markdown(aggregate_edge_costs([[("a.cpp", "h.h", 0.1, 10)]]), [good, bad])
    assert "采集失败" in text and "b.cpp" in text
    payload = edges_to_dict(aggregate_edge_costs([[("a.cpp", "h.h", 0.1, 10)]]), [good, bad])
    assert payload["units"][1]["ok"] is False
    assert payload["units"][1]["note"] == "编译失败：boom"
    assert payload["edges"][0]["marginal_lines"] == 10


CLANG = shutil.which("clang++")


@pytest.mark.skipif(CLANG is None, reason="需要 clang++（-ftime-trace）")
def test_collect_end_to_end_on_tiny_project(tmp_path):
    """真编译一次：a.cpp 包含 b.h，边 a.cpp→b.h 必须出现且行数正确。"""
    (tmp_path / "b.h").write_text("int f();\n" * 5, encoding="utf-8")
    (tmp_path / "a.cpp").write_text('#include "b.h"\nint main(){return f();}\n', encoding="utf-8")

    class _Task:
        source = tmp_path / "a.cpp"
        options = DriverOptions(include_dirs=[tmp_path])
        cwd = tmp_path

    driver = detect_driver(CLANG)
    units = collect_edge_units([_Task()], driver)
    assert len(units) == 1
    assert units[0].ok, units[0].note
    by_edge = {(Path(p).name, Path(c).name): (s, n) for p, c, s, n in units[0].edges}
    assert ("a.cpp", "b.h") in by_edge
    assert by_edge[("a.cpp", "b.h")][1] == 5


@pytest.mark.skipif(CLANG is None, reason="需要 clang++（-ftime-trace）")
def test_cli_edge_cost_writes_report(tmp_path):
    """命令行走一遍：报告落盘且包含免责口径。"""
    (tmp_path / "b.h").write_text("int f();\n" * 3, encoding="utf-8")
    (tmp_path / "a.cpp").write_text('#include "b.h"\nint main(){return 0;}\n', encoding="utf-8")
    out = tmp_path / "report.md"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "heavyheader",
            "edge-cost",
            str(tmp_path),
            "--compiler",
            CLANG,
            "--max-tus",
            "1",
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert completed.returncode == 0, completed.stderr
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert "口径" in text and "b.h" in text
