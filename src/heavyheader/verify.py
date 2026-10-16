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
from dataclasses import dataclass, field
from pathlib import Path

from .drivers.base import CompilationError, CompilerDriver, DriverOptions
from .models import ProjectScan, RemovalCandidate, TranslationUnit

__all__ = ["VerifyOptions", "VerificationResult", "verify_candidates", "find_include_line"]


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
    status: str  # "improved" | "no-change" | "compile-error" | "not-found" | "error"
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


def find_include_line(source_text: str, header: Path) -> int | None:
    """Return the 1-based line of the ``#include`` for ``header``, if any.

    Matches on the file name, which is what both ``"foo/bar.h"`` and
    ``<bar.h>`` spellings have in common.  The first uncommented match wins;
    a file that includes the same header twice is a separate (and rarer)
    problem we do not try to solve here.
    """
    wanted = header.name
    for number, line in enumerate(_strip_comments(source_text).splitlines(), start=1):
        match = _INCLUDE_RE.match(line)
        if match and Path(match.group("target")).name == wanted:
            return number
    return None


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
    include_dirs = list(include_dirs or [])

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

        line_number = find_include_line(original_text, candidate.header)
        if line_number is None:
            results.append(
                VerificationResult(
                    candidate=candidate,
                    status="not-found",
                    message="在源码中找不到对应的 #include 行",
                )
            )
            continue

        # Measure the unmodified file first: the baseline must come from the
        # same process, same machine, same run as the patched measurement.
        baseline_seconds, baseline_error = _measure(
            driver, source_path, options, None, verify.runs, verify.timeout
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
                include_dirs=[*include_dirs, source_path.parent],
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

    return results
