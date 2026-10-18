"""Core data model shared by every HeavyHeader stage.

The model is deliberately boring: plain dataclasses, no framework, no
side effects.  Everything the tool knows about a compilation run flows
through these types, which makes the pipeline easy to test in isolation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class CompilerFamily(enum.Enum):
    """Which dialect of compiler output we are talking to."""

    GCC = "gcc"
    CLANG = "clang"
    MSVC = "msvc"


class HeaderKind(enum.Enum):
    """Where a header came from.

    It is a **label for the reader**, not a filter for the detector.  An
    earlier revision treated ``PROJECT`` as "the only kind we may propose",
    on the grounds that we cannot edit ``<vector>``.  That conflated the
    owner of the header with the owner of the edit: the line a removal
    touches is always in the project's own source file, and dropping
    ``#include <iostream>`` from it is a normal, safe change.  Filtering on
    this enum cost the tool every effective suggestion in the controlled
    experiment -- all of them were standard library headers.
    """

    PROJECT = "project"
    SYSTEM = "system"
    EXTERNAL = "external"


# --------------------------------------------------------------------------
# raw, per-translation-unit facts
# --------------------------------------------------------------------------


@dataclass(slots=True)
class IncludeEvent:
    """One ``#include`` edge, exactly as the compiler reported it.

    ``depth`` is the nesting level of the include (0 = included directly by
    the translation unit's source file).  ``line`` is the source line of the
    ``#include`` directive when the compiler tells us; it is ``None`` for
    toolchains that do not report it.
    """

    includer: Path
    included: Path
    depth: int
    line: int | None = None


@dataclass(slots=True)
class PhaseTiming:
    """One ``-ftime-report`` row: a compiler phase and its wall-clock cost."""

    name: str
    wall_seconds: float
    wall_percent: float | None = None
    memory_bytes: int | None = None

    @property
    def is_total(self) -> bool:
        return self.name.strip().lower() == "total"


@dataclass(slots=True)
class TranslationUnit:
    """Everything HeavyHeader learned about compiling one source file."""

    source: Path
    #: Dependency list produced by ``-M``/``-MM`` (flat, deduplicated, order
    #: preserved).  The source file itself is *not* part of this list.
    dependencies: list[Path] = field(default_factory=list)
    #: The include *tree*, as reported by ``-H`` (GCC/Clang) or
    #: ``/showIncludes`` (MSVC).  Kept separate from ``dependencies`` because
    #: the tree carries the nesting information the graph needs.
    include_events: list[IncludeEvent] = field(default_factory=list)
    #: Per-phase timings from ``-ftime-report``.
    timings: list[PhaseTiming] = field(default_factory=list)
    #: 驱动围绕一次编译进程实测的墙钟秒数——**用户真实等待的时间**。
    #: 为什么不直接用 ``-ftime-report`` 的 TOTAL：那个是编译器自报的 CPU
    #: 时间。Windows 上进程启动、杀毒扫描、文件系统开销都不计入 CPU，
    #: 实测同一单元 CPU 0.02s / 墙钟 0.60s（差 30 倍）。「改一行要等多久」
    #: 问的是墙钟，所以增量构建税必须用这个字段；阶段细分仍用 ``timings``。
    #: 0.0 表示没有测量（旧数据或手工构造），调用方须回退到 ``timings``。
    wall_seconds: float = 0.0
    #: Headers the compiler flagged as missing an include guard.
    unguarded_headers: list[Path] = field(default_factory=list)
    #: Timing rows the sanity check rejected as physically impossible.
    #: Non-empty means the compiler's own timer misbehaved (GCC on MinGW
    #: does); the surviving rows are still usable.
    timing_discarded: list[str] = field(default_factory=list)
    #: How many times the driver had to retry *launching* the compiler because
    #: the OS temporarily refused to spawn the process (resource contention
    #: under many parallel compiles). 0 means it started on the first try. A
    #: non-zero value is informational only: the TU still compiled fine, but it
    #: is a signal that this scan was a hair's breadth from miscounting it as a
    #: failure -- see :meth:`heavyheader.drivers.base.CompilerDriver.run`.
    spawn_retries: int = field(default=0)
    #: Non-empty when the compiler refused to compile this file.
    error: str | None = None
    #: The exact :class:`~heavyheader.drivers.base.DriverOptions` this unit was
    #: compiled with, when the scan ran it.  ``None`` means "unknown".
    #:
    #: This exists because of a bug that produced *false* compile errors: on a
    #: project driven by ``compile_commands.json`` every unit carries its own
    #: ``-I`` flags, and a stage that rebuilt a unit from the shared
    #: ``ScanOptions`` instead of the unit's own options silently lost them --
    #: the compiler then could not find the project's headers and every probe
    #: "failed".  The verdict looked like "nothing is removable" when the truth
    #: was "we asked the wrong question".  Carrying the options on the unit
    #: makes the real build reproducible from the scan result alone.
    compile_options: object | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def total_wall_seconds(self) -> float:
        """Total wall time, taken from the ``TOTAL`` row only.

        Deliberately does **not** fall back to summing the phase rows: GCC's
        ``-ftime-report`` phases overlap (``preprocessing`` is a subset of
        ``phase parsing``), so summing them double-counts.

        When the compiler's ``TOTAL`` row is missing or was discarded by the
        sanity check (GCC on MinGW prints impossible values for some
        headers), the driver substitutes the wall clock it measured around
        the process -- see ``GccLikeDriver.interpret``.  So a 0.0 here means
        "never compiled", not "compiled in zero time"; callers that need to
        tell the two apart should check :attr:`timings` directly.
        """
        for timing in self.timings:
            if timing.is_total:
                return timing.wall_seconds
        return 0.0

    def phase_seconds(self, name: str) -> float:
        wanted = name.strip().lower()
        for timing in self.timings:
            if timing.name.strip().lower() == wanted:
                return timing.wall_seconds
        return 0.0

    def template_instantiation_seconds(self) -> float:
        """Seconds spent instantiating templates.

        This is the single most actionable number in the whole report for
        template-heavy C++: a file including ``nlohmann/json.hpp`` spends
        about 39% of its build time here, and no existing tool surfaces that
        figure.  It comes straight from ``-ftime-report``, so it is exact.
        """
        return self.phase_seconds("template instantiation")

    def template_instantiation_percent(self) -> float:
        total = self.total_wall_seconds()
        if total <= 0:
            return 0.0
        return self.template_instantiation_seconds() / total * 100.0

    def total_memory_bytes(self) -> int | None:
        """编译器自报的内存分配峰值（GCC/Clang 的 ``-ftime-report`` GGC 列）。

        ``None`` 表示这个工具链不给这个数（MSVC 没有对应输出）。
        调用方必须区分 ``None`` 与 ``0``——「测不到」不是「不占内存」，
        把 None 当 0 汇报等于编造一个「删除后内存不变」的结论。

        口径说明：GGC 是编译器内部的垃圾回收堆分配，不是进程 RSS。
        它衡量的是「编译器为这个 TU 付出了多少内存」，对头文件成本这个
        问题恰好是对的口径；但它不是操作系统看到的进程峰值内存。
        """
        for timing in self.timings:
            if timing.is_total:
                return timing.memory_bytes
        return None

    def direct_includes(self) -> list[Path]:
        """Headers included *directly* by the source file, first-seen order."""
        seen: dict[Path, None] = {}
        for event in self.include_events:
            if event.depth == 0:
                seen.setdefault(event.included)
        return list(seen)

    def children_of(self, header: Path) -> list[Path]:
        """Headers included directly by ``header`` inside this TU."""
        out: dict[Path, None] = {}
        for event in self.include_events:
            if event.includer == header and event.depth > 0:
                out.setdefault(event.included)
        return list(out)


# --------------------------------------------------------------------------
# derived, project-wide facts
# --------------------------------------------------------------------------


def tu_compile_seconds(tu: "TranslationUnit") -> float:
    """一次编译中**用户实际等待**的秒数：优先驱动实测墙钟，退回自报 TOTAL。

    这个函数存在的理由是把「用户等待」收敛成唯一口径。验证、增量税、累计耗时
    三处都必须调用它；如果各处自己写 ``wall or total``，迟早会漏掉一处——而漏掉
    的表现是数字变小、看起来更好，不会报错，比崩溃更难发现。
    """
    return tu.wall_seconds if tu.wall_seconds > 0 else tu.total_wall_seconds()


@dataclass(slots=True)
class HeaderStats:
    """Aggregated facts about one header across the whole project."""

    path: Path
    kind: HeaderKind = HeaderKind.PROJECT
    line_count: int = 0
    #: Number of translation units that pull this header in, directly or not.
    tu_reach: int = 0
    #: Number of translation units that ``#include`` it directly.
    tu_direct: int = 0
    #: Total number of inclusion events (a header included three times by
    #: different parents counts as three).
    inclusion_events: int = 0
    #: Deepest nesting level at which it was ever included.
    max_depth: int = 0
    #: Headers reachable from this one, excluding itself.
    transitive_headers: int = 0
    #: Lines of source in its transitive closure, excluding itself.
    transitive_lines: int = 0
    #: ``line_count * tu_reach`` -- how much text the project parses because
    #: of this header.
    attributed_lines: int = 0
    #: 增量构建税：改一行这个头文件后，预计要重编的编译单元耗时之和（秒）。
    #: 口径是「扫描期每个 TU 的实测耗时求和」，只含编译、不含链接；它是
    #: *编辑循环* 的等待成本，与 ``attributed_lines`` 的「全量成本」不同：
    #: 一个只在 1 个 TU 用的巨型头，全量成本高、增量税反而低。
    #: 0.0 表示没有可用计时（未编译/计时被丢弃），不是「瞬间完成」。
    incremental_seconds: float = 0.0
    #: Number of ``template<`` declarations in the header.  Textual, so it
    #: counts declarations in disabled ``#if`` branches too -- a known
    #: over-estimate that errs on the side of flagging.
    template_declarations: int = 0
    #: ``template_declarations * tu_reach`` -- the header's template surface
    #: across the project.  A heuristic for *where* template instantiation
    #: cost comes from, not a measurement of it.
    template_surface: int = 0

    @property
    def cost_rank_key(self) -> tuple[int, int, str]:
        """排序键：先按归因行数，再按覆盖 TU 数，最后按路径。

        第三项不是装饰：前两项经常并列（尤其小工程），没有最终 tie-breaker
        时排出来的顺序取决于字典/集合的迭代顺序，而集合里的 Path 受 Python
        哈希随机化影响，**同输入两次运行会给出不同的 Top-10**。A/A 对照实测
        抓到过这个现象；一个会抖动的排行榜不能用来和竞品比一致性。
        """
        return (self.attributed_lines, self.tu_reach, str(self.path))


@dataclass(slots=True)
class RemovalCandidate:
    """A ``#include`` line that might be safe to delete.

    ``estimated_lines_saved`` comes from the exclusive-closure computation;
    ``measured_seconds_saved`` is filled in later by ``heavyheader verify``
    and is the number that actually ends up in the report.
    """

    tu: Path
    header: Path
    line: int | None
    kind: HeaderKind
    #: Headers that become unreachable if this include is dropped.
    exclusive_headers: list[Path] = field(default_factory=list)
    estimated_lines_saved: int = 0
    estimated_percent: float = 0.0
    #: 预计省下的编译时间（秒），按「本编译单元」算，不是全项目。
    #: 公式见 ``graph._estimate_seconds``；``None`` 表示该单元没有可用的
    #: 编译耗时，此时不参与按时间排序（而不是当成 0 混在中间）。
    estimated_seconds_saved: float | None = None
    measured_seconds_saved: float | None = None
    verified: bool = False
    #: 探针结论，``""`` 表示还没探过。取值与 ``verify.VerificationResult.status``
    #: 一致：``improved`` / ``no-change`` / ``compile-error`` / ``not-found`` /
    #: ``ambiguous`` / ``error``。留着它是因为「试过但编译不过」必须能从列表里
    #: 区分出来——否则下一轮还会把同一个候选排到前面，等于没学到任何东西。
    probe_status: str = ""
    #: Result of the textual usage heuristic: "疑似未使用" / "正在使用" /
    #: "无法判定".  A *heuristic*, never a safety guarantee -- see
    #: :mod:`heavyheader.attrib.usage`.
    usage_label: str = "无法判定"
    #: 同一判定的机器可读形式（``plausibly_unused`` / ``in_use`` /
    #: ``unknown``）。排序用它，显示用 ``usage_label``——把中文标签当逻辑
    #: 键会让排序依赖翻译字符串，改一次文案就悄悄改掉一遍排序结果。
    usage_verdict: str = "unknown"
    #: Why we think it is (or is not) safe to remove.
    confidence: str = "unknown"
    notes: list[str] = field(default_factory=list)
    #: 明确结论「值不值得删」，由 ``attrib.advice.build_advice`` 生成。
    #: ``None`` = 还没走到下结论那一步（没跑探针）。它和 ``usage_label`` 的
    #: 区别是根本性的：usage_label 说的是「这个头文件还用不用」（可能性），
    #: advice 说的是「删掉它划不划算」（决定）——后者才是开发者真正问的问题。
    advice: "RemovalAdvice | None" = None
    #: AST 级交叉检查结论（clang-include-cleaner 子进程），默认 None。
    #: 只有显式开启 ``--semantic`` 才填充。它**只增强、不削弱**我们的结论：
    #: 工具把同一个头列为「可删」时作为佐证写进理由；工具沉默（没列为可删）
    #: 不等于「在用」，因此绝不据此降级我们的 REMOVE。详见 attrib/semantic.py。
    semantic: "SemanticVerdict | None" = None
    #: 这一行 ``#include`` 所在的**文件**，它可能不是编译单元自己。
    #: 头文件里的 include 会被几十个 TU 反复解析，删它一行影响的是那一批
    #: TU；只盯着 TU 自己的 include（旧行为）看不到这块空间——实测
    #: nlohmann/json 全项目 83 条 TU 级候选里只有 3 条删得掉，而把 include
    #: 图摊到所有文件后有 432 对。``None`` = 就在 ``tu`` 里，与旧数据兼容。
    site_file: Path | None = None
    #: 删掉这一行后必须重新编译的编译单元（头文件级候选才有多个）。
    #: 空 = 只有 ``tu`` 一个，即旧的 TU 级候选。
    affected_tus: list[Path] = field(default_factory=list)

    @property
    def site(self) -> Path:
        """实际要被改动的那个文件。"""
        return self.site_file if self.site_file is not None else self.tu

    @property
    def is_header_site(self) -> bool:
        """这一行在头文件里（而不是在编译单元的源里）。"""
        return self.site_file is not None and self.site_file != self.tu

    @property
    def reach(self) -> int:
        """有多少个编译单元会因为删掉这一行而重新解析。"""
        return max(1, len(self.affected_tus))

    @property
    def amplified_lines_saved(self) -> int:
        """全项目口径的预估收益：独占行数 × 受影响的编译单元数。

        单 TU 的 ``estimated_lines_saved`` 会系统性低估头文件级改动——
        ``prelude.h`` 里一行 288 行的 include 看着不起眼，但 24 个 TU 每个
        都要少解析这 288 行。同一个数在两种口径下差一个数量级，报告里必须
        标明用的是哪一种。
        """
        return self.estimated_lines_saved * self.reach


# --------------------------------------------------------------------------
# the decision layer: "is this worth removing?"
# --------------------------------------------------------------------------


class AdviceVerdict(enum.Enum):
    """The explicit answer a developer is asking for.

    Three states, deliberately.  The first version of the report answered with
    "疑似未使用" -- a *possibility*, which pushes the decision back onto the
    reader and makes the tool a todo-list generator instead of an advisor.
    Every candidate now lands in exactly one of:

    * ``REMOVE`` -- probed: compiles without it, **and** measured faster
      beyond the noise floor.  "Do this."
    * ``KEEP`` -- the compiler already vetoed it (or we cannot locate the
      line at all).  "Don't bother."
    * ``HUMAN`` -- compiles, but the evidence does not settle the question
      (no measurable win, or never measured).  "Here is both sides; you
      decide" -- with a ``lean`` when the evidence supports one.

    ``HUMAN`` is not a fuzzy state: it is a statement that *this* tool's
    evidence ends here, and the reasons say exactly where.
    """

    REMOVE = "remove"
    KEEP = "keep"
    HUMAN = "human"


class AdviceAspect(enum.Enum):
    """Which dimension of the trade-off a reason speaks to."""

    FUNCTION = "功能影响"
    MAINTENANCE = "维护成本"
    BUILD = "构建体验"


@dataclass(slots=True)
class AdviceReason:
    """One line of the trade-off, attributed to a dimension.

    ``evidence`` carries the number(s) behind the sentence so the report can
    show "独占闭包 12 个头 / 3,400 行" instead of "闭包较大".
    """

    aspect: AdviceAspect
    text: str
    evidence: str = ""


@dataclass(slots=True)
class RemovalAdvice:
    """The verdict plus the reasons it rests on.

    ``lean`` only exists for ``HUMAN``: which way the evidence points when it
    does not reach a verdict.  ``""`` when the evidence points neither way
    (e.g. never measured -- guessing a direction there would be fabrication).

    ``review_gain_lines`` / ``review_gain_share`` are the *arithmetic* behind
    the lean, not decoration: deleting a line costs a review round-trip and
    a test run, so "worth it on maintenance grounds" has to mean a specific
    number of lines the project stops parsing (and what share of this unit
    that is).  A threshold without the number is vibes; the number without
    the threshold is decoration.  Both are carried so a reader can disagree
    with the policy using the same arithmetic the tool used.
    """

    verdict: AdviceVerdict
    reasons: list[AdviceReason] = field(default_factory=list)
    lean: str = ""  # "remove" / "keep" / ""
    review_gain_lines: int = 0
    review_gain_share: float = 0.0

    @property
    def verdict_label(self) -> str:
        return {
            AdviceVerdict.REMOVE: "建议删除",
            AdviceVerdict.KEEP: "保留",
            AdviceVerdict.HUMAN: "需人工判断",
        }[self.verdict]

    def by_aspect(self, aspect: AdviceAspect) -> list[AdviceReason]:
        return [reason for reason in self.reasons if reason.aspect is aspect]


#: 探针结论里表示「这个候选不能按原样采纳」的那些。
_PROBE_FAILURES = frozenset({"compile-error", "not-found", "ambiguous", "error"})


def candidate_rank_key(candidate: RemovalCandidate) -> tuple[int, float]:
    """Ordering for the removal list.  Lower sorts first.

    Tiers exist because *whether the edit survives the compiler* dominates
    *how much it would save*.  The first version sorted purely by estimated
    savings; on nlohmann/json that put a header every unit needs at rank 1,
    and all five of its suggestions failed to compile.  A ranked list whose
    top entries cannot be applied is not a low-precision list, it is a broken
    one -- so the ranking encodes the precedence explicitly:

    ===== ============================================ ===============
    tier  what                                          within-tier order
    ===== ============================================ ===============
    0     probed and measured faster                     seconds saved
    1     heuristic says plausibly unused                 expected seconds
    2     heuristic cannot tell                           expected seconds
    3     heuristic says still in use                     expected seconds
    4     probed and it failed                           (kept for the record)
    ===== ============================================ ===============

    Tier 4 stays in the list on purpose: "we tried this and the compiler said
    no" is a finding worth showing.  It just never outranks a live suggestion.

    With :class:`RemovalAdvice` filled in (post-probe), the tiers collapse
    onto the verdict: ``REMOVE`` is tier 0, a ``HUMAN`` that leans remove is
    tier 1, any other ``HUMAN`` is tier 2, ``KEEP`` joins tier 4.  The
    heuristic tiers remain for scans that never probed.
    """
    advice = candidate.advice
    if advice is not None:
        if advice.verdict is AdviceVerdict.REMOVE:
            return (0, -(candidate.measured_seconds_saved or 0.0))
        if advice.verdict is AdviceVerdict.KEEP:
            return (4, 0.0)
        if advice.lean == "remove":
            return (1, -(candidate.measured_seconds_saved or 0.0))
        return (2, -(candidate.estimated_seconds_saved or 0.0))

    if candidate.probe_status in _PROBE_FAILURES:
        return (4, 0.0)
    if candidate.verified:
        saved = candidate.measured_seconds_saved or 0.0
        return (0, -saved) if saved > 0 else (4, 0.0)

    tier = {"plausibly_unused": 1, "unknown": 2, "in_use": 3}.get(
        candidate.usage_verdict, 2
    )
    estimate = candidate.estimated_seconds_saved
    if estimate is None:
        # No timing for this unit: fall back to the line share so it still
        # lands in a sensible place, but subtracting a tiny epsilon keeps
        # measured candidates ahead of unmeasured ones with the same share.
        estimate = candidate.estimated_percent * 1e-6
    # ``estimated_seconds_saved`` 的口径是**全项目**：TU 级候选只影响一个
    # 单元，两者相等；头文件级候选在生成时就已经把各受影响单元的预估相加
    # （见 attrib.sites），所以这里不能再乘一次 reach，否则同一个放大器
    # 会被算两遍。
    return (tier, -estimate)



@dataclass(slots=True)
class ProjectScan:
    """The whole result of a ``scan`` run."""

    root: Path
    compiler: str
    compiler_family: CompilerFamily
    standard: str | None
    tus: list[TranslationUnit] = field(default_factory=list)
    header_stats: dict[Path, HeaderStats] = field(default_factory=dict)
    candidates: list[RemovalCandidate] = field(default_factory=list)
    #: Sum of ``tu.total_wall_seconds()`` -- the baseline the savings
    #: percentages are relative to.
    baseline_seconds: float = 0.0
    #: 项目内每个文件只计一次的行数总和（被追踪的头文件 + 编译单元源文件）。
    #: 这是 ``blowup_factor()`` 的分母，口径与 Header Hero 的 "Total Lines"
    #: 一致：唯一文件，不按包含次数累加。
    total_project_lines: int = 0
    #: Template instantiation cost profile.  Filled in by the scanner; the
    #: per-TU numbers are exact, the per-header surface is a heuristic.
    template_profile: object | None = None
    #: Precompiled-header recommendation, only when ``--pch`` is passed.
    pch: object | None = None
    #: Forward-declaration candidates, only when ``--forward-decl`` is passed.
    forward_candidates: list = field(default_factory=list)
    #: 探针/测量阶段的结果，``--probe`` 时才有内容。None = 这一轮没跑实测，
    #: 报告必须把这种情况说明白，不能让读者以为「没测」等于「没问题」。
    probe_results: list | None = None
    warnings: list[str] = field(default_factory=list)
    #: 增量扫描统计。未启用缓存时为 None；有了它 CLI 能说明「复用了多少、
    #: 重编了多少」，让用户确认增量确实生效，而不是只看总耗时变短。
    cache_stats: object | None = None
    #: 复现本次扫描的完整命令。报告里的每条实测结论都必须能让读者在自己
    #: 机器上重跑一遍——这是「实测数据」与「宣传数字」的分界线。由 CLI 或
    #: 实验脚本设置；为空表示没有记录（旧数据/单元测试构造的 scan）。
    reproduce_command: str = ""

    # -- convenience accessors -------------------------------------------

    @property
    def ok_tus(self) -> list[TranslationUnit]:
        return [tu for tu in self.tus if tu.ok]

    @property
    def failed_tus(self) -> list[TranslationUnit]:
        return [tu for tu in self.tus if not tu.ok]

    def headers_by_cost(self) -> list[HeaderStats]:
        return sorted(
            self.header_stats.values(),
            key=lambda h: h.cost_rank_key,
            reverse=True,
        )

    def total_dependency_edges(self) -> int:
        return sum(len(tu.include_events) for tu in self.ok_tus)

    def total_included_lines(self) -> int:
        """Sum of ``attributed_lines`` -- total header text parsed project-wide."""
        return sum(h.attributed_lines for h in self.header_stats.values())

    def blowup_factor(self) -> float:
        """``Total Parsed / Total Lines`` -- 平均每行被解析多少次。

        公式与 Header Hero（Bitsquid，2011）的 Blowup Factor 相同，来源与
        差异见 ``docs/competitors.md``：分子是「头文件行数 × 被包含次数」的
        全项目求和（本工具的包含次数取自真实编译器的 ``-H`` 依赖树，而不是
        对 ``#include`` 的正则扫描），分母是项目内唯一文件的行数总和。

        返回 0.0 表示分母缺失（旧数据或未统计），而不是「没有放大」——调用方
        必须区分这两种情况，不能把 0.0 当成好消息渲染。
        """
        if self.total_project_lines <= 0:
            return 0.0
        return self.total_included_lines() / self.total_project_lines

    def candidates_by_estimate(self) -> list[RemovalCandidate]:
        """Candidates in the order they should be acted on.

        The ordering rules live in :func:`candidate_rank_key` so that the list
        produced by ``graph.find_removal_candidates`` and the one shown by the
        reports can never disagree.  They used to be two separate sorts, and
        the report's copy put "plausibly unused" above a *measured* win --
        i.e. a guess outranked a measurement.
        """
        return sorted(self.candidates, key=candidate_rank_key)
