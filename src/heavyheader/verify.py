"""Empirical verification: remove an include, recompile, measure.

Everything else in HeavyHeader is a *model* -- a well-founded one, but still a
model.  This module is where the tool earns the right to put numbers in a
report: it actually performs the edit, actually recompiles, and actually
measures the difference.

Why this matters for credibility:

* A cost model can be wrong in interesting ways.  A wall-clock measurement
  cannot.
* "This include is unused" is a claim about semantics that we cannot make
  without full AST analysis.  "This translation unit compiles in 0.31s without
  this include and 0.52s with it" is a claim anyone can reproduce.

The measurement protocol is deliberately simple and deliberately honest:

* The include line is removed by *commenting it out* rather than deleting it,
  so the experiment is trivially reversible and the diff is reviewable.
* Each side is compiled ``runs`` times and the **minimum** is taken.  The
  minimum is the standard robust estimator for timing: noise only ever makes
  a measurement slower, never faster, so the minimum is the closest available
  approximation of the true cost.
* If the modified file no longer compiles, that is recorded as a finding
  ("cannot remove: compile error"), not silently dropped.  A tool that hides
  its failures is worse than no tool.
"""

from __future__ import annotations

import contextlib
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from . import codegen
from . import includes as _includes
from .attrib.advice import (
    AGGREGATE_NOISE_FLOOR,
    TIER_COMPILES,
    TIER_EQUIVALENT,
    TIER_VERIFIED,
    TIME_NOISE_FLOOR,
    build_advice,
)
from .attrib.semantic import SemanticBackend, SemanticVerdict
from .drivers.base import CompilationError, CompilerDriver, DriverOptions
from .models import (
    ProjectScan,
    RemovalCandidate,
    candidate_rank_key,
    tu_compile_seconds,
)
from .scanner import default_include_dirs

# 行号定位只有一份实现（``heavyheader.includes``）。下面这些别名是内部与测试
# 用的历史名字，保留它们是为了不把「重命名」和「改算法」混在一次提交里。
_INCLUDE_RE = _includes.INCLUDE_RE
_BOM = _includes.BOM
_strip_bom = _includes.strip_bom
_strip_comments = _includes.strip_comments
_path_matches = _includes.path_matches
find_include_lines = _includes.find_include_lines
find_include_line = _includes.find_include_line

__all__ = [
    "VerifyOptions",
    "VerificationResult",
    "order_for_measurement",
    "verify_candidates",
    "find_include_line",
    "find_include_lines",
    "locate_include_line",
    "IncludeLocation",
]


@dataclass(slots=True)
class VerifyOptions:
    """Knobs for the verification pass.

    Two stages, deliberately separated because they cost very different
    amounts and answer different questions:

    * **probe** -- compile the unit *with the include removed*, once.  It
      answers "does this edit even compile?" for the price of one compile
      (0.3-2.5 s on a real project).  Rejecting a candidate here is cheap and
      is what makes the whole pipeline affordable.
    * **measure** -- compile both sides ``runs`` times and keep the minimum.
      It answers "and is it actually faster?", which needs a baseline on the
      same machine in the same run.

    The first version ran only the second stage, on the top five candidates
    the cost model happened to rank first -- so a broken ranking produced five
    failed builds and no way to tell "the ranking is wrong" from "the project
    has nothing to remove".  Probing first turns that into five cheap
    rejections plus an honest statement about the candidate pool.
    """

    top: int = 5
    runs: int = 3
    #: 复测遍数。``1`` = 不回测（旧行为）。
    #:
    #: 为什么需要它：G2 实测发现，同一候选连测三次，percent_saved 在 1.0%–2.1%
    #: 之间摆动，``improved`` 出现 1/3 次；第一轮判为 improved 的候选复测
    #: 0/4 复现。单遍判定在阈值附近等于抽签。设成 2 表示「improved 之后必须
    #: 再独立测一遍，仍 improved 才保留结论」，否则降级为 no-change 并写明原因。
    confirm_passes: int = 1
    #: Compiles per side during the probe stage.  1 is enough: a compile error
    #: is deterministic, and a *time* claim is what ``runs`` is for.
    probe_runs: int = 1
    timeout: float = 300.0
    #: Keep the patched sources around for inspection.
    keep_artifacts: bool = False
    #: Wall-clock budget for the whole pass, in seconds.  ``0`` = unlimited.
    #: The pass stops cleanly at a candidate boundary, never mid-compile, and
    #: reports how far it got -- a truncated honest result beats an aborted one.
    budget_seconds: float = 0.0
    #: Stop after the probe stage (do not pay for full measurements).  Answers
    #: "which of these are even compilable" at the lowest possible cost.
    probe_only: bool = False
    #: Parallel compiles.  Probes are independent subprocesses, so threads give
    #: real concurrency -- this is the difference between 2 minutes and 20
    #: seconds on a 200-candidate pool.
    jobs: int = 4
    #: 计时阶段的并发度。**默认 1（串行）**，与上面的探针并发分开。
    #:
    #: 为什么必须分开：这台机器上一次编译的墙钟 215 ms 里只有 29 ms 是编译器
    #: 自己的（其余是进程启动 + 文件系统），并发把每个编译都拖慢 20–25%，
    #: 且拖慢量随负载抖动。实测同一批 4 条「删了就省 3–4%」的候选：
    #: 串行 4/4 复现；``jobs=4`` 时 0/4 复现（基线从 215 ms 涨到 270 ms，
    #: 第一遍 percent 6.7% / 第二遍 1.3%，稳定性全毁）。探针阶段只回答
    #: 「编不编得过」，并发无害；计时阶段回答「省多少」，并发就是噪声源。
    timing_jobs: int = 1
    #: 代码等价校验：删掉这一行之后，编出来的还是不是同一个程序。
    #:
    #: 为什么需要：2026-10-19 实测里，删掉 ``<iomanip>`` 后编译通过，但目标文件
    #: 从 21,289 字节缩到 10,183 字节——重载解析变了。只判「能编过」的工具会把
    #: 这种改动当成可删建议发出去。开启后，探针阶段会多编译两次（原始 + 补丁，
    #: 编到汇编比哈希），代价约等于一次探针，换的是「建议删除」从「能编」升级为
    #: 「代码没变」。默认关闭：它属于更强的证据，而不是更快的默认。
    codegen_check: bool = False
    #: Optional test hook, called as ``test_runner(source_path) -> (ok, detail)``.
    #:
    #: Why a callback and not a command-line flag: the patched source lives in
    #: a temp directory, so a user-supplied "run my tests" command could never
    #: see the experiment -- and editing the user's real files to make it
    #: visible would break the one promise ``verify`` has never broken (the
    #: project's sources are not touched).  The caller -- an experiment
    #: harness, an IDE, a build-system integration -- knows how to compile and
    #: run the patched copy, so it supplies the runner.
    #:
    #: When set, every ``improved`` candidate gets one more evidence grade:
    #: the test suite is run against the patched unit.  A failing test
    #: demotes the verdict; a passing one is quoted in the advice.
    test_runner: object | None = None
    #: 测量顺序。``"rank"`` = 按候选排序键（疑似未使用优先，默认行为）；
    #: ``"estimate"`` = 只按预估收益降序。
    #:
    #: 为什么要有第二种：**已验证层级**里文本判据没有投票权。实测（Lua）8 条
    #: 落地收益里 7 条被判成 ``in_use`` / ``unknown``——按判据排序时它们排在
    #: tier 3，``--top 20`` 根本轮不到，于是省时最多的建议永远不被测量。
    #: 按预估收益排是这一层能做的最中立的排序：它不采信判据，也不假装知道。
    order: str = "rank"


