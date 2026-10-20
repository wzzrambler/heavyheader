"""证据分层模块的测试（2026-10-20）。

这一层不是装饰：供给侧漏报的实测显示，**落地收益最多的那几条候选恰好被文本
判据判成 ``in_use`` / ``unknown``**，旧界面按判据排序，于是它们从不出现。
下面这些用例锁的是三件事：

1. 档位只认「闸门真跑过且指纹一致」——没验过不许说「程序没变」；
2. 分层汇总必须**连没取得证据的那一档一起报**，否则「没验过」看起来像「没问题」；
3. ``outside_recommendation`` 是这一层的全部增量——它是唯一能证明「已验证层级
   不是把推荐集换个顺序重排一遍」的数字。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader import evidence  # noqa: E402
from heavyheader.attrib.advice import (  # noqa: E402
    TIER_COMPILES,
    TIER_EQUIVALENT,
    TIER_VERIFIED,
)
from heavyheader.models import HeaderKind, RemovalCandidate  # noqa: E402
from heavyheader.verify import VerificationResult  # noqa: E402


def _candidate(tmp_path: Path, name: str = "heavy.h") -> RemovalCandidate:
    header = tmp_path / "include" / name
    source = tmp_path / "src" / "app.cpp"
    return RemovalCandidate(
        tu=source,
        header=header,
        line=1,
        kind=HeaderKind.PROJECT,
        exclusive_headers=[header],
        estimated_lines_saved=2,
        estimated_percent=100.0,
    )


def _result(candidate: RemovalCandidate, status: str, **kwargs) -> VerificationResult:
    return VerificationResult(candidate=candidate, status=status, **kwargs)


def _gated(
    candidate: RemovalCandidate,
    status: str = "improved",
    *,
    verdict: str = "in_use",
    seconds: tuple[float, float] = (1.0, 0.9),
    confirm: int = 2,
    identical: bool = True,
) -> VerificationResult:
    """一条「过了汇编闸门」的结论。默认判据是 in_use——那正是实测里
    收益最大那批的处境（保守判据不敢推荐，但编译器说删得掉）。"""
    candidate.usage_verdict = verdict
    return _result(
        candidate,
        status,
        baseline_seconds=seconds[0],
        patched_seconds=seconds[1],
        codegen_checked=True,
        codegen_identical=identical,
        confirm_passes=confirm,
    )


# ---------------------------------------------------------------------------
# 档位判定
# ---------------------------------------------------------------------------


def test_tier_never_outruns_the_gate(tmp_path):
    """没跑闸门 → 最高只能到 compiles_only。

    这是整个分层的底线：省下两次编译可以，但不能把省下的时间换成一句没量过的
    「程序没变」。实测反例是删掉 <iomanip> 后目标文件 21,289 → 10,183 字节。
    """
    candidate = _candidate(tmp_path)
    result = _result(
        candidate,
        "improved",
        baseline_seconds=1.0,
        patched_seconds=0.8,
        codegen_checked=False,
    )
    assert evidence.tier_of(result) == TIER_COMPILES
    # 这一档的说明本身就必须是否定式：「不能说程序没变」，而不是「程序没变」。
    assert "不能说" in evidence.describe(TIER_COMPILES)


def test_gate_error_is_not_evidence_of_change(tmp_path):
    """拿不到指纹 ≠ 代码变了。任一侧 !ok 时结论是 error，不进任何档。"""
    candidate = _candidate(tmp_path)
    result = _result(
        candidate,
        "error",
        codegen_checked=True,
        codegen_identical=None,
    )
    assert evidence.tier_of(result) == ""
    assert evidence.summarise([result])["gate_blocked"] == 0


# ---------------------------------------------------------------------------
# 汇总口径
# ---------------------------------------------------------------------------


def test_summary_counts_the_no_evidence_tier_too(tmp_path):
    """分档计数必须包含「无证据」那一档。

    只报成功三档会让读者以为剩下的也都验过了——这正是「没验过」伪装成
    「没问题」的路径。
    """
    candidate = _candidate(tmp_path)
    results = [
        _gated(candidate, "improved", verdict="in_use"),
        _gated(candidate, "no-change", verdict="plausibly_unused", seconds=(1.0, 1.0)),
        _result(candidate, "compile-error"),
    ]
    counts = evidence.tier_counts(results)
    assert counts[TIER_VERIFIED] == 1
    assert counts[TIER_EQUIVALENT] == 1
    assert counts[""] == 1


def test_summary_reports_gate_work_both_ways(tmp_path):
    """放行数与拦下数一起报：只放行不拦截的闸门等于没有闸门。"""
    ok = _candidate(tmp_path, "ok.h")
    bad = _candidate(tmp_path, "bad.h")
    results = [
        _gated(ok, "improved"),
        _gated(bad, "codegen-changed", identical=False),
    ]
    summary = evidence.summarise(results)
    assert summary["gate_ran"] == 2
    assert summary["gate_blocked"] == 1


# ---------------------------------------------------------------------------
# 已验证层级的增量
# ---------------------------------------------------------------------------


def test_outside_recommendation_is_the_whole_point(tmp_path):
    """判据没推荐、却被闸门抬上来的那批——全部增量就在这里。

    如果这一批为空，「已验证层级」就只是把推荐集换个顺序重排一遍。
    """
    a = _candidate(tmp_path, "stdio.h")
    b = _candidate(tmp_path, "unused.h")
    results = [
        _gated(a, "improved", verdict="in_use", seconds=(1.0, 0.9)),
        _gated(b, "improved", verdict="plausibly_unused", seconds=(1.0, 0.99)),
    ]
    outside = evidence.outside_recommendation(results)
    assert [r.candidate.header.name for r in outside] == ["stdio.h"]
    assert evidence.summarise(results)["outside_recommendation"] == 1


def test_equivalent_alone_is_not_a_recommendation(tmp_path):
    """``equivalent`` 只证明「程序没变」，不证明「值得删」。

    它被排除在默认已验证清单之外的原因就在这里——实测里这一档 11 条全是
    ``lean=keep``（系统头被多 TU 共用）。
    """
    candidate = _candidate(tmp_path)
    result = _gated(candidate, "no-change", seconds=(1.0, 1.0))
    assert evidence.tier_of(result) == TIER_EQUIVALENT
    assert result not in evidence.select([result], TIER_VERIFIED)


@pytest.mark.parametrize(
    ("tier", "expected"),
    [("verified", 1), ("equivalent", 1), ("all", 2), ("", 2)],
)
def test_select_filters_by_tier(tmp_path, tier, expected):
    """筛选与展示用同一个判据（都现算 tier_of），否则看到的和筛出来的不是一批。"""
    results = [
        _gated(_candidate(tmp_path, "a.h"), "improved"),
        _gated(_candidate(tmp_path, "b.h"), "no-change", seconds=(1.0, 1.0)),
    ]
    assert len(evidence.select(results, tier)) == expected


def test_sort_puts_strongest_evidence_first(tmp_path):
    """排序按证据强度，不是按文本判据——这一节存在的理由就是重排。"""
    weak = _gated(_candidate(tmp_path, "weak.h"), "improved", seconds=(1.0, 0.99))
    strong = _gated(_candidate(tmp_path, "strong.h"), "improved", seconds=(1.0, 0.5))
    ordered = evidence.sort_by_evidence([weak, strong])
    assert [r.candidate.header.name for r in ordered] == ["strong.h", "weak.h"]


def test_cross_tab_keeps_verdict_and_tier_together(tmp_path):
    """交叉表保留两侧信息，因为「判据×证据」这一格才是讲故事的地方。"""
    results = [
        _gated(_candidate(tmp_path, "a.h"), "improved", verdict="in_use"),
        _gated(_candidate(tmp_path, "b.h"), "improved", verdict="in_use"),
    ]
    assert evidence.cross_tab(results) == {"in_use|verified": 2}


# ---------------------------------------------------------------------------
# 测量顺序：这一层按收益排，不按判据排
# ---------------------------------------------------------------------------


def test_estimate_order_ignores_the_verdict(tmp_path):
    """已验证层级的排序不采信文本判据。

    实测（Lua）8 条落地收益里 7 条被判 ``in_use`` / ``unknown``；按判据排序时
    它们在 tier 3，``--top 20`` 根本轮不到——**省时最多的建议永远不被测量**。
    """
    from heavyheader.verify import order_for_measurement

    big = _candidate(tmp_path, "stdio.h")
    big.usage_verdict = "in_use"
    big.estimated_seconds_saved = 0.4
    small = _candidate(tmp_path, "unused.h")
    small.usage_verdict = "plausibly_unused"
    small.estimated_seconds_saved = 0.01

    by_rank = order_for_measurement([big, small], "rank")
    by_estimate = order_for_measurement([big, small], "estimate")

    # 判据排序把「疑似未使用」放前面（哪怕它只值 10 ms）；
    # 收益排序把那条被判「在用」的 400 ms 放前面。
    assert by_rank[0] is small
    assert by_estimate[0] is big


def test_estimate_order_keeps_candidates_without_an_estimate(tmp_path):
    """没有预估的候选排最后，但**不能从池子里消失**——排最后不等于没机会。"""
    from heavyheader.verify import order_for_measurement

    known = _candidate(tmp_path, "known.h")
    known.estimated_seconds_saved = 0.05
    unknown = _candidate(tmp_path, "unknown.h")
    unknown.estimated_seconds_saved = None

    ordered = order_for_measurement([unknown, known], "estimate")
    assert [c.header.name for c in ordered] == ["known.h", "unknown.h"]


# ---------------------------------------------------------------------------
# 报告层：这一层必须真的出现在读者眼前
# ---------------------------------------------------------------------------


def _scan_with(tmp_path: Path, results: list):
    from heavyheader.models import CompilerFamily, HeaderStats, ProjectScan

    header = results[0].candidate.header
    scan = ProjectScan(
        root=tmp_path,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[],
        header_stats={
            header: HeaderStats(
                path=header,
                kind=HeaderKind.PROJECT,
                line_count=10,
                tu_reach=1,
                attributed_lines=10,
                inclusion_events=1,
            )
        },
    )
    scan.candidates = [r.candidate for r in results]
    scan.probe_results = results
    scan.probe_mode = "probe"
    scan.reproduce_command = "heavyheader scan /proj --compiler g++"
    return scan


def test_report_shows_the_layer_and_where_it_came_from(tmp_path):
    """报告里必须出现「已验证层级」，并且说明它跟推荐集不是一回事。"""
    from heavyheader.report.markdown import render_markdown

    stdio = _candidate(tmp_path, "stdio.h")
    result = _gated(stdio, "improved", verdict="in_use", seconds=(1.0, 0.9))
    text = render_markdown(_scan_with(tmp_path, [result]))

    assert "已验证层级" in text
    assert "stdio.h" in text
    # 增量来自判据没推荐的那一批，这句话必须写出来，否则读者会以为只是重排。
    assert "在用" in text
    assert "编译器说得清" in text


def test_report_warns_when_only_compiles_was_measured(tmp_path):
    """没跑闸门时表格里那一格必须是警告，不能读成「验证过」。"""
    from heavyheader.report.markdown import render_markdown

    candidate = _candidate(tmp_path, "thing.h")
    result = _result(
        candidate,
        "improved",
        baseline_seconds=1.0,
        patched_seconds=0.9,
        codegen_checked=False,
    )
    text = render_markdown(_scan_with(tmp_path, [result]))

    assert "程序可能变了" in text
    # 只有 compiles_only 时不该出现已验证层级那一节——它建立在汇编证据上。
    assert "已验证层级" not in text

