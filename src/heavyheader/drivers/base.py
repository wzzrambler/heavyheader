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

import locale
import os
import re
import shlex
import subprocess
import time
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
from ..parse.timereport import (
    TimeReportError,
    parse_time_report,
    sanitise_timings,
)

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
    #: Where to write the object file.  ``None`` = do not produce one
    #: (the ``-fsyntax-only`` default).
    #:
    #: This exists for the *size* dimension of the removal advice: "the
    #: object file is byte-identical without this include" is a real,
    #: reproducible finding, and the only way to get it is to actually
    #: produce both object files.  Setting this implies full compilation
    #: for that one invocation and strips the flags that would conflict
    #: (``-fsyntax-only``/``/Zs`` refuse to emit an object; ``-MF``-style
    #: dependency flags would write next to the *caller's* cwd and
    #: pollute the project).
    output_path: Path | None = None


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
        """Compile ``source`` and return what we learned.

        并行编译时，操作系统偶尔会**启动不了**编译器进程（Windows 上常见
        ``[WinError 1455] 页文件不足`` / ``[WinError 6] 句柄无效``，POSIX 上是
        EMFILE/ENOMEM）。这与 TU 本身有没有错无关——过会儿重编就好了，但当时
        会被记成一个假失败，连跑多次时 TU 数忽高忽低就是它引起的（见
        scanner 的稳健性测试）。这种「启动失败」和「编译失败」是两回事：编译失败
        是 returncode != 0，走正常路径；这里只对**启动**的瞬时失败做有限次重试。
        """
        argv = self.build_command(source, options)
        attempts = 0
        while attempts < _MAX_SPAWN_ATTEMPTS:
            attempts += 1
            try:
                started = _perf_counter()
                completed = subprocess.run(
                    argv,
                    cwd=str(cwd) if cwd else None,
                    capture_output=True,
                    text=True,
                    encoding=self._output_encoding(),
                    errors="replace",
                    timeout=timeout,
                    env=self._subprocess_env(),
                )
            except subprocess.TimeoutExpired as exc:
                # 编译确实太慢，超出了预算。重试只会再等一个超时，没有意义，
                # 直接记作编译失败（与「启动失败」区分）。
                raise CompilationError(
                    f"{source}: compiler timed out after {timeout:.0f}s"
                ) from exc
            except FileNotFoundError as exc:
                # 编译器根本不在 PATH 上——必然失败，不重试。
                raise CompilationError(
                    f"compiler {argv[0]!r} not found on PATH"
                ) from exc
            except (OSError, subprocess.SubprocessError) as exc:
                # 启动失败：多半是系统资源临时紧张（进程/句柄/内存池耗尽）。
                # 留给下一次尝试；次数用尽才当作真正的失败，免得一个 TU 的
                # 瞬时故障误记成「这个文件编译不过」。
                if attempts < _MAX_SPAWN_ATTEMPTS:
                    _sleep(_SPAWN_BACKOFF * attempts)
                    continue
                raise CompilationError(
                    f"{source}: compiler failed to start "
                    f"({type(exc).__name__}: {exc}); "
                    "this is usually transient OS contention under many parallel "
                    "compiles -- retrying did not help"
                ) from exc
            else:
                # 启动成功，跳出重试循环。
                break

        wall = _perf_counter() - started
        tu = self.interpret(
            source=source,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            wall_seconds=wall,
        )
        # 记下来以便扫描层能提示用户：这个 TU 差一点就被误记成失败了。
        tu.spawn_retries = attempts - 1
        return tu

    def _subprocess_env(self) -> dict | None:
        """传给 ``subprocess.run`` 的环境变量。

        默认 ``None``——直接继承当前进程环境。子类可在此注入编译器需要的
        特定变量（例如 MSVC 的语言/编码相关设置），而不必改动调用方。
        """
        return None

    def _output_encoding(self) -> str:
        """读取编译器输出时用的解码方式。

        默认 UTF-8。子类可覆盖——例如 MSVC 在本机把诊断输出按系统 ANSI
        代码页（中文 Windows 上是 GBK/936）编码，必须用对应编码解码，
        否则中文（等）内容会变成乱码、连带着 ``/showIncludes`` 的本地化
        前缀也匹配不上。
        """
        return "utf-8"


def _perf_counter() -> float:
    return time.perf_counter()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


