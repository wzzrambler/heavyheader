"""Compiler driver abstraction.

HeavyHeader never parses source code itself.  It asks a real compiler what
it actually did and reads the answer, which means the analysis is exact by
construction -- there is no hand-rolled preprocessor to get subtly wrong.

Each driver knows how to ask one compiler family three questions in a single
invocation:

1. *What did you include, and how deeply?*  ``-H`` (GCC/Clang) or
   ``/showIncludes`` (MSVC).
2. *Where did the time go?*  ``-ftime-report``.
3. *What is missing an include guard?*  the ``-H`` trailer.

Keeping it to one invocation per translation unit matters: a second pass
would double the wall-clock cost of a scan and, worse, would let the two
passes disagree if a header changed in between.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import CompilerFamily, TranslationUnit
from ..parse.depfile import parse_depfile
from ..parse.includetree import (
    parse_include_guards,
    parse_include_tree,
    parse_show_includes,
)
from ..parse.timereport import TimeReportError, parse_time_report

__all__ = [
    "CompilerDriver",
    "GccLikeDriver",
    "MsvcDriver",
    "detect_driver",
    "CompilationError",
]


class CompilationError(RuntimeError):
    """The compiler refused to compile a translation unit."""


@dataclass(slots=True)
class DriverOptions:
    """Everything the driver needs to know beyond the source file."""

    standard: str | None = None
    include_dirs: list[Path] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    extra_flags: list[str] = field(default_factory=list)
    #: Emit full compilation instead of ``-fsyntax-only``.  Slower but
    #: produces the ``opt and generate`` phase too.
    full_compile: bool = False


class CompilerDriver(ABC):
    """One compiler family's dialect of the questions above."""

    family: CompilerFamily

    @abstractmethod
    def build_command(self, source: Path, options: DriverOptions) -> list[str]:
        """Return the argv for compiling ``source``."""

    @abstractmethod
    def interpret(
        self,
        source: Path,
        returncode: int,
        stdout: str,
        stderr: str,
        wall_seconds: float,
    ) -> TranslationUnit:
        """Turn a finished invocation into a :class:`TranslationUnit`."""

    # -- shared helpers ---------------------------------------------------

    def run(
        self,
        source: Path,
        options: DriverOptions,
        cwd: Path | None = None,
        timeout: float = 300.0,
    ) -> TranslationUnit:
        """Compile ``source`` and return what we learned."""
        argv = self.build_command(source, options)
        started = _perf_counter()
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompilationError(
                f"{source}: compiler timed out after {timeout:.0f}s"
            ) from exc
        except FileNotFoundError as exc:
            raise CompilationError(
                f"compiler {argv[0]!r} not found on PATH"
            ) from exc
        wall = _perf_counter() - started

        return self.interpret(
            source=source,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            wall_seconds=wall,
        )


def _perf_counter() -> float:
    import time

    return time.perf_counter()


class GccLikeDriver(CompilerDriver):
    """GCC and Clang share a flag dialect, so they share a driver.

    The only real difference is that Clang needs ``-ftime-report`` (17+) or
    ``-ftime-trace`` (JSON) while GCC has had ``-ftime-report`` since 9.  We
    use the text form for both and fall back to measuring wall time when the
    compiler does not support it.
    """

    family = CompilerFamily.GCC

    def __init__(self, executable: str, is_clang: bool = False) -> None:
        self.executable = executable
        self.is_clang = is_clang
        self.family = CompilerFamily.CLANG if is_clang else CompilerFamily.GCC

    def build_command(self, source: Path, options: DriverOptions) -> list[str]:
        argv = [self.executable]
        if options.standard:
            argv.append(f"-std={options.standard}")
        for directory in options.include_dirs:
            argv += ["-I", str(directory)]
        for define in options.defines:
            argv.append(f"-D{define}")
        # -H prints the include tree to stderr, -ftime-report the phase
        # breakdown; -fsyntax-only keeps us from writing object files and
        # from paying for code generation we do not measure.
        argv += ["-H", "-ftime-report"]
        if not options.full_compile:
            argv.append("-fsyntax-only")
        argv += options.extra_flags
        argv.append(str(source))
        return argv

    def interpret(
        self,
        source: Path,
        returncode: int,
        stdout: str,
        stderr: str,
        wall_seconds: float,
    ) -> TranslationUnit:
        if returncode != 0:
            return TranslationUnit(
                source=source,
                error=_first_error_line(stderr) or f"compiler exited with {returncode}",
            )

        events = parse_include_tree(stderr, source)
        try:
            timings = parse_time_report(stderr)
        except TimeReportError:
            timings = []
        guards = parse_include_guards(stderr)

        # -M is not usable in the same invocation as a real compile (it
        # implies -E), so we reconstruct the flat dependency list from the
        # include tree instead.  It is the same information, minus order.
        dependencies: dict[Path, None] = {}
        for event in events:
            dependencies.setdefault(event.included)

        return TranslationUnit(
            source=source,
            dependencies=list(dependencies),
            include_events=events,
            timings=timings,
            unguarded_headers=guards,
        )


