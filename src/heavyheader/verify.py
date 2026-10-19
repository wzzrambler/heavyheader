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
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import NamedTuple

from . import codegen
from .attrib.advice import AGGREGATE_NOISE_FLOOR, TIME_NOISE_FLOOR, build_advice
from .attrib.semantic import SemanticBackend, SemanticVerdict
from .drivers.base import CompilationError, CompilerDriver, DriverOptions
from .models import (
    ProjectScan,
    RemovalCandidate,
    tu_compile_seconds,
)
from .scanner import default_include_dirs

__all__ = [
    "VerifyOptions",
    "VerificationResult",
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


# --------------------------------------------------------------------------
# locating and patching the include line
# --------------------------------------------------------------------------

_INCLUDE_RE = re.compile(
    r'^\s*#\s*include\s*(?P<delim>[<"])(?P<target>[^>"]+)[>"]'
)


#: UTF-8 BOM.  Windows editors（VS、Notepad、vcpkg 抓下来的一批文件）默认
#: 会给源码加它。它不是 ``\s``，所以 ``^\s*#\s*include`` 匹配不到带 BOM 的
#: 首行——一个真实工程的第一个 include 就这样被判成「找不到该注释哪一行」，
#: 结论从「建议删除」悄悄变成「需人工判断」。扫描侧不受影响：编译器自己
#: 认识 BOM；受影响的只有我们自己做文本匹配的这一层。
_BOM = "\ufeff"


def _strip_bom(text: str) -> str:
    """Remove a leading BOM without changing the line count.

    行号是定位 include 的唯一坐标，所以只能去掉 BOM 字符本身——
    把首行删掉会让后面所有行号错一位。
    """
    return text[len(_BOM) :] if text.startswith(_BOM) else text


def _strip_comments(source: str) -> str:
    """Blank out comments while preserving line structure.

    Line count must not change, otherwise line numbers reported by the
    compiler stop matching the file we edit.  Only whole-line and trailing
    comments are handled -- a ``//`` inside a string literal would confuse a
    full lexer, but that is vanishingly rare in an ``#include`` line.
    """
    out: list[str] = []
    in_block = False
    for line in _strip_bom(source).splitlines():
        result: list[str] = []
        index = 0
        while index < len(line):
            if in_block:
                end = line.find("*/", index)
                if end == -1:
                    result.append(" " * (len(line) - index))
                    index = len(line)
                else:
                    result.append(" " * (end + 2 - index))
                    index = end + 2
                    in_block = False
                continue
            pair = line[index : index + 2]
            if pair == "/*":
                in_block = True
                result.append("  ")
                index += 2
                continue
            if pair == "//":
                result.append(" " * (len(line) - index))
                index = len(line)
                continue
            result.append(line[index])
            index += 1
        out.append("".join(result))
    return "\n".join(out) + "\n"


class IncludeLocation(NamedTuple):
    """Where the ``#include`` for a header lives, and how sure we are."""

    line: int | None
    #: ""（已定位）/ "not-found" / "ambiguous"
    status: str
    #: 文本匹配到的行数。歧义时用来把情况说清楚，而不是含糊一句「找不到」。
    matches: int


def _normalise(text: str) -> str:
    """统一路径分隔符，去掉 ``./`` 前缀，便于比较。"""
    return text.strip().replace("\\", "/").lstrip("./")


def _path_matches(target: str, header: Path) -> bool:
    """``#include`` 的文本是否指向这个头文件（强匹配）。

    比只比文件名强得多：``#include "other/thing.h"`` 的文本是路径，与候选头的
    完整路径做后缀比较即可。这样 ``util/thing.h`` 与 ``other/thing.h`` 两个
    同名头能被区分开——只比文件名时它们完全一样，verify 会注释掉先出现的那行，
    于是给出一条指向错误文件的结论。

    这不是重新实现预处理器：只是把 include 文本当作路径后缀来比，不做宏展开、
    不算条件编译。比不出来时调用方会退回弱匹配。
    """
    wanted = str(header).replace("\\", "/")
    target = _normalise(target)
    return bool(target) and (wanted == target or wanted.endswith("/" + target))


def _match_lines(source_text: str, header: Path, predicate) -> list[int]:
    return [
        number
        for number, line in enumerate(_strip_comments(source_text).splitlines(), start=1)
        if (match := _INCLUDE_RE.match(line)) and predicate(match.group("target"))
    ]


def find_include_lines(source_text: str, header: Path) -> list[int]:
    """Every 1-based line whose ``#include`` names ``header``.

    两级匹配：先用完整路径后缀（强），只有强匹配定不下来时才退回文件名（弱）。
    返回**全部**候选行，由调用方决定——同名头冲突时若随便挑一行，删掉的可能是
    另一个文件，而结论看起来完全可信。
    """
    strong = _match_lines(source_text, header, lambda t: _path_matches(t, header))
    if strong:
        return strong
    # 弱匹配：include 文本只有文件名（``#include <thing.h>``），或路径对不上。
    return _match_lines(source_text, header, lambda t: Path(t).name == header.name)


def find_include_line(source_text: str, header: Path) -> int | None:
    """The include line for ``header``, but only when it is unambiguous.

    同名冲突时返回 ``None``，而不是「第一个匹配胜出」。宁可验证不了，也不能
    把另一条 include 注释掉——那会让 verify 给出一个指向错误文件的结论。
    """
    matches = find_include_lines(source_text, header)
    return matches[0] if len(matches) == 1 else None


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
    ranked = scan.candidates_by_estimate()[: max(0, limit)]

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
            if error is None and codegen_ok:
                # 比的是「同一个源文件、去掉那一行之后，编出来的汇编是否一字不差」。
                # 两次用同一份 options、同一个输出路径；源码目录由 codegen.compare 掩平。
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

    for index, (candidate, source_path, _line_number) in enumerate(pending):
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
        if codegen_pair is not None and not codegen_pair[0].same_as(codegen_pair[1]):
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="codegen-changed",
                    patched_seconds=patched_seconds,
                    message="移除后编译通过，但生成的代码与原来不同"
                    "（重载解析或条件编译发生变化）",
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
                )
            )
            continue

        baseline_seconds = baselines.get(
            driver,
            source_path,
            _unit_options(scan, candidate, options, effective_includes),
            verify.runs,
            verify.timeout,
        )
        if baseline_seconds is None:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message="基线编译失败，无法比较",
                )
            )
            continue
        survivors.append((index, candidate, patched_seconds))

    # 完整测量本身也可以并行（每个候选一次 patched 编译，runs 次取最小）。
    # measured: pending 下标 -> (基线秒, 移除后秒, 移除后 GGC 内存字节,
    #                               移除后侧被丢弃的计时行)
    measured: dict[int, tuple[float, float, int | None, tuple[str, ...]]] = {}

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
        with ThreadPoolExecutor(max_workers=max(1, verify.jobs)) as pool:
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
                    + f"（阈值 {TIME_NOISE_FLOOR * 100:.0f}%）"
                )
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
        results.extend(
            _verify_header_site(
                scan, driver, options, effective_includes, verify, multi_pending, baselines
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
