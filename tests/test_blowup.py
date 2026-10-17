"""Blowup Factor：补上 Header Hero 早就有的指标，并守住「缺分母不许装正常」。

这些测试存在的理由不是覆盖率，而是防止两类回归：

1. 公式被悄悄改掉。它和老工具的公式一字不差，是我们在文档里主动声明
   「同源」的依据；改掉它就等于把声明的依据抽走。
2. 分母缺失时渲染成 0.0。``0.0`` 在报表里读起来像「没有放大」，而真相是
   「没算」。旧 JSON 就属于这种情况，报表必须说「未统计」。
"""

from pathlib import Path

import pytest

from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    ProjectScan,
)
from heavyheader.report.jsonio import scan_from_dict, scan_to_dict
from heavyheader.report.markdown import render_markdown


def _scan(pairs: list[tuple[str, int, int]], total_lines: int) -> ProjectScan:
    """Build a scan whose only content is header cost, so the maths is exact."""
    stats = {}
    for name, lines, reach in pairs:
        path = Path(name)
        stats[path] = HeaderStats(
            path=path,
            kind=HeaderKind.PROJECT,
            line_count=lines,
            tu_reach=reach,
            attributed_lines=lines * reach,
        )
    return ProjectScan(
        root=Path("."),
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        header_stats=stats,
        total_project_lines=total_lines,
    )


def test_blowup_factor_is_total_parsed_over_unique_lines():
    scan = _scan([("a.h", 100, 3), ("b.h", 50, 2)], total_lines=300)
    assert scan.total_included_lines() == 400
    # 400 / 300 —— 与 Header Hero 的 Total Parsed / Total Lines 同式。
    assert scan.blowup_factor() == pytest.approx(400 / 300)


def test_blowup_factor_is_zero_when_denominator_missing():
    scan = _scan([("a.h", 100, 3)], total_lines=0)
    # 不能抛除零异常，也不能返回 inf；0.0 是「没算」，由报表负责说明。
    assert scan.blowup_factor() == 0.0


def test_report_flags_missing_denominator_instead_of_showing_zero():
    scan = _scan([("a.h", 100, 3)], total_lines=0)
    text = render_markdown(scan)
    assert "未统计" in text
    assert "不代表没有放大" in text
    # 绝不能出现 "Blowup Factor：0.0×" —— 那会被读成健康指标。
    assert "Blowup Factor：0.0×" not in text


def test_report_prints_measured_blowup_factor():
    scan = _scan([("a.h", 100, 3)], total_lines=100)
    text = render_markdown(scan)
    assert "Blowup Factor：3.0×" in text


def test_json_roundtrip_keeps_denominator_and_exports_factor():
    scan = _scan([("a.h", 100, 3)], total_lines=200)
    data = scan_to_dict(scan)
    assert data["totals"]["total_project_lines"] == 200
    assert data["totals"]["blowup_factor"] == pytest.approx(1.5)

    again = scan_from_dict(data)
    assert again.total_project_lines == 200
    assert again.blowup_factor() == pytest.approx(1.5)


def test_old_json_without_denominator_degrades_to_undeclared():
    """旧版 JSON 没有该字段：读回来必须是「未统计」，不能凭空补一个数。"""
    scan = _scan([("a.h", 100, 3)], total_lines=200)
    data = scan_to_dict(scan)
    data["totals"].pop("total_project_lines")

    again = scan_from_dict(data)
    assert again.total_project_lines == 0
    assert again.blowup_factor() == 0.0
