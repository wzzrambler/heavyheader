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

import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from typing import NamedTuple
from pathlib import Path

from .drivers.base import CompilationError, CompilerDriver, DriverOptions
from .models import ProjectScan, RemovalCandidate, TranslationUnit
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
    """Knobs for the verification pass."""

    top: int = 5
    runs: int = 3
    timeout: float = 300.0
    #: Keep the patched sources around for inspection.
    keep_artifacts: bool = False


@dataclass(slots=True)
class VerificationResult:
    """Outcome of trying to remove one include."""

    candidate: RemovalCandidate
    #: improved / no-change / compile-error / not-found / ambiguous / error
    status: str
    baseline_seconds: float | None = None
    patched_seconds: float | None = None
    message: str = ""

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


def _strip_comments(source: str) -> str:
    """Blank out comments while preserving line structure.

    Line count must not change, otherwise line numbers reported by the
    compiler stop matching the file we edit.  Only whole-line and trailing
    comments are handled -- a ``//`` inside a string literal would confuse a
    full lexer, but that is vanishingly rare in an ``#include`` line.
    """
    out: list[str] = []
    in_block = False
    for line in source.splitlines():
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
            match = _INCLUDE_RE.match(lines[hint_line - 1])
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
    indent = len(lines[index]) - len(lines[index].lstrip())
    lines[index] = " " * indent + "// [heavyheader] " + lines[index].lstrip()
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
) -> tuple[float | None, str | None]:
    """Compile ``source`` ``runs`` times, return ``(min_seconds, error)``."""
    best: float | None = None
    last_error: str | None = None
    for _ in range(max(1, runs)):
        try:
            tu = driver.run(source, options, cwd=cwd, timeout=timeout)
        except CompilationError as exc:
            return None, str(exc)
        if not tu.ok:
            return None, tu.error or "compile failed"
        seconds = tu.total_wall_seconds()
        if best is None or seconds < best:
            best = seconds
    return best, last_error


def verify_candidates(
    scan: ProjectScan,
    driver: CompilerDriver,
    options: DriverOptions,
    *,
    verify: VerifyOptions | None = None,
    include_dirs: list[Path] | None = None,
) -> list[VerificationResult]:
    """Empirically test the top ``verify.top`` removal candidates.

    ``options`` should be the same options used to produce ``scan`` -- the
    comparison is only meaningful if both sides are compiled identically.
    """
    verify = verify or VerifyOptions()

    # 有效包含路径 = 扫描时用的（options）+ 调用方额外给的（kwarg）；
    # 两者都为空时套用与 scan 相同的布局兜底。
    #
    # 以前基线编译直接透传 options（无兜底），而打补丁那侧又改用 kwarg、
    # 丢掉了 options 里的 -I。两个后果：include/ 布局的项目基线就编译不过，
    # 以及两侧口径不一致导致「移除后编译失败」的误报。
    effective_includes = list(options.include_dirs) + list(include_dirs or [])
    if not effective_includes:
        effective_includes = default_include_dirs(scan.root)
    baseline_options = replace(options, include_dirs=effective_includes)

    ranked = scan.candidates_by_estimate()[: max(0, verify.top)]
    results: list[VerificationResult] = []

    for candidate in ranked:
        source_path = candidate.tu
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

        location = locate_include_line(
            original_text, candidate.header, candidate.line
        )
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
        line_number = location.line

        # Measure the unmodified file first: the baseline must come from the
        # same process, same machine, same run as the patched measurement.
        baseline_seconds, baseline_error = _measure(
            driver, source_path, baseline_options, None, verify.runs, verify.timeout
        )
        if baseline_seconds is None:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="error",
                    message=f"基线编译失败：{baseline_error}",
                )
            )
            continue

        with tempfile.TemporaryDirectory(prefix="heavyheader-") as tmp:
            patched_path = Path(tmp) / source_path.name
            patched_path.write_text(
                _comment_out(original_text, line_number), encoding="utf-8"
            )

            # A quoted include resolves against the *including file's*
            # directory, so a file compiled from a temp directory would lose
            # access to its siblings.  Add the original directory explicitly.
            patched_options = DriverOptions(
                standard=options.standard,
                include_dirs=[*effective_includes, source_path.parent],
                defines=list(options.defines),
                extra_flags=list(options.extra_flags),
                full_compile=options.full_compile,
            )
            patched_seconds, patched_error = _measure(
                driver, patched_path, patched_options, None, verify.runs, verify.timeout
            )

        if patched_seconds is None:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="compile-error",
                    baseline_seconds=baseline_seconds,
                    message=f"移除后编译失败：{patched_error}",
                )
            )
            continue

        saved = baseline_seconds - patched_seconds
        # A change smaller than 2% is inside the noise floor of a single
        # file's compile time on a loaded machine; reporting it as a win
        # would be dishonest.
        meaningful = baseline_seconds > 0 and abs(saved) / baseline_seconds >= 0.02
        results.append(
            VerificationResult(
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
            )
        )

        # Fold the measurement back into the scan so reports can show both
        # the estimate and the truth side by side.
        candidate.measured_seconds_saved = max(0.0, saved)
        candidate.verified = True

    # 失败也要留下痕迹。以前只有成功的候选被写回 scan，失败的候选在列表里
    # 看起来跟没探过一模一样——于是报告会把一个「已经实测编译不过」的候选
    # 继续排在前排，下一轮扫描还会重复推荐它。probe_status 就是让排序知道
    # 「这条已经证伪了」。
    for result in results:
        result.candidate.probe_status = result.status

    return results
