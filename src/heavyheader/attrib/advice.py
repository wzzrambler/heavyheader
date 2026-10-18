"""The decision layer: "is this include worth removing?"

Everything upstream answers *possibility* questions -- how much does this
header cost, is it plausibly used, does the unit still compile without it.
None of that is what a developer actually asks.  They ask **"should I delete
this line?"**, and until this module existed the tool answered with
"疑似未使用" -- a maybe, which turns the report into a todo list the reader
still has to think about, one line at a time.

This module converts evidence into one of exactly three verdicts:

``REMOVE``
    Probed: the unit compiles without the include, **and** it measured
    faster beyond the noise floor.  Both claims are machine-checked facts
    from :mod:`heavyheader.verify`, not opinions.
``KEEP``
    The compiler already vetoed the edit, or the include line cannot be
    located at all.  "Tried; does not work" is a finding, and it is shown
    -- but it is never dressed up as a suggestion.
``HUMAN``
    Compiles, but the evidence stops short of a verdict: no measurable win,
    or never measured.  This is *not* a fuzzy state.  It says precisely
    which evidence is missing, and when the remaining evidence points one
    way it records a ``lean`` so the reader is not left to guess.

Why the rules live here and not in the report
---------------------------------------------
The report used to contain the thresholds ("2% is noise") in prose, while
the code that *applied* them lived in ``verify``.  Two copies of a rule is
one copy too many: the next person to edit the prose would not know the
code exists.  The noise floor is therefore defined once, here, and
``verify`` imports it -- so "improved" in the status column and "REMOVE"
in the verdict column can never disagree about what improvement means.

Honesty boundaries, stated once:

* compiling is not correctness.  A ``REMOVE`` verdict means "this unit
  compiles faster without this line", never "the project is still
  correct".  Running the test suite is the developer's step, and the
  advice says so.
* the 2% noise floor is an *empirical* threshold measured on this class
  of workload (see ``experiments/control.py`` A/A runs), not a statistical
  confidence bound.  It is documented as such wherever it is shown.
* the maintenance ``lean`` threshold is a *policy* choice (how many lines
  of avoided parsing justify a review round-trip), not physics.  It is a
  named constant so it can be argued with.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..models import (
    AdviceAspect,
    AdviceReason,
    AdviceVerdict,
    ProjectScan,
    RemovalAdvice,
    RemovalCandidate,
)

if TYPE_CHECKING:
    # 只在类型注解里出现：verify 要 import 本模块的噪声阈值，反向 import
    # 会成环，所以这里用 TYPE_CHECKING 守卫，运行时不建立依赖。
    from ..verify import VerificationResult

__all__ = [
    "TIME_NOISE_FLOOR",
    "AGGREGATE_NOISE_FLOOR",
    "MEMORY_NOISE_FLOOR",
    "MAINTENANCE_LEAN_LINES",
    "MAINTENANCE_LEAN_SHARE",
    "build_advice",
]

#: A time difference below this share of the baseline is inside the noise
#: floor of one unit's compile time on a loaded machine.  Derived from the
#: A/A control runs in ``experiments/control.py`` (median absolute drift
#: 2.36%, max 8.30% on that machine) -- an empirical threshold, not a
#: confidence interval.  ``verify`` imports it so the status column and the
#: verdict column share one definition of "improved".
TIME_NOISE_FLOOR = 0.02

#: Same idea for the compiler-reported GGC allocation peak.  Allocation is
#: far more deterministic than wall time, but the allocator's own
#: bookkeeping still wobbles a little, so the bar is the same 2% rather
#: than zero.
MEMORY_NOISE_FLOOR = 0.02

#: 聚合口径的噪声线：**一批编译单元耗时之和** + **交替测量**（B/P/B/P…）。
#:
#: 两个数字不能混用。单单元口径是 2%（上面那个），但把 24 个单元的耗时
#: 加起来测两次，差也会到 2%——因为误差是**同一批测量期间的机器漂移**，
#: 不是每个单元各自抖动，聚合并不能把它平均掉（实测 20299/20269/20675 ms，
#: 第三次整体高 2%）。先测完基线再测补丁的协议，会把这 2% 的漂移整块算成
#: 收益，这正是 G2 里 1.0–1.9% 的候选无法判定的根源。
#:
#: 改成交替后（``experiments/g2_interleaved.py``，doctest 24 TU、A/A 两侧
#: 同一份代码）：聚合差异 **0.644% / 0.245%**，比单单元口径低一个数量级的
#: 一半。1% 是在观测到的最大值上留了余量，不是把观测值当上界。
#:
#: 只用于头文件级候选（一次改动影响多个单元）。TU 级候选仍是单单元口径 +
#: 2%——那里的分母是一次编译，漂移结构不同，套用这个数会放松标准。
AGGREGATE_NOISE_FLOOR = 0.01

#: How many lines of avoided parsing justify a deletion whose only benefit
#: is maintenance (no measurable build-time win).  Policy, not physics:
#: every deletion costs a review round-trip and a test run, and 200 lines
#: of headers nobody will ever miss is not worth one.  Change it if your
#: team disagrees.
MAINTENANCE_LEAN_LINES = 1000

#: The share of *this unit's* header parsing that disappears.  A 25% cut is
#: a maintenance win even when other units still parse the same header --
#: the reader of this file stops paying for it.  Policy, like the line
#: threshold above.
MAINTENANCE_LEAN_SHARE = 0.25


def _fmt_ms(seconds: float | None) -> str:
    if seconds is None:
        return "未测"
    return f"{seconds * 1000:.0f} ms"


def _fmt_kb(value: int | None) -> str:
    if value is None:
        return "该工具链不提供"
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} MB"
    return f"{value / 1024:.0f} kB"


def _closure_summary(candidate: RemovalCandidate) -> str:
    return (
        f"{len(candidate.exclusive_headers)} 个头文件 / "
        f"{candidate.estimated_lines_saved:,} 行"
    )


def _reach_note(scan: ProjectScan | None, header: Path) -> str:
    """Who else still parses this header after the edit?

    ``tu_reach == 1`` means this unit is the *only* consumer in the project:
    deleting the include retires the header entirely.  Anything above one
    means the rest of the project keeps paying for it, and the benefit of
    this edit is confined to the one unit -- a materially different
    trade-off that the old report never mentioned.
    """
    if scan is None:
        return ""
    stats = scan.header_stats.get(header)
    if stats is None:
        return ""
    if stats.tu_reach <= 1:
        return "全项目仅此一个编译单元引用它，删除后整个项目不再解析这些代码"
    return (
        f"另有 {stats.tu_reach - 1} 个编译单元也会解析它，"
        "本次删除只影响当前单元，其余单元的编译成本不变"
    )


def _unprobed(candidate: RemovalCandidate) -> RemovalAdvice:
    """No probe ran: the honest answer is "we do not know yet"."""
    reasons = [
        AdviceReason(
            aspect=AdviceAspect.FUNCTION,
            text=(
                "未实测。删除后能否编译、会不会破坏功能，工具没有答案——"
                f"文本启发式（{candidate.usage_label}）不是安全性保证"
            ),
        ),
        AdviceReason(
            aspect=AdviceAspect.MAINTENANCE,
            text="删除后不再被解析的代码",
            evidence=_closure_summary(candidate),
        ),
        AdviceReason(
            aspect=AdviceAspect.BUILD,
            text=(
                "预计节省为按行数折算的估算，不是秒表读数；"
                "跑 ``--probe`` 后才有时效性结论"
            ),
            evidence=(
                f"≈{_fmt_ms(candidate.estimated_seconds_saved)}（估算）"
                if candidate.estimated_seconds_saved is not None
                else "该单元无耗时数据"
            ),
        ),
    ]
    return RemovalAdvice(
        verdict=AdviceVerdict.HUMAN,
        reasons=reasons,
        lean="",
    )


def _keep(candidate: RemovalCandidate, result: VerificationResult) -> RemovalAdvice:
    """The compiler said no.  Record it; do not dress it up."""
    detail = " ".join(str(result.message).split())[:160]
    reasons = [
        AdviceReason(
            aspect=AdviceAspect.FUNCTION,
            text=f"实测编译失败：{detail}" if detail else "实测编译失败",
        ),
        AdviceReason(
            aspect=AdviceAspect.MAINTENANCE,
            text="无需权衡——这个编辑在当前配置下不可行，改不了就不是选项",
        ),
        AdviceReason(
            aspect=AdviceAspect.BUILD,
            text="编译不过 = 没有构建可言，收益为 0",
        ),
    ]
    return RemovalAdvice(verdict=AdviceVerdict.KEEP, reasons=reasons)


def _cannot_locate(
    candidate: RemovalCandidate, result: VerificationResult
) -> RemovalAdvice:
    """We could not pin down which line to comment out.  Not guessing."""
    detail = " ".join(str(result.message).split())[:160]
    reasons = [
        AdviceReason(
            aspect=AdviceAspect.FUNCTION,
            text=f"无法定位该注释哪一行：{detail}" if detail else "无法定位该注释哪一行",
        ),
        AdviceReason(
            aspect=AdviceAspect.MAINTENANCE,
            text="定位失败时任何结论都是假的——注释错一行，指向的就是另一个头文件",
        ),
        AdviceReason(
            aspect=AdviceAspect.BUILD,
            text="未进入编译阶段，没有时效性数据",
        ),
    ]
    return RemovalAdvice(verdict=AdviceVerdict.HUMAN, reasons=reasons, lean="")


def _remove(
    candidate: RemovalCandidate,
    result: VerificationResult,
    scan: ProjectScan | None,
) -> RemovalAdvice:
    """Compiles without it, measurably faster, and (if run) tests pass."""
    saved = result.seconds_saved or 0.0
    percent = result.percent_saved or 0.0
    build_text = "实测编译耗时下降，超过 2% 噪声线"
    if getattr(result, "timing_note", ""):
        build_text += f"（注意：{result.timing_note}）"
    reasons = [
        AdviceReason(
            aspect=AdviceAspect.FUNCTION,
            text=(
                "移除后该单元编译通过（真实编译验证，非推断）。"
                "注意：编译通过不等于功能正确——合并前请跑一遍这个单元的测试"
            ),
        ),
        AdviceReason(
            aspect=AdviceAspect.BUILD,
            text=build_text,
            evidence=(
                f"{_fmt_ms(result.baseline_seconds)} → {_fmt_ms(result.patched_seconds)}"
                f"（−{percent:.1f}%，−{_fmt_ms(saved)}）"
            ),
        ),
        AdviceReason(
            aspect=AdviceAspect.MAINTENANCE,
            text="删除后这些代码不再被解析",
            evidence=_closure_summary(candidate),
        ),
    ]

    memory_note = _memory_reason(result)
    if memory_note is not None:
        reasons.append(memory_note)
    size_note = _size_reason(result)
    if size_note is not None:
        reasons.append(size_note)

    reach = _reach_note(scan, candidate.header)
    if reach:
        reasons.append(
            AdviceReason(aspect=AdviceAspect.MAINTENANCE, text=reach)
        )

    if candidate.usage_verdict == "in_use":
        reasons.append(
            AdviceReason(
                aspect=AdviceAspect.FUNCTION,
                text=(
                    "文本启发式认为该头仍在被使用，但编译器不这么认为——"
                    "实测编译优先于启发式，这也正是启发式不能单独作为结论的原因"
                ),
            )
        )
    _append_test_reason(reasons, result)
    # AST 交叉检查（仅增强、不削弱）：工具把同一头列为可删时作为佐证；
    # 工具分析此单元失败时说明「未能提供」，让 REMOVE 的背书更诚实。
    reasons += _semantic_reasons(candidate)
    return RemovalAdvice(verdict=AdviceVerdict.REMOVE, reasons=reasons)


def _semantic_reasons(candidate: RemovalCandidate) -> list[AdviceReason]:
    """AST-level cross-check reasons, if the semantic layer produced a verdict.

    The contract is **corroborate only**: when clang-include-cleaner also lists
    this header as removable, that is evidence worth quoting; when the tool's
    analysis of this unit failed, we say so plainly rather than imply a verdict.
    When the tool is silent about a header (not listed as removable) we add
    nothing -- its silence is not a verdict of "in use", and demoting on silence
    would be the ctypes failure all over again, just slower.
    """
    from .semantic import _semantic_agrees

    verdict = candidate.semantic
    if verdict is None:
        return []
    agrees = _semantic_agrees(verdict, candidate.header)
    if agrees is True:
        return [
            AdviceReason(
                aspect=AdviceAspect.FUNCTION,
                text=(
                    "AST 级工具 clang-include-cleaner 同样判定此 include 可删，"
                    "与实测结论交叉印证"
                ),
                evidence=f"工具输出移除建议：{candidate.header.name}",
            )
        ]
    if not verdict.ok:
        return [
            AdviceReason(
                aspect=AdviceAspect.FUNCTION,
                text=(
                    "AST 交叉检查未能提供：clang-include-cleaner 分析此单元失败，"
                    "本结论仍以本工具实证为准"
                ),
                evidence=verdict.note,
            )
        ]
    return []


def _append_test_reason(reasons: list[AdviceReason], result) -> None:
    """Quote the test-runner outcome as a FUNCTION-grade reason.

    Three states, deliberately unequal:

    * ``passed`` -- the highest evidence grade this tool can reach without
      becoming a test framework itself: the unit compiles faster **and**
      its tests pass.  It still is not "the project is correct" -- only
      this unit's tests ran -- and the wording says exactly that.
    * ``failed`` -- handled by the caller (the verdict is demoted); this
      function is not reached with a failure.
    * ``error`` / not run -- silence is wrong here: the reader must know
      the stronger evidence was *not* collected, so they do not mistake
      "compiles faster" for "verified".
    """
    status = getattr(result, "test_status", "")
    detail = getattr(result, "test_detail", "")
    if status == "passed":
        reasons.append(
            AdviceReason(
                aspect=AdviceAspect.FUNCTION,
                text=(
                    "该单元测试在补丁版上真实执行并通过（证据等级高于单纯编译；"
                    "仍未覆盖项目其余部分）"
                ),
                evidence=detail,
            )
        )
    elif status == "error":
        reasons.append(
            AdviceReason(
                aspect=AdviceAspect.FUNCTION,
                text="测试未能提供证据（基线版测试即未通过或执行失败）——本条结论的证据止于编译",
                evidence=detail,
            )
        )


def _test_failed(
    candidate: RemovalCandidate,
    result: VerificationResult,
    scan: ProjectScan | None,
) -> RemovalAdvice:
    """Compiles faster, measurably -- and the unit's tests fail.

    "更快但坏了" is not an optimization, it is a regression with a stopwatch.
    The verdict drops to HUMAN with a keep lean: the compiler cannot veto
    this edit, but the tests can, and the tests are the higher authority.
    """
    saved = result.seconds_saved or 0.0
    percent = result.percent_saved or 0.0
    reasons = [
        AdviceReason(
            aspect=AdviceAspect.FUNCTION,
            text=(
                "**该单元测试在补丁版上失败**——编译通过且更快，但功能被破坏。"
                "「更快但坏了」不是收益，是带着秒表的回归"
            ),
            evidence=getattr(result, "test_detail", ""),
        ),
        AdviceReason(
            aspect=AdviceAspect.BUILD,
            text="耗时确实下降（但这不改变结论：测试是比编译器更高的权威）",
            evidence=(
                f"{_fmt_ms(result.baseline_seconds)} → {_fmt_ms(result.patched_seconds)}"
                f"（−{percent:.1f}%，−{_fmt_ms(saved)}）"
            ),
        ),
        AdviceReason(
            aspect=AdviceAspect.MAINTENANCE,
            text="删除后不再被解析的代码（收益真实存在，但被功能证据否决）",
            evidence=_closure_summary(candidate),
        ),
    ]
    reach = _reach_note(scan, candidate.header)
    if reach:
        reasons.append(AdviceReason(aspect=AdviceAspect.MAINTENANCE, text=reach))
    return RemovalAdvice(
        verdict=AdviceVerdict.HUMAN,
        reasons=reasons,
        lean="keep",
        review_gain_lines=candidate.estimated_lines_saved,
        review_gain_share=round(candidate.estimated_percent / 100.0, 4),
    )


def _memory_reason(result: VerificationResult) -> AdviceReason | None:
    """GGC allocation delta, when the toolchain reports one."""
    base = getattr(result, "baseline_memory_kb", None)
    patched = getattr(result, "patched_memory_kb", None)
    if base is None or patched is None:
        return None
    delta = patched - base
    share = abs(delta) / base if base else 0.0
    if share < MEMORY_NOISE_FLOOR:
        return AdviceReason(
            aspect=AdviceAspect.BUILD,
            text="编译器内存分配峰值无实质变化（GGC 自报值，差异在 2% 以内）",
            evidence=f"{_fmt_kb(base)} → {_fmt_kb(patched)}",
        )
    return AdviceReason(
        aspect=AdviceAspect.BUILD,
        text="编译器内存分配峰值下降（GGC 自报值，非进程 RSS）",
        evidence=f"{_fmt_kb(base)} → {_fmt_kb(patched)}（−{share * 100:.1f}%）",
    )


def _size_reason(result: VerificationResult) -> AdviceReason | None:
    """Object-file size delta, when a full compile was measured."""
    base = getattr(result, "baseline_object_bytes", None)
    patched = getattr(result, "patched_object_bytes", None)
    if base is None or patched is None:
        return None
    delta = patched - base
    if delta == 0:
        return AdviceReason(
            aspect=AdviceAspect.BUILD,
            text="目标文件体积不变——本次收益全部在编译时间侧，不在产物体积侧",
            evidence=f"{base:,} B → {patched:,} B",
        )
    return AdviceReason(
        aspect=AdviceAspect.BUILD,
        text="目标文件体积变化（完整编译实测）",
        evidence=f"{base:,} B → {patched:,} B（{delta:+,} B）",
    )


def _human_with_lean(
    candidate: RemovalCandidate,
    result: VerificationResult,
    scan: ProjectScan | None,
) -> RemovalAdvice:
    """Compiles, but no measurable build win.  Both sides, then a lean.

    The lean is arithmetic, not a mood.  A deletion costs a review round-trip
    and a test run; what it buys back is the parsing this unit (and possibly
    the whole project) stops doing.  So the rule states both numbers and
    both thresholds, and the reader can redo the division:

        lean = remove  ⇔  sole consumer in the project
                        ∧ (≥ MAINTENANCE_LEAN_LINES lines
                           ∨ ≥ MAINTENANCE_LEAN_SHARE of this unit)

    "Sole consumer" matters because if other units still parse the header,
    the project as a whole keeps paying -- only this unit's reader stops,
    which is a real but smaller benefit.
    """
    saved = result.seconds_saved or 0.0
    gain_lines = candidate.estimated_lines_saved
    gain_share = candidate.estimated_percent / 100.0
    reasons = [
        AdviceReason(
            aspect=AdviceAspect.FUNCTION,
            text="移除后编译通过（真实编译验证）——功能上这个编辑是可行的",
        ),
        AdviceReason(
            aspect=AdviceAspect.BUILD,
            text=(
                "耗时差异在 2% 噪声线以内，不能声称构建更快"
                "（该阈值是实测经验值，不是统计置信界）"
            ),
            evidence=(
                f"{_fmt_ms(result.baseline_seconds)} → "
                f"{_fmt_ms(result.patched_seconds)}（{saved * 1000:+.0f} ms）"
            ),
        ),
        AdviceReason(
            aspect=AdviceAspect.MAINTENANCE,
            text="删除后本单元不再解析的代码",
            evidence=_closure_summary(candidate),
        ),
    ]
    reach = _reach_note(scan, candidate.header)
    if reach:
        reasons.append(AdviceReason(aspect=AdviceAspect.MAINTENANCE, text=reach))

    stats = scan.header_stats.get(candidate.header) if scan else None
    only_consumer = stats is not None and stats.tu_reach <= 1
    other_consumers = (stats.tu_reach - 1) if stats is not None else 0
    worth_it = only_consumer and (
        gain_lines >= MAINTENANCE_LEAN_LINES or gain_share >= MAINTENANCE_LEAN_SHARE
    )
    if worth_it:
        lean = "remove"
        reasons.append(
            AdviceReason(
                aspect=AdviceAspect.MAINTENANCE,
                text="倾向删除：维护收益算式成立",
                evidence=(
                    f"净收益 {gain_lines:,} 行（占本单元 {gain_share * 100:.1f}%），"
                    f"全项目仅此一个使用者；代价是一次 review 加一遍该单元测试。"
                    f"判据：仅一个使用者且（≥{MAINTENANCE_LEAN_LINES:,} 行 "
                    f"或 ≥{MAINTENANCE_LEAN_SHARE * 100:.0f}% 占比）"
                ),
            )
        )
    else:
        lean = "keep"
        if not only_consumer:
            why = f"另有 {other_consumers} 个编译单元仍在解析它，删除只便宜本单元"
        elif gain_lines < MAINTENANCE_LEAN_LINES and gain_share < MAINTENANCE_LEAN_SHARE:
            why = (
                f"净收益 {gain_lines:,} 行（占本单元 {gain_share * 100:.1f}%），"
                f"低于判据（≥{MAINTENANCE_LEAN_LINES:,} 行或 "
                f"≥{MAINTENANCE_LEAN_SHARE * 100:.0f}%）"
            )
        else:
            why = "构建收益在噪声内，维护收益不满足倾向删除判据"
        reasons.append(
            AdviceReason(
                aspect=AdviceAspect.MAINTENANCE,
                text="倾向保留：把审查精力花在实测有构建收益的行上",
                evidence=f"{why}；改动代价是一次 review 加一遍该单元测试",
            )
        )
    return RemovalAdvice(
        verdict=AdviceVerdict.HUMAN,
        reasons=reasons,
        lean=lean,
        review_gain_lines=gain_lines,
        review_gain_share=round(gain_share, 4),
    )


def build_advice(
    candidate: RemovalCandidate,
    result: VerificationResult | None = None,
    scan: ProjectScan | None = None,
) -> RemovalAdvice:
    """Turn the evidence about one candidate into an explicit verdict.

    Pure and deterministic: same inputs, same advice.  The rules, in
    precedence order:

    1. no probe result            → ``HUMAN``, no lean (nothing was measured)
    2. probe says compile failed  → ``KEEP`` (the compiler already vetoed)
    3. line cannot be located     → ``HUMAN``, no lean (guessing would lie)
    4. compiles, not timed        → ``HUMAN``, no lean
    5. compiles, timed, improved  → ``REMOVE`` -- *unless* a test runner was
       supplied and the unit's tests fail, in which case the compiler's
       blessing is overruled by the tests': ``HUMAN`` with a keep lean.
       "Faster but broken" is a regression with a stopwatch, not a win.
    6. compiles, timed, no change → ``HUMAN`` with a maintenance lean

    Rule 5's test clause is what turns "compiles faster" into "verified
    faster" -- and it is deliberately opt-in (``VerifyOptions.test_runner``),
    because the honest default is to say the stronger evidence was *not*
    collected rather than to imply it was.

    Rule 6 is where the old tool was silent.  "疑似未使用" covered both
    "worth trying" and "not worth the review" with the same words; the lean
    separates them with a stated threshold instead of vibes.
    """
    if result is None:
        return _unprobed(candidate)

    if result.status == "compile-error":
        return _keep(candidate, result)
    if result.status in ("ambiguous", "not-found", "error"):
        return _cannot_locate(candidate, result)
    if result.status == "compiles":
        reasons = [
            AdviceReason(
                aspect=AdviceAspect.FUNCTION,
                text="移除后编译通过（真实编译验证，未做耗时测量）",
            ),
            AdviceReason(
                aspect=AdviceAspect.MAINTENANCE,
                text="删除后不再被解析的代码",
                evidence=_closure_summary(candidate),
            ),
            AdviceReason(
                aspect=AdviceAspect.BUILD,
                text="本轮只问了「能不能编过」，没问「能快多少」——加测量轮次才有收益结论",
            ),
        ]
        reach = _reach_note(scan, candidate.header)
        if reach:
            reasons.append(AdviceReason(aspect=AdviceAspect.MAINTENANCE, text=reach))
        return RemovalAdvice(verdict=AdviceVerdict.HUMAN, reasons=reasons, lean="")
    if result.status == "improved":
        if getattr(result, "test_status", "") == "failed":
            # 测试是比编译器更高的权威：编译通过且更快，但测试挂了，
            # 结论必须降级。没有这条规则，「建议删除」就只是「编译通过」。
            return _test_failed(candidate, result, scan)
        return _remove(candidate, result, scan)
    return _human_with_lean(candidate, result, scan)