class MsvcDriver(CompilerDriver):
    """MSVC: ``/showIncludes`` for the tree, wall time for everything else.

    MSVC has no ``-ftime-report`` equivalent that is convenient to parse, so
    we lean on the wall-clock measurement the driver already takes and mark
    the per-phase breakdown as unavailable.
    """

    family = CompilerFamily.MSVC

    def __init__(self, executable: str = "cl") -> None:
        self.executable = executable

    def build_command(self, source: Path, options: DriverOptions) -> list[str]:
        argv = [self.executable, "/nologo", "/showIncludes", "/Zs"]
        if options.standard:
            argv.append(f"/std:{options.standard}")
        for directory in options.include_dirs:
            argv.append(f"/I{directory}")
        for define in options.defines:
            argv.append(f"/D{define}")
        argv += options.extra_flags
        argv.append(str(source))
        return argv

    def interpret(
        self,
        source: Path,
        returncode: int,
        stdout: str,
        stderr: str,
        wall_seconds: float,
    ) -> TranslationUnit:
        blob = stdout + "\n" + stderr
        if returncode != 0:
            return TranslationUnit(
                source=source,
                error=_first_error_line(blob) or f"compiler exited with {returncode}",
            )

        events = parse_show_includes(blob, source)
        dependencies: dict[Path, None] = {}
        for event in events:
            dependencies.setdefault(event.included)

        # MSVC cannot give us phase timings, so report the single measured
        # phase.  Downstream code that wants a breakdown will see only this.
        timings = [
            _wall_timing(wall_seconds),
        ]
        return TranslationUnit(
            source=source,
            dependencies=list(dependencies),
            include_events=events,
            timings=timings,
        )


def _wall_timing(seconds: float):
    from ..models import PhaseTiming

    return PhaseTiming(name="TOTAL", wall_seconds=seconds, wall_percent=100.0)


_ERROR_LINE = re.compile(r"\berror\s*:", re.IGNORECASE)


def _first_error_line(text: str) -> str | None:
    """Pick the most informative error line out of compiler diagnostics.

    A naive "first line containing 'error'" search lands on
    ``In file included from ...`` chains or on a bare header path, which tells
    the reader nothing.  Prefer a line that actually carries a diagnostic,
    and fall back to anything mentioning a failure.
    """
    fallback: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _ERROR_LINE.search(stripped):
            return stripped[:400]
        if fallback is None and ("fatal" in stripped.lower() or "error" in stripped.lower()):
            fallback = stripped[:400]
    return fallback


def detect_driver(executable: str) -> CompilerDriver:
    """Pick a driver for ``executable``, probing it if the name is ambiguous.

    ``g++``/``gcc``/``clang++``/``clang`` are recognised by name; anything
    else is probed with ``--version`` so that wrappers (ccache, distcc,
    cross-compilers named ``arm-linux-gnueabihf-g++``) still land in the
    right dialect.
    """
    name = Path(executable).name.lower()
    if "clang" in name:
        return GccLikeDriver(executable, is_clang=True)
    if name.startswith("g++") or name.startswith("gcc") or name in {"cc", "c++"}:
        return GccLikeDriver(executable, is_clang=False)
    if name in {"cl", "cl.exe"}:
        return MsvcDriver(executable)

    # Ambiguous: ask the compiler itself.
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessExpired):  # pragma: no cover
        raise CompilationError(f"cannot probe compiler {executable!r}")
    banner = (completed.stdout or "") + (completed.stderr or "")
    if "clang" in banner.lower():
        return GccLikeDriver(executable, is_clang=True)
    if "free software foundation" in banner.lower() or "gcc" in banner.lower():
        return GccLikeDriver(executable, is_clang=False)
    raise CompilationError(
        f"cannot tell which compiler {executable!r} is; "
        "pass an explicit driver instead"
    )


def quote_command(argv: list[str]) -> str:
    """Render an argv for logs and error messages."""
    return " ".join(shlex.quote(part) for part in argv)