def order_for_measurement(
    candidates: list[RemovalCandidate], order: str
) -> list[RemovalCandidate]:
    """按 ``order`` 排测量顺序。抽成函数是为了能单独测排序规则本身，
    不必真的编译一遍。"""
    if order == "estimate":
        # 预估缺失的排最后，但仍留在池里：排最后不等于「没有机会」。
        return sorted(
            candidates,
            key=lambda c: (c.estimated_seconds_saved is None,
                           -(c.estimated_seconds_saved or 0.0)),
        )
    return sorted(candidates, key=candidate_rank_key)


@dataclass(slots=True)
class VerificationResult:
    """Outcome of trying to remove one include."""

    candidate: RemovalCandidate
    #: improved / no-change / compile-error / not-found / ambiguous / error
    status: str
    baseline_seconds: float | None = None
    patched_seconds: float | None = None
    message: str = ""
    #: 编译器自报的 GGC 内存分配峰值（``-ftime-report`` 的 TOTAL 行），
    #: 单位字节。``None`` = 该工具链不提供（MSVC）或未测。**不是进程 RSS**，
    #: 报告里必须带上这个口径说明，否则读者会拿它跟任务管理器比。
    baseline_memory_kb: int | None = None
    patched_memory_kb: int | None = None
    #: 目标文件大小（一次完整编译实测）。``None`` = 未测（只在实测有收益的
    #: 候选上才值得多花两次完整编译）。0 字节与 None 是两回事。
    baseline_object_bytes: int | None = None
    patched_object_bytes: int | None = None
    #: 测试证据等级。``""`` = 没跑测试（未配置 runner，或候选没有构建收益，
    #: 跑一次测试的代价换不来证据）；``passed`` / ``failed`` = 补丁版测试
    #: 的结局；``error`` = 基线版测试就没过——那是测试环境或基线的问题，
    #: **不能记到候选头上**，但也不能拿它当证据。
    test_status: str = ""
    test_detail: str = ""
    #: 任一侧的计时来自墙钟回退而非编译器自报时，这里写明原因。
    #: 空串 = 两侧都是编译器自己的 TOTAL 行。带着它是因为两种计时口径
    #: 不同，读者有权知道刚看到的那个秒数是谁量的。
    timing_note: str = ""
    #: 代码等价闸门**是否真的跑过**。
    #:
    #: 与「本轮有没有开 ``--check-codegen``」是两件事：关掉开关是性能选择，
    #: 但不能因为省了时间就声称验过等价性。库调用默认 False（CLI 的
    #: ``verify`` 默认是开的）——没验过就不许说「程序没变」。
    codegen_checked: bool = False
    #: 闸门跑过时的结论：``True`` = 汇编指纹一致（程序等价已证）；``False`` =
    #: 指纹不同（已转成 ``codegen-changed``，留着便于追溯）；``None`` = 没跑过。
    codegen_identical: bool | None = None
    #: 复测仍判 improved 的遍数（0 = 没复测）。收益结论的强度就挂在这个数上：
    #: 0 遍的「省了 x%」与 2 遍仍成立的「省了 x%」不该用同一句话讲。
    confirm_passes: int = 0

    @property
    def seconds_saved(self) -> float | None:
        if self.baseline_seconds is None or self.patched_seconds is None:
            return None
        return self.baseline_seconds - self.patched_seconds

    @property
    def percent_saved(self) -> float | None:
        saved = self.seconds_saved
        if saved is None or not self.baseline_seconds:
            return None
        return saved / self.baseline_seconds * 100.0

    @property
    def evidence_tier(self) -> str:
        """这条结论站在哪一层证据上（常量见 :data:`TIER_*`）。

        分层的起因：usage 判据是文本推断，实测是编译器给的证据，两者的可信度
        不是一个量级，但旧界面把它们混在同一句「建议删除」里。分层之后，
        「文本判不了、但汇编指纹证明等价」这类候选不再被当成可疑项。

        反过来同样要紧：**没跑闸门就不能站到上面两层**。闸门在 CLI 里默认开，
        但库调用默认关、MSVC 也给不出汇编——这几种情况下这里只返回
        ``compiles_only``。
        """
        if self.status in ("compile-error", "ambiguous", "not-found", "error"):
            return ""
        if self.codegen_checked and self.codegen_identical:
            # 闸门通过 = 删掉这一行之后编出来的目标代码字节级一致。
            # 「程序没变」到此已被证明；剩下的只是「值不值得删」。
            return TIER_VERIFIED if self.status == "improved" else TIER_EQUIVALENT
        if self.status in ("improved", "no-change", "compiles"):
            return TIER_COMPILES
        return ""


# --------------------------------------------------------------------------
# locating and patching the include line
# --------------------------------------------------------------------------

class IncludeLocation(NamedTuple):
    """Where the ``#include`` for a header lives, and how sure we are."""

    line: int | None
    #: ""（已定位）/ "not-found" / "ambiguous"
    status: str
    #: 文本匹配到的行数。歧义时用来把情况说清楚，而不是含糊一句「找不到」。
    matches: int


def locate_include_line(
    source_text: str, header: Path, hint_line: int | None = None
) -> IncludeLocation:
    """Pin down which line to comment out.

    ``hint_line`` 是编译器上报的行号（``RemovalCandidate.line``，来自 ``-H``
    或 ``/showIncludes``）。它是权威数据：哪个头被包含、在第几行，编译器比
    正则清楚。所以先用它，再用文本匹配兜底（MSVC 等工具链不报行号）。

    用 hint 之前会校验那一行确实是 include 且名字对得上——扫描之后源码可能
    被改动过，行号会错位，盲信一个失效的行号比不用更糟。

    文本匹配出现同名冲突时报告 ``ambiguous``：**不猜**。删错一行的代价是
    verify 给出一条看起来可信、实际指向别处的结论。
    """
    if hint_line is not None:
        lines = source_text.splitlines()
        if 1 <= hint_line <= len(lines):
            # 带 BOM 的文件首行以 \ufeff 开头，它不是 \s，正则匹配不到。
            match = _INCLUDE_RE.match(_strip_bom(lines[hint_line - 1]))
            if match and Path(match.group("target")).name == header.name:
                return IncludeLocation(hint_line, "", 1)

    matches = find_include_lines(source_text, header)
    if not matches:
        return IncludeLocation(None, "not-found", 0)
    if len(matches) > 1:
        return IncludeLocation(None, "ambiguous", len(matches))
    return IncludeLocation(matches[0], "", 1)


