"""Tests for the decision layer: every candidate gets one explicit verdict.

The rules are the product decision of this iteration, so they are tested as
rules: a table of (evidence → verdict) pairs, each asserted on the actual
``build_advice`` output.  If a future change makes "疑似未使用" leak back
into the verdicts, or lets a ``KEEP`` outrank a ``REMOVE``, these tests fail
before the report does.

The rules under test (precedence order):

====================== ============================================
evidence               verdict
====================== ============================================
no probe result        HUMAN, no lean
compile failed         KEEP
line not locatable     HUMAN, no lean
compiles, not timed    HUMAN, no lean
compiles, timed,       REMOVE
saved ≥ noise floor
compiles, timed,       HUMAN, lean per closure size
saved < noise floor
====================== ============================================
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.attrib.advice import (
    MAINTENANCE_LEAN_LINES,
    TIME_NOISE_FLOOR,
    build_advice,
)
from heavyheader.models import (
    AdviceAspect,
    AdviceVerdict,
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    ProjectScan,
    RemovalCandidate,
    candidate_rank_key,
)
from heavyheader.verify import VerificationResult


def _candidate(
    tmp_path: Path, *, lines: int = 2, reach: int = 1, percent: float = 100.0
) -> RemovalCandidate:
    header = tmp_path / "include" / "heavy.h"
    source = tmp_path / "src" / "app.cpp"
    return RemovalCandidate(
        tu=source,
        header=header,
        line=1,
        kind=HeaderKind.PROJECT,
        exclusive_headers=[header],
        estimated_lines_saved=lines,
        estimated_percent=percent,
    )


def _result(candidate: RemovalCandidate, status: str, **kwargs) -> VerificationResult:
    return VerificationResult(candidate=candidate, status=status, **kwargs)


def _scan(tmp_path: Path, header: Path, reach: int) -> ProjectScan:
    return ProjectScan(
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
                tu_reach=reach,
                attributed_lines=10 * reach,
                inclusion_events=reach,
            )
        },
    )


# --------------------------------------------------------------------------
# the decision table
# --------------------------------------------------------------------------


def test_no_probe_result_means_no_verdict(tmp_path):
    """未实测 = 不给结论。估算不是证据，「疑似」两个字不许回来。"""
    candidate = _candidate(tmp_path)

    advice = build_advice(candidate, None, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == ""
    assert any("未实测" in r.text for r in advice.reasons)


def test_compile_failure_is_keep_not_a_suggestion(tmp_path):
    """编译器已经否决的候选必须显示为「保留」，且不再出现在建议里。"""
    candidate = _candidate(tmp_path)
    result = _result(candidate, "compile-error", message="error: 'Heavy' was not declared")

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.KEEP
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert function_reasons and "编译失败" in function_reasons[0].text


def test_unlocatable_line_is_human_without_a_lean(tmp_path):
    """定位不到该注释哪一行时，任何结论都是假的——包括倾向。"""
    candidate = _candidate(tmp_path)
    result = _result(candidate, "ambiguous", message="源码里有 2 行 include 都叫 heavy.h")

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == ""


def test_compiles_without_timing_is_human_without_a_lean(tmp_path):
    """probe-only 轮次：能编过，但没计时就不能说值得删。"""
    candidate = _candidate(tmp_path)
    result = _result(candidate, "compiles", patched_seconds=0.4)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == ""


def test_measured_improvement_is_remove(tmp_path):
    """编译通过 + 超过噪声线 = 唯一的「建议删除」。"""
    candidate = _candidate(tmp_path)
    result = _result(
        candidate,
        "improved",
        baseline_seconds=1.0,
        patched_seconds=0.6,  # -40%，远超 2% 噪声线
    )

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.REMOVE
    build_reasons = advice.by_aspect(AdviceAspect.BUILD)
    assert build_reasons
    # 理由里必须带数字，不带数字的理由是形容词不是证据。
    assert "1000 ms" in build_reasons[0].evidence
    assert "600 ms" in build_reasons[0].evidence
    assert "40.0%" in build_reasons[0].evidence
    # 「编译通过 ≠ 功能正确」必须出现在功能维度里。
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert function_reasons and "编译通过" in function_reasons[0].text


def test_improvement_inside_noise_floor_is_not_remove(tmp_path):
    """1% 的「提升」在噪声里。判成建议删除就是把噪声当收益。"""
    candidate = _candidate(tmp_path, lines=2, percent=2.0)
    result = _result(
        candidate,
        "no-change",
        baseline_seconds=1.0,
        patched_seconds=0.99,  # -1% < 2%
    )

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == "keep"


def test_small_closure_with_no_win_leans_keep(tmp_path):
    """收益在噪声内、闭包又小：倾向保留，别浪费 review。"""
    candidate = _candidate(tmp_path, lines=10, percent=2.0)
    result = _result(candidate, "no-change", baseline_seconds=1.0, patched_seconds=0.995)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 5))

    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == "keep"
    # 算式必须随结论一起给出，读者才能用同一套算术 disagree。
    assert advice.review_gain_lines == 10
    assert advice.review_gain_share == pytest.approx(0.02)


def test_big_exclusive_closure_with_single_consumer_leans_remove(tmp_path):
    """没有构建收益，但整个项目就此不再解析这 1000+ 行——维护收益真实存在。"""
    candidate = _candidate(tmp_path, lines=MAINTENANCE_LEAN_LINES, percent=60.0)
    result = _result(candidate, "no-change", baseline_seconds=1.0, patched_seconds=0.995)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == "remove"
    maintenance = advice.by_aspect(AdviceAspect.MAINTENANCE)
    assert any("倾向删除" in r.text for r in maintenance)


def test_share_of_the_unit_alone_can_justify_the_lean(tmp_path):
    """行数只有 200（低于 1000 行阈值），但占本单元解析量 30%、
    且全项目仅一个使用者——分享值判据成立，倾向删除。"""
    candidate = _candidate(tmp_path, lines=200, percent=30.0)
    result = _result(candidate, "no-change", baseline_seconds=1.0, patched_seconds=0.995)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.lean == "remove"
    assert advice.review_gain_lines == 200
    assert advice.review_gain_share == pytest.approx(0.30)


def test_share_judge_needs_the_sole_consumer_too(tmp_path):
    """占比够、行数不够，但另有 3 个单元在解析它——项目整体仍在付费，
    只便宜本单元读者，不满足判据。"""
    candidate = _candidate(tmp_path, lines=200, percent=30.0)
    result = _result(candidate, "no-change", baseline_seconds=1.0, patched_seconds=0.995)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 4))

    assert advice.lean == "keep"


def test_big_closure_shared_by_many_units_still_leans_keep(tmp_path):
    """闭包大但别的单元也在用：删除只便宜一个 TU，不够 justify 一次改动。"""
    candidate = _candidate(tmp_path, lines=MAINTENANCE_LEAN_LINES * 2, percent=80.0)
    result = _result(candidate, "no-change", baseline_seconds=1.0, patched_seconds=0.995)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 4))

    assert advice.lean == "keep"


# --------------------------------------------------------------------------
# the measured dimensions
# --------------------------------------------------------------------------


def test_memory_delta_is_reported_with_its_own_caveat(tmp_path):
    """GGC 内存是编译器自报值，不是 RSS——这句话必须跟着数字走。"""
    candidate = _candidate(tmp_path)
    result = _result(
        candidate,
        "improved",
        baseline_seconds=1.0,
        patched_seconds=0.6,
        baseline_memory_kb=100 * 1024,
        patched_memory_kb=60 * 1024,
    )

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    memory_reasons = [
        r for r in advice.by_aspect(AdviceAspect.BUILD) if "内存" in r.text
    ]
    assert memory_reasons
    assert "GGC" in memory_reasons[0].text
    assert "100 kB" in memory_reasons[0].evidence


def test_missing_memory_is_not_reported_as_zero(tmp_path):
    """工具链不提供内存数据时，理由里不出现内存——不编造「不变」。"""
    candidate = _candidate(tmp_path)
    result = _result(
        candidate, "improved", baseline_seconds=1.0, patched_seconds=0.6
    )

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert not any("内存" in r.text for r in advice.reasons)


def test_identical_object_size_is_a_finding_not_silence(tmp_path):
    """「体积不变、收益全在时间侧」是个有信息量的结论。"""
    candidate = _candidate(tmp_path)
    result = _result(
        candidate,
        "improved",
        baseline_seconds=1.0,
        patched_seconds=0.6,
        baseline_object_bytes=20480,
        patched_object_bytes=20480,
    )

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    size_reasons = [r for r in advice.reasons if "目标文件" in r.text]
    assert size_reasons and "不变" in size_reasons[0].text


def test_heuristic_in_use_but_measured_improved_says_so(tmp_path):
    """启发式说在用、编译器说可以删——实测优先，且必须把这个反差写出来，
    否则读者会以为启发式才是权威。"""
    candidate = _candidate(tmp_path)
    candidate.usage_verdict = "in_use"
    result = _result(candidate, "improved", baseline_seconds=1.0, patched_seconds=0.6)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.REMOVE
    assert any("启发式" in r.text for r in advice.reasons)


# --------------------------------------------------------------------------
# determinism & ranking integration
# --------------------------------------------------------------------------


def test_same_evidence_produces_the_same_advice(tmp_path):
    """同样输入必须给出同样结论——规则是确定性的，不是临场发挥。"""
    candidate = _candidate(tmp_path)
    result = _result(candidate, "improved", baseline_seconds=1.0, patched_seconds=0.6)

    first = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))
    second = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert first.verdict is second.verdict
    assert [r.text for r in first.reasons] == [r.text for r in second.reasons]


def test_remove_advice_outranks_everything_else(tmp_path):
    """REMOVE 必须排在最前；KEEP 与实测失败同层，永远不压过活建议。"""
    winner = _candidate(tmp_path / "a")
    winner.advice = build_advice(
        winner,
        _result(winner, "improved", baseline_seconds=1.0, patched_seconds=0.6),
        None,
    )
    maybe = _candidate(tmp_path / "b")
    maybe.advice = build_advice(maybe, None, None)
    vetoed = _candidate(tmp_path / "c")
    vetoed.advice = build_advice(
        vetoed, _result(vetoed, "compile-error", message="boom"), None
    )

    ranked = sorted([vetoed, maybe, winner], key=candidate_rank_key)

    assert ranked[0] is winner
    assert ranked[-1] is vetoed


def test_noise_floor_is_shared_with_the_status_column():
    """verify 的 improved 判定与 REMOVE 结论必须用同一个阈值——
    两个副本就会漂移，然后报告里出现「improved 但不是建议删除」。"""
    assert TIME_NOISE_FLOOR == 0.02


# --------------------------------------------------------------------------
# the report is where the verdict becomes visible to a developer
# --------------------------------------------------------------------------


def _scan_with_advice(tmp_path: Path) -> ProjectScan:
    from heavyheader.report.markdown import render_markdown

    candidate = _candidate(tmp_path)
    scan = _scan(tmp_path, candidate.header, 1)
    scan.candidates = [candidate]
    scan.reproduce_command = (
        "heavyheader scan /proj --compiler g++ --probe"
    )
    return scan, render_markdown(scan)


def test_report_shows_explicit_verdict_not_a_maybe(tmp_path):
    """报告里每个有证据的候选必须是明确结论；
    「疑似未使用」这类可能性措辞不允许充当结论列。"""
    scan, report = _scan_with_advice(tmp_path)
    scan.candidates[0].advice = build_advice(
        scan.candidates[0],
        _result(
            scan.candidates[0],
            "improved",
            baseline_seconds=1.0,
            patched_seconds=0.6,
        ),
        scan,
    )
    from heavyheader.report.markdown import render_markdown

    text = render_markdown(scan)

    assert "| 结论 |" in text
    assert "建议删除" in text
    # 未实测的候选显示「未实测」而不是猜测。
    other = _candidate(tmp_path / "b")
    scan.candidates.append(other)
    text = render_markdown(scan)
    assert "未实测" in text


def test_report_marks_unprobed_candidates_as_unmeasured(tmp_path):
    """没跑 --probe 时，结论列必须是「未实测」——没测过就下判断是编造。"""
    _scan, report = _scan_with_advice(tmp_path)

    # 图例里可以出现这三个词（解释口径），但结论小节本身不允许出现——
    # 没有任何候选有证据时，不生成「值不值得删」那一节。
    assert "未实测" in report
    assert "## 删除建议：值不值得删" not in report


def test_report_advice_section_carries_the_reproduce_command(tmp_path):
    """实测结论必须附带可重跑的命令，否则它只是我们机器上的一串数字。"""
    scan, report = _scan_with_advice(tmp_path)
    scan.candidates[0].advice = build_advice(
        scan.candidates[0],
        _result(
            scan.candidates[0],
            "improved",
            baseline_seconds=1.0,
            patched_seconds=0.6,
        ),
        scan,
    )
    from heavyheader.report.markdown import render_markdown

    text = render_markdown(scan)

    assert "## 复现方式" in text or "复现" in text
    assert "heavyheader scan /proj --compiler g++ --probe" in text
    assert "## 删除建议：值不值得删" in text


def test_report_states_that_compiling_is_not_correctness(tmp_path):
    """「编译通过 ≠ 功能正确」必须写在报告里，不能只在代码注释里。"""
    scan, _report = _scan_with_advice(tmp_path)
    scan.candidates[0].advice = build_advice(
        scan.candidates[0],
        _result(
            scan.candidates[0],
            "improved",
            baseline_seconds=1.0,
            patched_seconds=0.6,
        ),
        scan,
    )
    from heavyheader.report.markdown import render_markdown

    assert "编译通过 ≠ 功能正确" in render_markdown(scan)


# --------------------------------------------------------------------------
# the test-runner evidence grade
# --------------------------------------------------------------------------


def _improved_result(candidate, **kwargs) -> VerificationResult:
    return _result(
        candidate,
        "improved",
        baseline_seconds=1.0,
        patched_seconds=0.6,
        **kwargs,
    )


def test_faster_but_tests_fail_is_not_a_remove(tmp_path):
    """编译通过且实测更快，但单元测试失败——「更快但坏了」不是收益，
    是带着秒表的回归。测试是比编译器更高的权威。"""
    candidate = _candidate(tmp_path)
    result = _improved_result(candidate, test_status="failed", test_detail="2 assertions failed")

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == "keep"
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert function_reasons and "测试" in function_reasons[0].text
    assert "2 assertions failed" in function_reasons[0].evidence


def test_passing_tests_are_quoted_as_the_highest_evidence_grade(tmp_path):
    """测试通过要把「真实执行过」写进理由——这是不变成测试框架的前提下，
    本工具能到的最高证据等级；同时写明它仍未覆盖项目其余部分。"""
    candidate = _candidate(tmp_path)
    result = _improved_result(candidate, test_status="passed", test_detail="42 assertions passed")

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.REMOVE
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert any("测试" in r.text and "42 assertions" in r.evidence for r in function_reasons)
    assert any("仍未覆盖项目其余部分" in r.text for r in function_reasons)


def test_baseline_test_failure_is_not_charged_to_the_candidate(tmp_path):
    """基线版测试就没过：那是测试环境或基线的问题。结论维持，
    但理由必须说明「本条证据止于编译」——不能把没取证包装成已取证。"""
    candidate = _candidate(tmp_path)
    result = _improved_result(candidate, test_status="error", test_detail="baseline suite red")

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.REMOVE
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert any("测试未能提供证据" in r.text for r in function_reasons)


def test_without_a_runner_the_advice_says_so_by_silence(tmp_path):
    """没配 test_runner 时不出测试理由——但「编译通过 ≠ 功能正确」
    的脚注必须在，读者才不会以为跑过测试。"""
    candidate = _candidate(tmp_path)
    result = _improved_result(candidate)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.REMOVE
    # 不能出现任何「测试已执行」类理由（注意：脚注里也有「测试」二字，
    # 所以按证据句子的专属措辞匹配，而不是裸词匹配）。
    assert not any(
        "该单元测试在补丁版上" in r.text or "测试未能提供证据" in r.text
        for r in advice.reasons
    )
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert any("编译通过不等于功能正确" in r.text for r in function_reasons)


def test_ast_tool_agreement_is_quoted_as_corroboration(tmp_path):
    """clang-include-cleaner 也把同一个 include 列为可删时，作为佐证写进 REMOVE
    理由——这是「只增强、不削弱」：语义层的一致让我们更敢推这条建议。"""
    from heavyheader.attrib.semantic import SemanticVerdict

    candidate = _candidate(tmp_path)
    candidate.semantic = SemanticVerdict(
        candidate.tu.stem, frozenset({candidate.header.name}), True
    )
    result = _improved_result(candidate)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.REMOVE
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert any("交叉印证" in r.text for r in function_reasons)
    assert any(candidate.header.name in r.evidence for r in function_reasons)


def test_ast_tool_failure_is_stated_not_silent(tmp_path):
    """语义工具分析此单元失败：既不佐证也不 demote，但必须明说「未能提供」，
    让 REMOVE 的背书更诚实——绝不假装做过 AST 校验。"""
    from heavyheader.attrib.semantic import SemanticVerdict

    candidate = _candidate(tmp_path)
    candidate.semantic = SemanticVerdict(
        candidate.tu.stem, frozenset(), False, note="unit-not-analysed"
    )
    result = _improved_result(candidate)

    advice = build_advice(candidate, result, _scan(tmp_path, candidate.header, 1))

    assert advice.verdict is AdviceVerdict.REMOVE
    function_reasons = advice.by_aspect(AdviceAspect.FUNCTION)
    assert any("未能提供" in r.text for r in function_reasons)
    assert any("unit-not-analysed" in r.evidence for r in function_reasons)


def test_codegen_changed_is_human_with_keep_lean(tmp_path):
    """编译通过但代码变了：不能给「建议删除」，也不能当成「删不掉」。

    实测案例：删 `<iomanip>` 后目标文件面积减半（重载解析改变）。这类改动
    如果按 `improved` 处理，使用者会在不知情的情况下换掉程序的行为。
    """
    candidate = _candidate(tmp_path)
    result = _result(
        candidate,
        "codegen-changed",
        message="移除后编译通过，但生成的代码与原来不同",
    )
    advice = build_advice(candidate, result, None)
    assert advice.verdict is AdviceVerdict.HUMAN
    assert advice.lean == "keep"
    texts = " ".join(reason.text for reason in advice.reasons)
    assert "编译通过" in texts and "同一个程序" in texts
    assert "不计入收益" in texts


def test_scan_report_lists_the_verify_queue(tmp_path):
    """没跑 verify 的扫描报告要把候选列成「待验证队列」，而不是留给读者。

    旧版在 scan-only 报告里把「无法判定」当成读者的作业。这一节说清队列多大、
    verify 按什么顺序测；无法判定层单列有实测依据（Lua 32/32 通过三真值）。
    """
    from heavyheader.report.markdown import render_markdown

    unknown = _candidate(tmp_path / "u")
    unknown.usage_verdict = "unknown"
    unknown.usage_label = "无法判定"
    in_use = _candidate(tmp_path / "i")
    in_use.usage_verdict = "in_use"
    in_use.usage_label = "在用"
    scan = _scan(tmp_path, unknown.header, 1)
    scan.candidates = [in_use, unknown]
    scan.reproduce_command = "heavyheader scan /proj --compiler g++"

    text = render_markdown(scan)

    assert "待验证队列" in text
    assert "verify" in text
    # 分层计数必须写出来：疑似未使用 0 · 无法判定 1 · 在用 1。
    assert "疑似未使用 **0** 条 · 无法判定 **1** 条 · 在用 **1** 条" in text
    # 队列顺序 = candidate_rank_key：无法判定排在在用前面。
    assert text.index("无法判定") < text.index("在用")


def test_pending_queue_absent_when_everything_is_verified(tmp_path):
    """全部候选都有实测结论时不再渲染队列——没有待验证项就不摆空桌子。"""
    from heavyheader.report.markdown import render_markdown

    candidate = _candidate(tmp_path)
    scan = _scan(tmp_path, candidate.header, 1)
    scan.candidates = [candidate]
    scan.reproduce_command = "heavyheader scan /proj --compiler g++"
    scan.candidates[0].advice = build_advice(
        candidate,
        _result(candidate, "improved", baseline_seconds=1.0, patched_seconds=0.6),
        scan,
    )

    text = render_markdown(scan)

    assert "待验证队列" not in text
    assert "建议删除" in text



# ---------------------------------------------------------------------------
# 证据层（2026-10-20）
#
# 起因是供给侧漏报的实测：8 条落地收益里 7 条被文本判据判成 in_use / unknown，
# 却全都过了汇编等价闸门。判据说不清、编译器说得清的时候，界面不能把两者
# 混在同一句「建议删除」里。
# ---------------------------------------------------------------------------

def test_evidence_tier_only_credits_a_gate_that_actually_ran(tmp_path):
    """证据层只认「闸门跑过且一致」，不认「本轮想不想跑」。

    闸门没跑（库调用默认关闭 / 编译器给不出汇编 / 主动省时间）时必须停在
    ``compiles_only``：省下的两次编译不能变成一句没量过的「程序没变」。
    """
    candidate = _candidate(tmp_path)
    table = [
        # status,      checked, identical, expected tier
        ("improved", False, None, "compiles_only"),
        ("improved", True, True, "verified"),
        ("no-change", True, True, "equivalent"),
        ("no-change", False, None, "compiles_only"),
        ("compiles", True, True, "equivalent"),
        ("compiles", False, None, "compiles_only"),
    ]
    for status, checked, identical, expected in table:
        result = _result(
            candidate,
            status,
            baseline_seconds=1.0,
            patched_seconds=0.8,
            codegen_checked=checked,
            codegen_identical=identical,
        )
        assert result.evidence_tier == expected, (status, checked, identical)


def test_rejected_statuses_carry_no_tier(tmp_path):
    """被否决的候选不参与分层——没有「可删」可言，就谈不到证据有多强。"""
    candidate = _candidate(tmp_path)
    for status in ("compile-error", "codegen-changed", "ambiguous", "not-found", "error"):
        result = _result(
            candidate,
            status,
            codegen_checked=True,
            codegen_identical=(status != "codegen-changed"),
        )
        assert result.evidence_tier == "", status


def test_remove_never_claims_equivalence_without_the_gate(tmp_path):
    """闸门没跑时，REMOVE 不许说「程序没变」。

    分层纪律里最容易被违反的一条，也是最危险的一条：一句没量过的承诺比
    不做校验更糟，因为读者会照着它合并。
    """
    candidate = _candidate(tmp_path)
    ungated = build_advice(
        candidate,
        _result(candidate, "improved", baseline_seconds=1.0, patched_seconds=0.8),
        None,
    )
    assert ungated.verdict is AdviceVerdict.REMOVE
    plain = " ".join(f"{r.text} {r.evidence or ''}" for r in ungated.reasons)
    assert "没有做代码等价校验" in plain
    assert "一字未变" not in plain

    gated = build_advice(
        candidate,
        _result(
            candidate,
            "improved",
            baseline_seconds=1.0,
            patched_seconds=0.8,
            codegen_checked=True,
            codegen_identical=True,
        ),
        None,
    )
    assert gated.verdict is AdviceVerdict.REMOVE
    proven = " ".join(f"{r.text} {r.evidence or ''}" for r in gated.reasons)
    assert "一字未变" in proven
    assert "没有做代码等价校验" not in proven


def test_no_change_says_equivalence_is_proven_when_the_gate_passed(tmp_path):
    """收益不可测量 ≠ 什么都不确定：闸门过了就该把「程序不变」单独讲清楚。

    这一档正是供给侧那 12 条 UCRT 疑似副作用的落点：文本判不了，编译器判了，
    只是省下的时间落在噪声里。
    """
    candidate = _candidate(tmp_path)
    advice = build_advice(
        candidate,
        _result(
            candidate,
            "no-change",
            baseline_seconds=1.0,
            patched_seconds=0.99,
            codegen_checked=True,
            codegen_identical=True,
        ),
        None,
    )
    text = " ".join(f"{r.text} {r.evidence or ''}" for r in advice.reasons)
    assert "一字未变" in text
    # 收益那一半仍然不许夸大。
    assert "不能声称构建更快" in text
