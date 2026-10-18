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


def test_candidate_rank_heuristic_survives_json_round_trip():
    """排序分层必须跟着 JSON 一起存，否则重载后排序整体退化。

    真实影响：``usage_label`` 是给人看的中文，``usage_verdict`` 才是排序键。
    只序列化 label 时，``scan_from_dict`` 会把全部候选恢复成 ``unknown``，
    于是「明确在用」的 150k 行伞头文件和「疑似未使用」的 90 行系统头落在
    同一层，按估值排——推荐列表的第一条变成删不掉的那个。
    """
    from heavyheader.models import (
        HeaderKind,
        ProjectScan,
        RemovalCandidate,
        candidate_rank_key,
    )
    from heavyheader.report.jsonio import scan_from_dict, scan_to_dict

    used = RemovalCandidate(
        tu=Path("a.cpp"),
        header=Path("big.hpp"),
        line=1,
        kind=HeaderKind.PROJECT,
        estimated_lines_saved=150_000,
        usage_label="正在使用",
        usage_verdict="in_use",
    )
    maybe = RemovalCandidate(
        tu=Path("b.cpp"),
        header=Path("tiny.hpp"),
        line=1,
        kind=HeaderKind.SYSTEM,
        estimated_lines_saved=90,
        usage_label="疑似未使用",
        usage_verdict="plausibly_unused",
    )
    scan = ProjectScan(
        root=Path("."), compiler="g++", compiler_family=CompilerFamily.GCC,
        standard="c++17", candidates=[used, maybe],
    )

    again = scan_from_dict(scan_to_dict(scan))
    by_header = {c.header.name: c for c in again.candidates}
    assert by_header["big.hpp"].usage_verdict == "in_use"
    assert by_header["tiny.hpp"].usage_verdict == "plausibly_unused"
    # 排序结论必须和原始扫描一致：疑似未使用排在明确在用之前。
    ordered = sorted(again.candidates, key=candidate_rank_key)
    assert [c.header.name for c in ordered] == ["tiny.hpp", "big.hpp"]


def test_header_site_survives_json_round_trip():
    """「改哪个文件」和「要重编哪些单元」必须跟着 JSON 走。

    丢了这两个字段，重载出来的头文件级候选会静默变成 TU 级——``site`` 回落
    到代表单元，``verify`` 因此去注释**编译单元源码**里的一行，而不是那个
    头文件里的一行。改错了文件，测出来的秒数还看着挺合理，这是最难发现的
    一类错误。
    """
    from heavyheader.models import HeaderKind, ProjectScan, RemovalCandidate
    from heavyheader.report.jsonio import scan_from_dict, scan_to_dict

    site = RemovalCandidate(
        tu=Path("src/representative.cpp"),
        header=Path("unneeded.hpp"),
        line=7,
        kind=HeaderKind.SYSTEM,
        estimated_lines_saved=288,
        usage_verdict="plausibly_unused",
        site_file=Path("include/shared.h"),
        affected_tus=[Path("src/a.cpp"), Path("src/b.cpp"), Path("src/c.cpp")],
    )
    plain = RemovalCandidate(
        tu=Path("src/other.cpp"),
        header=Path("unneeded.hpp"),
        line=3,
        kind=HeaderKind.SYSTEM,
        estimated_lines_saved=288,
    )
    scan = ProjectScan(
        root=Path("."), compiler="g++", compiler_family=CompilerFamily.GCC,
        standard="c++17", candidates=[site, plain],
    )

    again = scan_from_dict(scan_to_dict(scan))
    reloaded = again.candidates[0]
    assert reloaded.site == Path("include/shared.h")
    assert reloaded.is_header_site is True
    assert reloaded.reach == 3
    assert sorted(reloaded.affected_tus) == [
        Path("src/a.cpp"), Path("src/b.cpp"), Path("src/c.cpp")
    ]
    # TU 级候选不受影响：不能因为加了字段就把它们变成「头文件级」。
    assert again.candidates[1].reach == 1
    assert again.candidates[1].is_header_site is False


def test_legacy_json_without_verdict_is_ranked_by_label():
    """旧基线文件没有 verdict 字段；反推 label 而不是一律当成 unknown。"""
    from heavyheader.models import candidate_rank_key
    from heavyheader.report.jsonio import scan_from_dict

    data = {
        "schema": 1,
        "root": ".", "compiler": "g++", "compiler_family": "gcc", "standard": "c++17",
        "translation_units": [], "headers": [], "warnings": [],
        "candidates": [
            {"tu": "a.cpp", "header": "big.hpp", "kind": "project",
             "estimated_lines_saved": 150000, "usage_label": "正在使用"},
            {"tu": "b.cpp", "header": "tiny.hpp", "kind": "system",
             "estimated_lines_saved": 90, "usage_label": "疑似未使用"},
        ],
    }
    scan = scan_from_dict(data)
    ordered = sorted(scan.candidates, key=candidate_rank_key)
    assert [c.header.name for c in ordered] == ["tiny.hpp", "big.hpp"]