def _forced_codegen(candidate) -> bool:
    """这条候选**必须**拿到汇编指纹才允许下结论，开关说了不算。

    两类：文本层怀疑「被包含时执行」的副作用；以及实现片段的一次实例化——
    后者删掉几乎总能编过（只是少一个函数定义），「编译通过」在这里根本不是
    证据。关掉 ``--check-codegen`` 是性能选择，不是允许在没证据时下结论。
    """
    return candidate.side_effect_suspected or candidate.instantiation


def _codegen_fields(pair: tuple[object, object] | None) -> dict[str, object]:
    """把闸门结论摊成 ``VerificationResult`` 的字段。

    闸门结果以前只用一次（判 ``codegen-changed``）就丢掉，下游于是分不清
    「验过且等价」与「根本没验」。现在每条结论都带着「验没验、验出什么」，
    :attr:`VerificationResult.evidence_tier` 才有依据。
    """
    if pair is None:
        return {}
    before, after = pair
    if not before.ok or not after.ok:
        # 拿不到指纹就是**没验过**，不是「验出不同」。三者必须分开：
        # 混成 False 会让「闸门不可用」被当成「发现危险改动」。
        return {"codegen_checked": True, "codegen_identical": None}
    return {"codegen_checked": True, "codegen_identical": before.same_as(after)}


def _comment_out(source_text: str, line_number: int) -> str:
    lines = source_text.splitlines()
    index = line_number - 1
    if not 0 <= index < len(lines):
        return source_text
    target = lines[index]
    # BOM 待在文件最前面：注释标记插到它后面，否则补丁文件会丢掉 BOM，
    # 而「实验必须可逆」连编码头都不该动。
    bom = ""
    if target.startswith(_BOM):
        bom, target = _BOM, target[len(_BOM) :]
    indent = len(target) - len(target.lstrip())
    lines[index] = bom + " " * indent + "// [heavyheader] " + target.lstrip()
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------


def _measure(
    driver: CompilerDriver,
    source: Path,
    options: DriverOptions,
    cwd: Path | None,
    runs: int,
    timeout: float,
) -> tuple[float | None, int | None, str | None, tuple[str, ...]]:
    """Compile ``source`` ``runs`` times.

    Returns ``(min_seconds, memory, error, discarded_timings)``.

    ``min_seconds`` is the fastest run's **wall clock** (driver-measured),
    falling back to the compiler's TOTAL only when the wall reading is
    missing -- the same rule as :func:`heavyheader.models.tu_compile_seconds`,
    so verify's savings and the scan headline cannot drift apart.

    ``memory`` is the compiler-reported GGC allocation peak of the best run
    (bytes), or ``None`` when the toolchain does not report one.  It is kept
    beside the timing rather than folded into it: a candidate can save wall
    time without saving a byte of allocation, and the advice must be able to
    say so honestly.

    ``discarded_timings`` is what the sanity check threw away for the best
    run -- non-empty means the compiler's own timer misbehaved (GCC on MinGW
    prints impossible values for some headers) and the surviving TOTAL came
    from the wall-clock fallback.  It travels with the number so no reader
    mistakes a wall-clock reading for a compiler-internal one.
    """
    best: float | None = None
    best_memory: int | None = None
    best_discarded: tuple[str, ...] = ()
    for _ in range(max(1, runs)):
        try:
            tu = driver.run(source, options, cwd=cwd, timeout=timeout)
        except CompilationError as exc:
            return None, None, str(exc), ()
        if not tu.ok:
            return None, None, tu.error or "compile failed", ()
        # 与 scan.baseline_seconds 同一口径：用户等待的是墙钟。取多次运行的
        # 最小值是为了压噪声（最小值对偶发卡顿不敏感），但量纲必须是墙钟，
        # 否则报告里的「节省 30 ms」与用户实际等待对不上。
        seconds = tu_compile_seconds(tu)
        if best is None or seconds < best:
            best = seconds
            best_memory = tu.total_memory_bytes()
            best_discarded = tuple(tu.timing_discarded)
    return best, best_memory, None, best_discarded


def _object_size(
    driver: CompilerDriver,
    source: Path,
    options: DriverOptions,
    timeout: float,
) -> int | None:
    """Full-compile ``source`` to a temp object file and return its size.

    Returns ``None`` -- never a guess -- when the compile fails or produces
    no file.  A ``None`` here means the size dimension is simply absent from
    the advice; inventing "unchanged" from a failed measurement is exactly
    the kind of number this tool exists to not produce.
    """
    with tempfile.TemporaryDirectory(prefix="heavyheader-size-") as tmp:
        target = Path(tmp) / "probe.o"
        sized = replace(options, output_path=target)
        try:
            tu = driver.run(source, sized, cwd=None, timeout=timeout)
        except CompilationError:
            return None
        if not tu.ok or not target.is_file():
            return None
        try:
            return target.stat().st_size
        except OSError:
            return None


def _unit_options_for_path(
    scan: ProjectScan,
    unit_source: Path,
    fallback: DriverOptions,
    effective_includes: list[Path],
) -> DriverOptions:
    """The options one specific unit was *actually* compiled with.

    Split out from :func:`_unit_options` because a header-level candidate has
    **many** units to rebuild, and looking them up through the candidate would
    silently give every one of them the representative unit's flags.
    """
    for tu in scan.ok_tus:
        if tu.source == unit_source:
            recorded = getattr(tu, "compile_options", None)
            if recorded is not None:
                return recorded
            break
    return replace(fallback, include_dirs=list(effective_includes))


def _unit_options(
    scan: ProjectScan,
    candidate: RemovalCandidate,
    fallback: DriverOptions,
    effective_includes: list[Path],
) -> DriverOptions:
    """The options this candidate's unit was *actually* compiled with.

    ``compile_commands.json``-driven projects give every unit its own ``-I``
    flags.  Rebuilding a unit from the shared ``ScanOptions`` drops them, the
    compiler cannot find the project's headers, and every probe comes back
    "compile error" -- a verdict that looks like "nothing is removable" when
    the truth is "we asked the wrong question".  The scan records each unit's
    options on the TU (``TranslationUnit.compile_options``) precisely so this
    stage can reproduce the same build.

    Note the lookup is by *path*: ``RemovalCandidate.tu`` holds the source
    path, not the ``TranslationUnit``.  Reading the attribute straight off the
    candidate silently returns ``None`` on every call, which is exactly how
    this bug survived a test suite -- the fallback is plausible enough that
    nothing crashes, it just answers the wrong question.

    When the unit carries no recorded options (a hand-built scan, a test), the
    caller's options are used with the layout fallback applied -- the old
    behaviour, preserved for the case where it is the only information there
    is.
    """
    return _unit_options_for_path(scan, candidate.tu, fallback, effective_includes)