#: 并行编译时，操作系统偶尔会**启动不了**编译器进程（资源临时紧张）。
#: 这种「启动失败」与「编译失败」是两码事——启动失败多半是进程/句柄/内存池
#: 一时耗尽，过会儿重编就好了；编译失败是 returncode != 0，走正常路径。这里
#: 只对**启动**失败做有限次重试，避免把一个本来没问题的 TU 误记成失败（连跑
#: 多次时 TU 数忽高忽低就是它引起的）。
_MAX_SPAWN_ATTEMPTS = 3
_SPAWN_BACKOFF = 0.05


#: Flags that prevent an object file from being written.  Stripped (not
#: warned about) when ``output_path`` is set: the caller asked for an
#: object, and a refusal here would silently degrade the size measurement
#: into a compile error.
_NO_OBJECT_FLAGS_GCC = ("-fsyntax-only", "-fsyntax")
#: Dependency-file flags: they write ``*.d`` relative to the process cwd,
#: which for a probe is the *user's* project directory.  Pollution, and
#: they contribute nothing to codegen.
_DEP_FILE_FLAGS_GCC = ("-MD", "-MMD", "-MP", "-MF", "-MT", "-MQ")
_NO_OBJECT_FLAGS_MSVC = ("/Zs",)
_DEP_FILE_FLAGS_MSVC = ("/showIncludes",)


def _strip_for_object(
    argv: list[str], no_object: tuple[str, ...], dep: tuple[str, ...]
) -> list[str]:
    """Remove flags that conflict with producing an object file.

    ``-MF``/``-MT``/``-MQ`` take their target as the next argv element, so
    that element goes too.  Everything else passes through untouched -- the
    compile must stay otherwise identical to the one that produced the
    baseline, or the size comparison is meaningless.
    """
    out: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token in dep:
            if token in ("-MF", "-MT", "-MQ"):
                skip_next = True
            continue
        if token in no_object:
            continue
        out.append(token)
    return out


