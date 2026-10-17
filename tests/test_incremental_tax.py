"""增量构建税：改一行头文件，下一次增量构建要等多久。

竞品之间真正没人回答的是「编辑循环的等待」：Header Hero 排全量成本，
ClangBuildAnalyzer 报单次构建耗时，deheader 只判能不能删。没有一个把
「这个头文件改动会导致哪些 TU 重编、合计多少秒」摆出来。

这里守两件事：
1. 税额只累加「传递包含它的 TU」，无关 TU 不得计入（否则虚高，用户会关掉它）；
2. 没有计时的 TU 不能被当成「免费」——0.0 是「未统计」，报表必须能区分。
"""

from pathlib import Path

import pytest

from heavyheader.attrib.graph import HeaderSizes, build_header_stats
from heavyheader.models import (
    CompilerFamily,
    IncludeEvent,
    PhaseTiming,
    ProjectScan,
    TranslationUnit,
)
from heavyheader.report.jsonio import scan_from_dict, scan_to_dict
from heavyheader.report.markdown import render_markdown


def _write(root: Path, name: str, lines: int = 20) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"int sym_{name.replace('/', '_')}_{i}();" for i in range(lines))
    path.write_text(body + "\n", encoding="utf-8")
    return path


def _tu(source: Path, edges: list[tuple[str, str, int]], seconds: float | None) -> TranslationUnit:
    timings = [] if seconds is None else [PhaseTiming(name="TOTAL", wall_seconds=seconds)]
    return TranslationUnit(
        source=source,
        dependencies=[Path(b) for _, b, _ in edges],
        include_events=[
            IncludeEvent(includer=Path(a), included=Path(b), depth=d)
            for a, b, d in edges
        ],
        timings=timings,
    )


def _scan(root: Path, tus: list[TranslationUnit]) -> ProjectScan:
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=tus,
        baseline_seconds=sum(tu.total_wall_seconds() for tu in tus),
    )
    build_header_stats(scan, HeaderSizes())
    return scan


def _stat(scan: ProjectScan, name: str):
    """header_stats 的 key 是归一化后的绝对路径，测试按文件名取。"""
    return next(s for p, s in scan.header_stats.items() if p.name == name)


def test_tax_sums_only_transitively_dependent_units(tmp_path: Path):
    _write(tmp_path, "core.h")
    _write(tmp_path, "mid.h")
    a = tmp_path / "a.cpp"
    b = tmp_path / "b.cpp"

    # a.cpp 花 2.0s，包含 core.h -> mid.h；b.cpp 花 0.5s，只包含 core.h。
    # 税额必须只来自真的会重编的 TU：core=2.5，mid=2.0（b.cpp 不重编 mid）。
    scan = _scan(
        tmp_path,
        [
            _tu(a, [("a.cpp", "core.h", 1), ("core.h", "mid.h", 2)], 2.0),
            _tu(b, [("b.cpp", "core.h", 1)], 0.5),
        ],
    )

    assert _stat(scan, "core.h").incremental_seconds == pytest.approx(2.5)
    assert _stat(scan, "mid.h").incremental_seconds == pytest.approx(2.0)


def test_unit_without_timing_contributes_nothing_not_zero_seconds(tmp_path: Path):
    """没有 TOTAL 行的 TU 不能算成 0 秒后当成「改它不要钱」。"""
    _write(tmp_path, "core.h")
    timed = _write(tmp_path, "timed.cpp")
    untimed = _write(tmp_path, "untimed.cpp")

    scan = _scan(
        tmp_path,
        [
            _tu(timed, [("timed.cpp", "core.h", 1)], 1.25),
            _tu(untimed, [("untimed.cpp", "core.h", 1)], None),
        ],
    )
    # 只累加有计时的那个；税额是 1.25 而不是 1.25 / 2 或 0。
    assert _stat(scan, "core.h").incremental_seconds == pytest.approx(1.25)


def test_report_has_incremental_tax_section(tmp_path: Path):
    _write(tmp_path, "core.h")
    a = tmp_path / "a.cpp"
    scan = _scan(tmp_path, [_tu(a, [("a.cpp", "core.h", 1)], 1.5)])

    text = render_markdown(scan)
    assert "增量构建税" in text
    # 未校准就必须在标题上写明，不能让它看起来像结论。
    assert "未校准" in text
    assert "勿据此决策" in text
    assert "`core.h`" in text
    # 预测口径必须写清楚，否则读者会把它当成实测结论。
    assert "不含链接" in text


def test_json_roundtrip_keeps_tax(tmp_path: Path):
    _write(tmp_path, "core.h")
    a = tmp_path / "a.cpp"
    scan = _scan(tmp_path, [_tu(a, [("a.cpp", "core.h", 1)], 3.0)])

    data = scan_to_dict(scan)
    entry = next(h for h in data["headers"] if h["path"].endswith("core.h"))
    assert entry["incremental_seconds"] == pytest.approx(3.0)

    again = scan_from_dict(data)
    assert _stat(again, "core.h").incremental_seconds == pytest.approx(3.0)