@contextlib.contextmanager
def _patched_in_place(path: Path, line_number: int):
    """Comment out one line of ``path``, then put it back. Guaranteed.

    Header-level edits cannot use the temp-copy trick the TU-level path uses:
    a header is found by include-path resolution, so a copy in a temp
    directory would simply never be opened.  The real file has to change, and
    that makes the restore the most important line in this function -- an
    exception halfway through a measurement that leaves a project header with
    a commented-out ``#include`` is worse than no answer at all.

    Hence ``try/finally`` plus an explicit re-read check: if the restore did
    not take, the file is written again from the in-memory original, and only
    a failure of *that* is reported as an error.  deheader has always worked
    this way (edit, ``make``, restore); the difference here is that we restore
    unconditionally rather than on success.
    """
    original = path.read_text(encoding="utf-8", errors="replace")
    try:
        path.write_text(_comment_out(original, line_number), encoding="utf-8")
        yield
    finally:
        try:
            path.write_text(original, encoding="utf-8")
        except OSError:
            pass
        if path.read_text(encoding="utf-8", errors="replace") != original:
            path.write_text(original, encoding="utf-8")


def _patched_options(
    unit_options: DriverOptions,
    effective_includes: list[Path],
    source_path: Path,
    output_path: Path | None = None,
) -> DriverOptions:
    """Options for compiling the *patched* copy of ``source_path``.

    A quoted include resolves against the including file's directory, so a
    file compiled from a temp directory would lose access to its siblings.
    The original directory is added explicitly.

    ``output_path`` is for the object-size measurement: everything else
    stays identical so the size comparison means something.
    """
    return DriverOptions(
        standard=unit_options.standard,
        include_dirs=[*effective_includes, source_path.parent],
        defines=list(unit_options.defines),
        extra_flags=list(unit_options.extra_flags),
        full_compile=unit_options.full_compile,
        output_path=output_path,
    )


class _BaselineCache:
    """One baseline measurement per *unit*, shared by every candidate in it.

    The first version re-measured the unmodified file for each candidate, so a
    unit with six candidates paid for six identical baseline compiles.  The
    baseline does not depend on the candidate -- only on the unit and the
    machine state -- so it is measured once and reused.
    """

    def __init__(self) -> None:
        self._values: dict[Path, float | None] = {}
        self._memory: dict[Path, int | None] = {}
        self._discarded: dict[Path, tuple[str, ...]] = {}
        self.compiles = 0

    def get(
        self,
        driver: CompilerDriver,
        source_path: Path,
        options: DriverOptions,
        runs: int,
        timeout: float,
    ) -> float | None:
        if source_path in self._values:
            return self._values[source_path]
        seconds, memory, _error, discarded = _measure(
            driver, source_path, options, None, runs, timeout
        )
        self._values[source_path] = seconds
        self._memory[source_path] = memory
        self._discarded[source_path] = discarded
        self.compiles += max(1, runs)
        return seconds

    def memory(self, source_path: Path) -> int | None:
        return self._memory.get(source_path)

    def discarded(self, source_path: Path) -> tuple[str, ...]:
        return self._discarded.get(source_path, ())


def _verify_header_site(
    scan: ProjectScan,
    driver: CompilerDriver,
    options: DriverOptions,
    effective_includes: list[Path],
    verify: VerifyOptions,
    entries: list[tuple[RemovalCandidate, Path, int]],
    baselines: _BaselineCache,
) -> list[VerificationResult]:
    """Verify candidates whose ``#include`` lives in a header.

    One edit, many units.  The unit of measurement is therefore the **sum**
    over every unit that parses the file: a line that saves 0.2% in each of
    24 units is invisible one unit at a time and worth 0.2% of the whole
    build in aggregate.  Reporting it per unit would be reporting noise;
    reporting it per edit is what the developer actually did.

    Sequential by construction.  The edit is made in place (see
    :func:`_patched_in_place`), so two candidates in the same header running
    concurrently would patch and restore each other's source.  That is a
    correctness constraint, not a preference: no amount of parallelism is
    worth a measured result built on the wrong file.

    Every affected unit must compile.  Passing 23 of 24 is a failure: the
    one that broke is a unit that was silently relying on symbols arriving
    through this header, which is exactly the regression this stage exists to
    catch.
    """
    results: list[VerificationResult] = []

    def unit_options(unit_source: Path) -> DriverOptions:
        return _unit_options_for_path(scan, unit_source, options, effective_includes)

    def compile_all(units: list[Path], runs: int) -> tuple[float | None, str | None]:
        """Compile every unit, return the total; ``None`` if any one fails."""
        total = 0.0
        for unit in units:
            seconds, _memory, error, _discarded = _measure(
                driver,
                unit,
                _patched_options(unit_options(unit), effective_includes, unit),
                None,
                runs,
                verify.timeout,
            )
            if seconds is None:
                return None, f"{unit.name}: {error}"
            total += seconds
        return total, None

    def compile_one(unit: Path) -> tuple[float | None, str | None]:
        seconds, _memory, error, _discarded = _measure(
            driver,
            unit,
            _patched_options(unit_options(unit), effective_includes, unit),
            None,
            1,
            verify.timeout,
        )
        return seconds, error

    def measure_interleaved(
        units: list[Path], site_path: Path, line_number: int, runs: int
    ) -> tuple[float | None, float | None, str | None]:
        """交替测一批单元的两侧，各取最小，再分别求和。

        为什么不「先测完所有基线、再打补丁测所有单元」：那样两次测量相隔
        几分钟，机器漂移会整块落到差值上。实测同一批 24 个单元用老协议
        差 2.005%，改成交替后 0.644% / 0.245%（见
        ``experiments/g2_interleaved.py``）。这一条决定了 1% 量级收益能不能
        被判定，也就决定了 G2 能不能成立。

        代价是每个单元要来回改头文件 2×runs 次。文件写入比编译便宜两个数
        量级，这个交换是划算的。
        """
        base_total = 0.0
        patched_total = 0.0
        for unit in units:
            best_base: float | None = None
            best_patched: float | None = None
            for _ in range(runs):
                base, error = compile_one(unit)
                if base is None:
                    return None, None, f"{unit.name}: {error}"
                with _patched_in_place(site_path, line_number):
                    patched, error = compile_one(unit)
                if patched is None:
                    return None, None, f"{unit.name}: {error}"
                best_base = base if best_base is None else min(best_base, base)
                best_patched = (
                    patched if best_patched is None else min(best_patched, patched)
                )
            base_total += best_base or 0.0
            patched_total += best_patched or 0.0
        return base_total, patched_total, None

    for candidate, site_path, line_number in entries:
        units = list(candidate.affected_tus) or [candidate.tu]

        # 先花一遍编译问「能不能编过」。24 个单元里第 20 个挂掉时，前面 19
        # 个已经各跑了 runs 次——先探一遍能把这种浪费压到 1 次编译。
        probe_error: str | None = None
        with _patched_in_place(site_path, line_number):
            _probe_total, probe_error = compile_all(units, 1)
        if probe_error:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="compile-error",
                    message=f"移除后编译失败（{len(units)} 个单元之一）：{probe_error}",
                )
            )
            continue
        if verify.probe_only:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="compiles",
                    message=f"移除后 {len(units)} 个编译单元全部通过（未做耗时测量）",
                )
            )
            continue

        baseline_total, patched_total, measure_error = measure_interleaved(
            units, site_path, line_number, verify.runs
        )
        if baseline_total is None or patched_total is None:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message=f"测量阶段编译失败：{measure_error}",
                )
            )
            continue

        saved = baseline_total - patched_total
        meaningful = (
            baseline_total > 0
            and abs(saved) / baseline_total >= AGGREGATE_NOISE_FLOOR
        )
        status = "improved" if saved > 0 and meaningful else "no-change"
        result = VerificationResult(
            candidate=candidate,
            status=status,
            baseline_seconds=baseline_total,
            patched_seconds=patched_total,
            message=(
                f"{len(units)} 个单元合计 {baseline_total * 1000:.0f} ms → "
                f"{patched_total * 1000:.0f} ms"
                if meaningful
                else f"差异在噪声范围内（{saved * 1000:+.0f} ms / {len(units)} 个单元）"
            ),
        )

        # 与 TU 级同一条纪律：判为 improved 之后必须复测。这里复测的是
        # 「整批单元」，成本是 runs × 单元数，所以只对已经 improved 的做。
        if status == "improved" and verify.confirm_passes > 1:
            percents = [result.percent_saved or 0.0]
            confirmed = True
            for _ in range(verify.confirm_passes - 1):
                fresh_base, base_error = compile_all(units, verify.runs)
                if fresh_base is None:
                    confirmed = False
                    result.message += f"；复测基线编译失败（{base_error}）"
                    break
                with _patched_in_place(site_path, line_number):
                    fresh_patched, patched_error = compile_all(units, verify.runs)
                if fresh_patched is None:
                    confirmed = False
                    result.message += f"；复测补丁编译失败（{patched_error}）"
                    break
                percent = (fresh_base - fresh_patched) / fresh_base * 100 if fresh_base else 0.0
                percents.append(percent)
                if percent < AGGREGATE_NOISE_FLOOR * 100:
                    confirmed = False
            if not confirmed:
                result.status = "no-change"
                result.message += (
                    "；复测未通过，各遍 percent_saved = "
                    + " / ".join(f"{value:.2f}%" for value in percents)
                    + f"（阈值 {AGGREGATE_NOISE_FLOOR * 100:.0f}%）"
                )
        results.append(result)
        candidate.measured_seconds_saved = max(0.0, saved)
        candidate.verified = True

    return results