def _append_source(argv: list[str], source: Path, extra: list[str]) -> list[str]:
    """Append the source file unless ``extra`` already carries it.

    The driver owns the source argument.  When it does not, a
    ``compile_commands.json`` entry (``… -c foo.cpp``) plus the driver's own
    append passes ``foo.cpp`` **twice**: GCC accepts that silently, compiles
    the file twice, and prints two ``-ftime-report`` TOTAL rows -- while
    every reader here kept the first one.  One duplicated token corrupted
    every timing and include tree taken in compile_commands mode.

    The scanner now strips the token at the source; this is the second line
    of defence for hand-built options that carry it anyway.
    """
    token = str(source)
    if token in extra:
        return argv
    return [*argv, token]


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
        extra = list(options.extra_flags)
        if options.output_path is not None:
            # 目标文件测量：剥掉与「产出 .o」冲突的参数，再显式指定输出。
            # 编译单元自带的 extra_flags 常含 -c、-MD -MF xxx.d（来自
            # compile_commands.json），原样带上会把 .d 写到调用方 cwd，
            # 也可能让编译器以「不允许 -o 与 -fsyntax-only 同用」为由拒绝。
            extra = _strip_for_object(extra, _NO_OBJECT_FLAGS_GCC, _DEP_FILE_FLAGS_GCC)
            argv += extra
            argv += ["-o", str(options.output_path)]
            return _append_source(argv, source, extra)
        if not options.full_compile:
            argv.append("-fsyntax-only")
        argv += extra
        return _append_source(argv, source, extra)

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
            raw_timings = parse_time_report(stderr)
        except TimeReportError:
            raw_timings = []
        # Cross-check the compiler's self-reported timings against the wall
        # time we measured ourselves.  A phase cannot outlast its process;
        # when the compiler's timer is broken (GCC on MinGW does this) we
        # drop the impossible rows rather than report them.
        timings, sanity = sanitise_timings(raw_timings, wall_seconds)
        if not any(timing.is_total for timing in timings):
            # 编译器的 TOTAL 行缺失或被 sanity check 丢弃（GCC on MinGW 的
            # 已知 bug：某些头文件会让 -ftime-report 打印 2**64/1e9 秒）。
            # 此时退回我们自己量的墙钟——它不区分编译器内部阶段，但它是
            # 一个真实的进程耗时。关键是不允许「没有 TOTAL」静默变成 0.0：
            # 一个 0.0 的「移除后耗时」会被下游读成 100% 收益，
            # 那是把坏掉的计时器当成完美优化。
            timings = [*timings, _wall_timing(wall_seconds)]
            sanity.discarded.append(
                "TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）"
            )
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
            timing_discarded=list(sanity.discarded),
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

    def _subprocess_env(self) -> dict | None:
        """让 MSVC 的诊断文本尽量用英文 ASCII。

        ``VSLANG=1033`` 会把**错误/警告**描述改成英文 ASCII，避免错误文本里
        出现本地化乱码（前提是下面 ``_output_encoding`` 按正确的系统代码页解码）。

        注意：它**不会**改变 ``/showIncludes`` 的本地化前缀——中文 Windows 上
        前缀始终是「注意: 包含文件:」，与 VSLANG 无关。前缀的本地化由
        ``parse_show_includes`` 里的双前缀正则负责（中英文都接受），所以这里
        设不设 VSLANG 都不影响 include 树解析，只影响出错时错误文本的语言。
        （源文件自身的编码问题属于用户工程，工具无法代劳；含非 ASCII 源码时
        建议源码存为带 BOM 的 UTF-8，MSVC 会据此按 UTF-8 读取。）
        """
        env = dict(os.environ)
        env["VSLANG"] = "1033"
        return env

    def _output_encoding(self) -> str:
        """按 MSVC 真实使用的代码页解码其诊断输出。

        MSVC 把诊断信息（含 ``/showIncludes`` 的中文前缀「注意: 包含文件:」）
        按**系统 OEM 代码页**写出：中文 Windows 上是 GBK/936。用 UTF-8 解码会
        把中文变成乱码、前缀匹配不上，整个 include 树被解析成 0 条边。

        注意 ``locale.getpreferredencoding()`` 在现代 Python（PEP 540 / UTF-8
        mode）下常常返回 ``utf-8``，与控制台实际代码页不符，所以这里直接问
        Windows 的 OEM 代码页；非 Windows 或查询失败时再退回 locale 结果。
        """
        if os.name == "nt":
            try:
                import ctypes

                codepage = ctypes.windll.kernel32.GetOEMCP()  # type: ignore[attr-defined]
                if codepage:
                    return f"cp{codepage}"
            except Exception:
                pass
        return locale.getpreferredencoding() or "utf-8"

    def build_command(self, source: Path, options: DriverOptions) -> list[str]:
        argv = [self.executable, "/nologo"]
        extra = list(options.extra_flags)
        if options.output_path is not None:
            extra = _strip_for_object(extra, _NO_OBJECT_FLAGS_MSVC, _DEP_FILE_FLAGS_MSVC)
            argv += extra
            argv.append(f"/Fo{options.output_path}")
            return _append_source(argv, source, extra)
        argv.append("/showIncludes")
        argv.append("/Zs")
        if options.standard:
            argv.append(f"/std:{options.standard}")
        for directory in options.include_dirs:
            argv.append(f"/I{directory}")
        for define in options.defines:
            argv.append(f"/D{define}")
        argv += extra
        return _append_source(argv, source, extra)

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


# 一个横幅里出现任一项即判为 GCC 家族。按可信度排列：先找 clang，
# 再找这些，避免在同时提到两者的横幅上误判。
_GCC_BANNER_MARKERS = (
    "free software foundation",
    "gcc",
    "g++",
    "mingw",
    "gnu c",
    "gnu c++",
)


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
    # OSError covers "executable not found"; SubprocessError covers
    # TimeoutExpired.  The old code named a non-existent attribute
    # (subprocess.SubprocessExpired), so probing a missing compiler raised
    # AttributeError from the error handler instead of a usable message.
    except (OSError, subprocess.SubprocessError):
        raise CompilationError(
            f"cannot probe compiler {executable!r}: "
            "not found, not executable, or it timed out"
        )
    banner = (completed.stdout or "") + (completed.stderr or "")
    lowered = banner.lower()
    if "clang" in lowered:
        return GccLikeDriver(executable, is_clang=True)
    # Cross-compilers and vendor builds are inconsistent about what they
    # print: MinGW g++ leads with "g++.exe (x86_64-win32-seh-rev1...)" and
    # only mentions the FSF on a later line, and some vendor toolchains drop
    # the FSF line entirely.  Match any of the usual GCC-family markers.
    if any(marker in lowered for marker in _GCC_BANNER_MARKERS):
        return GccLikeDriver(executable, is_clang=False)
    raise CompilationError(
        f"cannot tell which compiler {executable!r} is; "
        "pass an explicit driver instead"
    )


def quote_command(argv: list[str]) -> str:
    """Render an argv for logs and error messages."""
    return " ".join(shlex.quote(part) for part in argv)