def verify_candidates(
    scan: ProjectScan,
    driver: CompilerDriver,
    options: DriverOptions,
    *,
    verify: VerifyOptions | None = None,
    include_dirs: list[Path] | None = None,
    semantic: SemanticBackend | None = None,
    build_path: Path | None = None,
) -> list[VerificationResult]:
    """Probe then measure the removal candidates, in rank order.

    ``options`` should be the same options used to produce ``scan`` -- the
    comparison is only meaningful if both sides are compiled identically.

    Stages, cheapest first:

    1. locate the ``#include`` line (a candidate we cannot pin down is
       reported as ``ambiguous`` / ``not-found``, never guessed at);
    2. **probe** -- compile with it removed, once.  Most bad candidates die
       here, at the cost of one compile;
    3. **measure** -- for survivors only, compile both sides ``runs`` times
       and keep the minimum.  Baselines are cached per unit, so several
       candidates from one file share a single baseline.

    Results are written back into the candidates (``probe_status``,
    ``measured_seconds_saved``, ``verified``) so the report can show the
    estimate and the measurement side by side instead of only the estimate.
    """
    verify = verify or VerifyOptions()

    # 有效包含路径 = 扫描时用的（options）+ 调用方额外给的（kwarg）；
    # 两者都为空时套用与 scan 相同的布局兜底。
    #
    # 以前基线编译直接透传 options（无兜底），而打补丁那侧又改用 kwarg、
    # 丢掉了 options 里的 -I。两个后果：include/ 布局的项目基线就编译不过，
    # 以及两侧口径不一致导致「移除后编译失败」的误报。
    # 这里保留一份兜底包含路径，但不再用它覆盖单元自己的参数：
    # compile_commands.json 驱动的项目里，单元自带的 -I 才是真实构建的那一套。
    effective_includes = list(options.include_dirs) + list(include_dirs or [])
    if not effective_includes:
        effective_includes = default_include_dirs(scan.root)

    limit = verify.top if verify.top and verify.top > 0 else len(scan.candidates)
    ranked = order_for_measurement(list(scan.candidates), verify.order)[: max(0, limit)]

    # ---------------------------------------------------------------- semantic
    # 可选 AST 交叉检查（ROADMAP v0.3 路线 1）：对每个出现候选的单元跑一次
    # clang-include-cleaner，结果缓存 per unit。跑不到（工具缺失 / 该单元分析
    # 失败）就留 None——build_advice 据此只写「未能提供」，绝不凭沉默降级结论。
    semantic_by_unit: dict[Path, SemanticVerdict | None] = {}
    if semantic is not None and semantic.available and build_path is not None:
        seen: set[Path] = set()
        for candidate in ranked:
            unit = candidate.tu
            if unit in seen:
                continue
            seen.add(unit)
            try:
                semantic_by_unit[unit] = semantic.analyse(unit, build_path)
            except Exception as exc:  # noqa: BLE001 - 单单元交叉检查失败不该拖垮整轮扫描
                semantic_by_unit[unit] = None
                scan.warnings.append(
                    f"语义交叉检查 {unit.name} 失败：{type(exc).__name__}: {exc}"
                )

    # ---------------------------------------------------------------- stage 1
    # 定位 + 读源码。定位不了的候选不进编译阶段：它的结论会是假的。
    pending: list[tuple[RemovalCandidate, Path, int]] = []
    results: list[VerificationResult] = []
    for candidate in ranked:
        # 要被改动的那个文件：TU 级就是源自己，头文件级是那个头文件。
        source_path = candidate.site
        try:
            original_text = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message=f"cannot read {source_path}: {exc}",
                )
            )
            continue

        location = locate_include_line(original_text, candidate.header, candidate.line)
        if location.line is None:
            if location.status == "ambiguous":
                results.append(
                    VerificationResult(
                        candidate=candidate,
                        status="ambiguous",
                        message=(
                            f"源码里有 {location.matches} 行 include 都叫 "
                            f"{candidate.header.name}，无法判断该注释掉哪一行"
                            "——注释错行会让结论指向另一个文件，因此跳过。"
                            "改用更完整的 include 路径，或用编译器的行号信息消歧"
                        ),
                    )
                )
            else:
                results.append(
                    VerificationResult(
                        candidate=candidate,
                        status="not-found",
                        message="在源码中找不到对应的 #include 行",
                    )
                )
            continue
        pending.append((candidate, source_path, location.line))

    # 头文件级候选走另一条路径：改的是公共头、测的是一批单元，不能和 TU 级
    # 的并行流程混在一起（同一个头文件被两个候选并发改会互相覆盖源码）。
    multi_pending = [entry for entry in pending if entry[0].is_header_site]
    pending = [entry for entry in pending if not entry[0].is_header_site]

    # ---------------------------------------------------------------- stage 2
    # 探针：并行编译「去掉这一行」的版本。这一阶段只回答「能不能编过」，
    # 因此 probe_runs=1 就够；把 runs 也用在这里会让每个坏候选多花两倍时间。
    probe_seconds: dict[int, float | None] = {}
    probe_errors: dict[int, str | None] = {}
    #: 代码等价校验的结果（仅 codegen_check 开启时填充）
    probe_codegen: dict[int, tuple[object, object]] = {}
    #: 闸门是否真的可用：MSVC 没有 ``-S``/``-g0`` 这套写法，硬开会把每个候选
    #: 都误报成「代码变了」。能力不够时直接不跑，报告也不许声称做过校验。
    codegen_ok = verify.codegen_check and codegen.supported(driver)
    #: 闸门本身能不能用（与「本轮开没开」无关）。疑似副作用候选强制用它，
    #: 但编译器不支持 ``-S`` 时（MSVC）也不能假装校验过。
    codegen_capable = codegen.supported(driver)

    def _probe_one(index: int, source_path: Path, line_number: int) -> None:
        candidate, source_path, line_number = pending[index]
        original_text = source_path.read_text(encoding="utf-8", errors="replace")
        with tempfile.TemporaryDirectory(prefix="heavyheader-probe-") as tmp:
            patched_path = Path(tmp) / source_path.name
            patched_path.write_text(
                _comment_out(original_text, line_number), encoding="utf-8"
            )
            unit_options = _unit_options(scan, candidate, options, effective_includes)
            patched_options = _patched_options(
                unit_options, effective_includes, source_path
            )
            seconds, _memory, error, _discarded = _measure(
                driver,
                patched_path,
                patched_options,
                None,
                verify.probe_runs,
                verify.timeout,
            )
            if error is None and (
                codegen_ok or (_forced_codegen(candidate) and codegen_capable)
            ):
                # 疑似副作用**强制**过闸门：文本层说不清那句宏调用展开成什么，
                # 那就让编译器说。这里不跟随 ``--check-codegen`` 开关——关掉它
                # 是用户的性能选择，不是允许在没证据时下结论的许可。
                probe_codegen[index] = codegen.compare(
                    driver,
                    source_path,
                    patched_path,
                    patched_options,
                    None,
                    Path(tmp),
                    verify.timeout,
                )
        probe_seconds[index] = seconds
        probe_errors[index] = error

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, verify.jobs)) as pool:
            futures = {
                pool.submit(_probe_one, index, source, line): index
                for index, (_c, source, line) in enumerate(pending)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - 一个候选不该带走整轮
                    probe_seconds[index] = None
                    probe_errors[index] = f"{type(exc).__name__}: {exc}"

    # ---------------------------------------------------------------- stage 3
    # 只对探针存活的候选做完整测量；基线按单元缓存，同单元多个候选只测一次。
    baselines = _BaselineCache()
    survivors: list[tuple[int, RemovalCandidate, float]] = []

    for index, (candidate, _source_path, _line_number) in enumerate(pending):
        patched_seconds = probe_seconds.get(index)
        if patched_seconds is None:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="compile-error",
                    message=f"移除后编译失败：{probe_errors.get(index) or 'compile failed'}",
                )
            )
            continue
        codegen_pair = probe_codegen.get(index)
        if codegen_pair is not None and (
            not codegen_pair[0].ok or not codegen_pair[1].ok
        ):
            # 闸门没跑完 ≠ 代码变了。以前两者共用 ``same_as`` 的 False，于是
            # 「这台机器/这套参数给不出汇编」被报成「发现危险改动」——既冤枉了
            # 候选，也让上面的 verified 档失真。拿不到证据就如实说判不了。
            note = codegen_pair[0].note or codegen_pair[1].note or "未知原因"
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message=(
                        f"代码等价校验未能完成（{note}），无法证明程序未变"
                        "——保守判为不可删"
                    ),
                    **_codegen_fields(codegen_pair),
                )
            )
            continue
        if _forced_codegen(candidate) and codegen_pair is None:
            # 文本层怀疑这个头靠「被包含时执行」起作用（疑似副作用），或者这一
            # 行只是**实现片段的一次实例化**——后者删掉通常只是让目标文件少一
            # 个函数定义，编译照样通过，只有汇编指纹能证明「程序没变」。编译器
            # 又不肯给指纹时（MSVC 的 ``-S`` 方言不同），没有合格证据就不下结
            # 论——报成编译不过或没收益都是编数字，这里如实说「判不了」。
            why = (
                "疑似副作用"
                if candidate.side_effect_suspected
                else "实现片段的一次实例化"
            )
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message=(
                        f"{why}：本轮拿不到汇编指纹，无法证明程序未变"
                        "（保守判为不可删）"
                    ),
                )
            )
            continue
        if codegen_pair is not None and not codegen_pair[0].same_as(codegen_pair[1]):
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="codegen-changed",
                    patched_seconds=patched_seconds,
                    message="移除后编译通过，但生成的代码与原来不同"
                    "（差异原因需人工判断：条件编译、重载解析选中的定义，"
                    "或链接期才暴露的缺失定义都有可能）",
                    **_codegen_fields(codegen_pair),
                )
            )
            continue
        if verify.probe_only:
            # 只问「能不能编过」。不给秒数结论——那一侧还没测，编个数字
            # 出来就是估算冒充实测。
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="compiles",
                    patched_seconds=patched_seconds,
                    message="移除后可以编译（未做耗时测量）",
                    **_codegen_fields(codegen_pair),
                )
            )
            continue

        # 基线不在这里测：留到下面的「同并发度预热」之后统一取，否则串行测得
        # 的基线会和并行测得的补丁侧不可比（见预热处的注释）。
        survivors.append((index, candidate, patched_seconds))

    # 完整测量本身也可以并行（每个候选一次 patched 编译，runs 次取最小）。
    # measured: pending 下标 -> (基线秒, 移除后秒, 移除后 GGC 内存字节,
    #                               移除后侧被丢弃的计时行)
    measured: dict[int, tuple[float, float, int | None, tuple[str, ...]]] = {}

    # **基线必须与补丁侧同并发度**。基线原先在下面的 for 循环里串行测，补丁侧
    # 却在 jobs 宽的线程池里跑——两边看到的机器负载不是一个量级。实测这台机器
    # 一次编译墙钟 215 ms 里只有 29 ms 是编译器自己的（其余是进程启动），
    # 4 路并发下每个编译都被拖慢 40–50 ms，于是 52 条候选**全部**被记成「更慢」，
    # 差值的符号由测量装置决定而不是由代码决定。先在同一个池子里把基线测完，
    # 两侧就吃同样的争抢（每单元一次，缓存仍然有效）。
    if survivors and verify.timing_jobs > 1:
        units = sorted({pending[index][1] for index, _c, _s in survivors})

        def _warm(unit: Path) -> None:
            try:
                baselines.get(
                    driver,
                    unit,
                    _unit_options_for_path(scan, unit, options, effective_includes),
                    verify.runs,
                    verify.timeout,
                )
            except Exception:  # noqa: BLE001 - 基线失败下面会按单元再报一次
                pass

        with ThreadPoolExecutor(max_workers=max(1, verify.timing_jobs)) as pool:
            list(pool.map(_warm, units))

    # 预热之后取基线：并行/串行两条路径到这里都只是查缓存。取不到（基线编不过）
    # 的候选如实报错，不参与比较。
    kept: list[tuple[int, RemovalCandidate, float]] = []
    for index, candidate, patched_seconds in survivors:
        source_path = pending[index][1]
        if baselines.get(
            driver,
            source_path,
            _unit_options(scan, candidate, options, effective_includes),
            verify.runs,
            verify.timeout,
        ) is None:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message="基线编译失败，无法比较",
                )
            )
            continue
        kept.append((index, candidate, patched_seconds))
    survivors = kept

    def _measure_one(index: int, source_path: Path, line_number: int) -> None:
        original_text = source_path.read_text(encoding="utf-8", errors="replace")
        with tempfile.TemporaryDirectory(prefix="heavyheader-verify-") as tmp:
            patched_path = Path(tmp) / source_path.name
            patched_path.write_text(
                _comment_out(original_text, line_number), encoding="utf-8"
            )
            unit_options = _unit_options(
                scan, pending[index][0], options, effective_includes
            )
            patched_options = _patched_options(
                unit_options, effective_includes, source_path
            )
            seconds, memory, _error, discarded = _measure(
                driver, patched_path, patched_options, None, verify.runs, verify.timeout
            )
        baseline = baselines._values[source_path]
        measured[index] = (baseline, seconds or 0.0, memory, discarded)

    if survivors:
        # 计时阶段用 ``timing_jobs``（默认串行）：并发会让每个编译都变慢且
        # 抖动量随负载变化，见 VerifyOptions.timing_jobs 的实测记录。
        with ThreadPoolExecutor(max_workers=max(1, verify.timing_jobs)) as pool:
            futures = {
                pool.submit(
                    _measure_one,
                    index,
                    pending[index][1],
                    pending[index][2],
                ): index
                for index, _c, _s in survivors
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001
                    measured[index] = (0.0, 0.0, None, ())
                    probe_errors[index] = f"{type(exc).__name__}: {exc}"

    # 目标文件大小：只对「实测有收益」的候选再花两次完整编译。
    # 「收益全在编译时间、产物体积不变」是个有价值的结论，但为每个噪声内
    # 的候选都测一遍体积，是把预算花在回答不了问题的测量上。
    # sizes: pending 下标 -> (基线字节, 移除后字节)
    sizes: dict[int, tuple[int | None, int | None]] = {}

    def _measure_sizes(index: int, source_path: Path, line_number: int) -> None:
        candidate = pending[index][0]
        original_text = source_path.read_text(encoding="utf-8", errors="replace")
        with tempfile.TemporaryDirectory(prefix="heavyheader-size-") as tmp:
            patched_path = Path(tmp) / source_path.name
            patched_path.write_text(
                _comment_out(original_text, line_number), encoding="utf-8"
            )
            unit_options = _unit_options(scan, candidate, options, effective_includes)
            baseline_size = _object_size(
                driver,
                source_path,
                _patched_options(unit_options, effective_includes, source_path),
                verify.timeout,
            )
            patched_size = _object_size(
                driver,
                patched_path,
                _patched_options(unit_options, effective_includes, source_path),
                verify.timeout,
            )
        sizes[index] = (baseline_size, patched_size)

    improved: list[tuple[int, VerificationResult]] = []
    for index, candidate, _probe in survivors:
        if index not in measured:
            continue
        baseline_seconds, patched_seconds, memory, discarded = measured[index]
        saved = baseline_seconds - patched_seconds
        # A change smaller than the noise floor is inside the noise of a
        # single file's compile time on a loaded machine; reporting it as a
        # win would be dishonest.  The floor is defined once, in
        # attrib.advice, so this status and the REMOVE verdict can never
        # disagree about what "improved" means.
        meaningful = (
            baseline_seconds > 0 and abs(saved) / baseline_seconds >= TIME_NOISE_FLOOR
        )
        timing_note = ""
        baseline_discarded = baselines.discarded(pending[index][1])
        if baseline_discarded or discarded:
            timing_note = (
                "编译器自报计时损坏（GCC/MinGW 已知问题），"
                "以上秒数为驱动实测墙钟，非编译器内部 TOTAL"
            )
        result = VerificationResult(
            candidate=candidate,
            status="improved" if saved > 0 and meaningful else "no-change",
            baseline_seconds=baseline_seconds,
            patched_seconds=patched_seconds,
            message=(
                f"基线 {baseline_seconds * 1000:.0f} ms → "
                f"移除后 {patched_seconds * 1000:.0f} ms"
                if meaningful
                else f"差异在噪声范围内（{saved * 1000:+.0f} ms）"
            ),
            baseline_memory_kb=baselines.memory(pending[index][1]),
            patched_memory_kb=memory,
            timing_note=timing_note,
            **_codegen_fields(probe_codegen.get(index)),
        )
        results.append(result)
        if result.status == "improved":
            improved.append((index, result))

    # ---------------------------------------------------------------- confirm
    # 复测：只在「已经判为 improved」的候选上再花一遍测量预算。
    # 单遍测量无法把「真实的 1% 收益」与「噪声恰好偏了 1%」分开，而报告的
    # 每一句「省了 x%」都建立在这个判定上。复测不通过的一律降级，并把每一遍
    # 的读数写进 message——读者要能看到它为什么不可信，而不是只看到一个结论。
    if verify.confirm_passes > 1 and improved:
        demoted: set[int] = set()
        for index, result in improved:
            candidate, source_path, line_number = pending[index]
            unit_options = _unit_options(scan, candidate, options, effective_includes)
            original_text = source_path.read_text(encoding="utf-8", errors="replace")
            percents = [result.percent_saved or 0.0]
            passed = True
            with tempfile.TemporaryDirectory(prefix="heavyheader-confirm-") as tmp:
                patched_path = Path(tmp) / source_path.name
                patched_path.write_text(
                    _comment_out(original_text, line_number), encoding="utf-8"
                )
                patched_options = _patched_options(
                    unit_options, effective_includes, source_path
                )
                for _ in range(verify.confirm_passes - 1):
                    base, _, err_b, _ = _measure(
                        driver, source_path, patched_options, None,
                        verify.runs, verify.timeout,
                    )
                    patched, _, err_p, _ = _measure(
                        driver, patched_path, patched_options, None,
                        verify.runs, verify.timeout,
                    )
                    if base is None or patched is None:
                        passed = False
                        percents.append(0.0)
                        result.message += (
                            f"；复测未能编译（{err_b or err_p or 'unknown'}），"
                            "结论降级为未证实"
                        )
                        break
                    percent = (base - patched) / base * 100 if base else 0.0
                    percents.append(percent)
                    if percent < TIME_NOISE_FLOOR * 100:
                        passed = False
            if not passed:
                demoted.add(index)
                result.status = "no-change"
                result.message += (
                    "；复测未通过，各遍 percent_saved = "
                    + " / ".join(f"{value:.2f}%" for value in percents)
                    +                     f"（阈值 {TIME_NOISE_FLOOR * 100:.0f}%）"
                )
            else:
                # 复测全过才记账：``confirm_passes`` 是「收益结论站得住的第几遍」，
                # 0 遍的「省了 x%」与 2 遍仍成立的「省了 x%」不该用同一句话讲。
                result.confirm_passes = verify.confirm_passes - 1
        improved = [pair for pair in improved if pair[0] not in demoted]

    if improved:
        with ThreadPoolExecutor(max_workers=max(1, verify.jobs)) as pool:
            futures = {
                pool.submit(
                    _measure_sizes,
                    index,
                    pending[index][1],
                    pending[index][2],
                ): index
                for index, _r in improved
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:  # noqa: BLE001 - 体积测不到不影响时间结论
                    pass
        for index, result in improved:
            baseline_bytes, patched_bytes = sizes.get(index, (None, None))
            result.baseline_object_bytes = baseline_bytes
            result.patched_object_bytes = patched_bytes

    # ---------------------------------------------------------------- stage 4
    # 测试证据：只对「实测有收益」的候选跑。为噪声内的候选跑一遍完整测试是
    # 把预算花在换不来证据的测量上——它的结论已经是 HUMAN，测试通过与否
    # 都不会把它变成建议删除。
    #
    # 协议（先证伪自己，再取证）：
    #   1. 基线版先跑。基线就不过 = 测试环境或基线自身有问题，
    #      记 error——不冤枉候选，但这份「通过」也不能当证据用；
    #   2. 基线过了才跑补丁版。补丁挂 = failed，结论降级；
    #   3. 两侧都过 = passed，作为证据写进建议。
    if improved and verify.test_runner is not None:
        for index, result in improved:
            candidate, source_path, line_number = pending[index]
            try:
                baseline_ok, baseline_detail = verify.test_runner(source_path)
            except Exception as exc:  # noqa: BLE001 - 测试基础设施故障不是候选的错
                result.test_status = "error"
                result.test_detail = f"基线测试执行失败：{type(exc).__name__}: {exc}"
                continue
            if not baseline_ok:
                result.test_status = "error"
                result.test_detail = (
                    f"基线版测试即未通过（{baseline_detail}）——"
                    "这是测试环境或基线的问题，不计到候选头上，但也不能作为证据"
                )
                continue
            try:
                original_text = source_path.read_text(encoding="utf-8", errors="replace")
                with tempfile.TemporaryDirectory(prefix="heavyheader-test-") as tmp:
                    patched_path = Path(tmp) / source_path.name
                    patched_path.write_text(
                        _comment_out(original_text, line_number), encoding="utf-8"
                    )
                    patched_ok, patched_detail = verify.test_runner(patched_path)
            except Exception as exc:  # noqa: BLE001
                result.test_status = "error"
                result.test_detail = f"补丁版测试执行失败：{type(exc).__name__}: {exc}"
                continue
            result.test_status = "passed" if patched_ok else "failed"
            result.test_detail = (
                f"补丁版测试{'通过' if patched_ok else '失败'}：{patched_detail}"
                if patched_detail
                else ("补丁版测试通过" if patched_ok else "补丁版测试失败")
            )

    for index, candidate, _probe in survivors:
        if index in measured:
            candidate.measured_seconds_saved = max(
                0.0, measured[index][0] - measured[index][1]
            )
            candidate.verified = True

    # ------------------------------------------------- header-level sites
    # 放在最后：它要改公共头文件，期间不能有任何并发编译在用它。
    if multi_pending:
        # 头文件级候选走「改一次头、重编一批单元」的路径，这条路径没有汇编闸门。
        # 疑似副作用的候选**必须**有闸门才配得上结论，所以它们在这里就被拦下，
        # 而不是靠「编译过了」冒充证据。
        gated = [entry for entry in multi_pending if entry[0].side_effect_suspected]
        multi_pending = [entry for entry in multi_pending if not entry[0].side_effect_suspected]
        for candidate, _site, _line in gated:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message="疑似副作用：头文件级候选暂不支持汇编闸门，保守判为不可删",
                )
            )
        if multi_pending:
            results.extend(
                _verify_header_site(
                    scan,
                    driver,
                    options,
                    effective_includes,
                    verify,
                    multi_pending,
                    baselines,
                )
            )

    # 失败也要留下痕迹。以前只有成功的候选被写回 scan，失败的候选在列表里
    # 看起来跟没探过一模一样——于是报告会把一个「已经实测编译不过」的候选
    # 继续排在前排，下一轮扫描还会重复推荐它。probe_status 就是让排序知道
    # 「这条已经证伪了」。
    for result in results:
        result.candidate.probe_status = result.status
        # AST 交叉检查结论随候选走（与 probe_status / advice 同样理由：报告与
        # 排序读的都是候选）。None 表示没开启语义层或该单元未能取到信号。
        result.candidate.semantic = semantic_by_unit.get(result.candidate.tu)
        # 明确结论「值不值得删」。规则在 attrib.advice，纯函数、可单测；
        # 这里只负责把证据喂给它。写回候选而不是只放在 result 里，是因为
        # 报告与排序读的都是候选——结论必须跟着候选走。
        result.candidate.advice = build_advice(result.candidate, result, scan)

    # 结果按候选的排序顺序返回（探针是并行完成的，顺序不能丢）。
    order = {id(candidate): position for position, (candidate, _s, _l) in enumerate(pending)}
    results.sort(key=lambda r: order.get(id(r.candidate), len(order)))
    return results
