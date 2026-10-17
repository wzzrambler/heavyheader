"""Scan orchestration: turn a directory into a :class:`ProjectScan`.

This is the seam between "a folder of C++" and "numbers".  It decides which
files are translation units, how to invoke the compiler on each of them, and
how to fold the results into the project-wide statistics.

Translation-unit discovery, in priority order:

1. ``compile_commands.json`` -- the single source of truth when it exists.
   Using it means we compile with the project's *real* flags, which is the
   difference between numbers that mean something and numbers that don't.
2. A recursive walk for C++ extensions, skipping the directories that are
   never interesting (``.git``, ``node_modules``, build output, vendored
   third-party trees).

Both paths are capped by ``max_tus`` because scanning is O(files) compiler
invocations and nobody wants to wait an hour for a report.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .cache import (
    CacheStats,
    ScanCache,
    StampResolver,
    compiler_fingerprint,
    load_cache,
    save_cache,
)
from .attrib.graph import (
    HeaderSizes,
    build_header_stats,
    classify_headers,
    find_removal_candidates,
    redundant_reparse_warnings,
)
from .attrib.forward import find_forward_declaration_candidates
from .attrib.pch import estimate_pch
from .attrib.templates import profile_project
from .drivers.base import CompilationError, CompilerDriver, DriverOptions
from .models import CompilerFamily, ProjectScan, TranslationUnit

__all__ = ["ScanOptions", "ScanError", "scan_project", "discover_translation_units"]

_SOURCE_EXTENSIONS = {".cpp", ".cxx", ".cc", ".c++", ".C", ".cp"}
_C_EXTENSIONS = {".c"}

_SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "build",
    "out",
    "cmake-build-debug",
    "cmake-build-release",
    ".cache",
    "third_party",
    "3rdparty",
    "vendor",
    "external",
}


class ScanError(RuntimeError):
    """A scan could not be carried out."""


@dataclass(slots=True)
class ScanOptions:
    """Knobs for a scan run."""

    root: Path
    compiler: str = "g++"
    standard: str | None = None
    include_dirs: list[Path] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    extra_flags: list[str] = field(default_factory=list)
    compile_commands: Path | None = None
    max_tus: int = 500
    jobs: int = 0  # 0 = os.cpu_count()
    full_compile: bool = False
    timeout: float = 300.0
    #: Extra directories to skip during the recursive walk.
    exclude_dirs: set[str] = field(default_factory=set)
    #: Estimate a precompiled-header recommendation.
    pch: bool = False
    #: How many headers to consider putting in the PCH.
    pch_top: int = 5
    #: Look for includes that could become forward declarations.
    forward_decl: bool = False

    def driver_options(self) -> DriverOptions:
        return DriverOptions(
            standard=self.standard,
            include_dirs=self.include_dirs,
            defines=self.defines,
            extra_flags=self.extra_flags,
            full_compile=self.full_compile,
        )


@dataclass(slots=True)
class _CompileTask:
    source: Path
    options: DriverOptions
    cwd: Path | None = None


def discover_translation_units(
    root: Path,
    *,
    compile_commands: Path | None = None,
    max_tus: int = 500,
    exclude_dirs: set[str] | None = None,
) -> list[_CompileTask]:
    """Work out which files to compile and with which flags.

    When ``compile_commands.json`` is available we honour it exactly: the
    command string is split into argv and the working directory is preserved,
    so relative include paths keep resolving the way the real build does.
    """
    tasks: list[_CompileTask] = []
    cc_path = compile_commands or (root / "compile_commands.json")

    if cc_path.is_file():
        tasks = _tasks_from_compile_commands(cc_path, max_tus, root=root)
        if tasks:
            return tasks

    skip = set(_SKIP_DIRECTORIES) | (exclude_dirs or set())
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.suffix in _SOURCE_EXTENSIONS or path.suffix in _C_EXTENSIONS:
                found.append(path)
                if len(found) >= max_tus:
                    break
        if len(found) >= max_tus:
            break

    options = DriverOptions()
    return [_CompileTask(source=path, options=options) for path in found]


def _tasks_from_compile_commands(
    cc_path: Path, max_tus: int, root: Path | None = None
) -> list[_CompileTask]:
    """Build compile tasks from a compilation database.

    ``root`` is the scan root, used as the working-directory fallback when an
    entry points at a build directory that no longer exists.
    """
    try:
        raw = json.loads(cc_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"cannot read {cc_path}: {exc}") from exc

    tasks: list[_CompileTask] = []
    for entry in raw:
        source = Path(entry.get("file", ""))
        if not source.name:
            continue
        directory = Path(entry.get("directory", "."))
        # 构建目录被删掉/移动过是很常见的事（先 CMake 生成、再清理 build 目录）。
        # 原样透传会让 subprocess 抛 NotADirectoryError——那不是 CompilationError，
        # 线程池接不住，一条坏记录就能带走整个扫描及其余已完成的编译结果。
        # 退回到项目根：多数项目的相对路径在那里依然成立。
        if not directory.is_dir():
            directory = root
        command = entry.get("command") or " ".join(entry.get("arguments", []))
        if not command:
            continue
        argv = _split_command(command)
        if not argv:
            continue
        # Strip the output flag: we compile to /dev/null via -fsyntax-only and
        # do not want the project's -o path polluting the source tree.
        argv = _strip_output_flag(argv)
        options = DriverOptions(extra_flags=argv[1:])
        tasks.append(_CompileTask(source=source, options=options, cwd=directory))
        if len(tasks) >= max_tus:
            break
    return tasks


def _split_command(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _strip_output_flag(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token == "-o":
            skip_next = True
            continue
        if token.startswith("-o") and len(token) > 2:
            continue
        out.append(token)
    return out


def default_include_dirs(root: Path) -> list[Path]:
    """Common C++ layout conventions, so headers resolve without help.

    公开而非私有：`verify` 必须和 `scan` 用同一套兜底路径，否则扫描能找到的
    头文件在验证时找不到，基线编译直接失败（见 verify.py）。
    """
    candidates = [root, root / "include", root / "src", root / "include" / "src"]
    seen: dict[Path, None] = {}
    for candidate in candidates:
        if candidate.is_dir():
            seen.setdefault(candidate)
    return list(seen)


def scan_project(
    options: ScanOptions,
    driver: CompilerDriver | None = None,
    cache_path: Path | None = None,
) -> ProjectScan:
    """Run a scan and return the aggregated result.

    Compilation is parallelised across translation units: the driver calls out
    to a subprocess, so threads give us real concurrency without the
    complexity of a process pool.

    ``cache_path`` 打开增量扫描：上次编译过、且此后源文件与依赖头都没变过的
    编译单元直接复用结论，不再编译一次。缓存坏了或编译选项变了会自动退化成
    全量，不会让扫描失败。
    """
    if driver is None:
        from .drivers.base import detect_driver

        driver = detect_driver(options.compiler)

    root = options.root.resolve()
    include_dirs = list(options.include_dirs) or default_include_dirs(root)

    tasks = discover_translation_units(
        options.root,
        compile_commands=options.compile_commands,
        max_tus=options.max_tus,
        exclude_dirs=options.exclude_dirs,
    )
    if not tasks:
        raise ScanError(
            f"no C/C++ translation units found under {options.root}; "
            "point --path at a source tree, or supply a compile_commands.json"
        )

    # Tasks coming from compile_commands.json already carry their own flags;
    # only the auto-discovered ones need the shared include paths.
    for task in tasks:
        if not task.options.extra_flags:
            task.options.include_dirs = include_dirs
            task.options.defines = options.defines
            task.options.standard = options.standard
            task.options.extra_flags = options.extra_flags
            task.options.full_compile = options.full_compile

    jobs = options.jobs or (os.cpu_count() or 4)
    tus: list[TranslationUnit] = []
    failures: list[str] = []

    # ------------------------------------------------------------------
    # 增量：先挑出可以直接复用上次结论的编译单元
    # ------------------------------------------------------------------
    stats = CacheStats(total=len(tasks))
    resolver = StampResolver()
    cache: ScanCache | None = None

    if cache_path is not None:
        fingerprint = compiler_fingerprint(
            compiler=options.compiler,
            family=getattr(driver.family, "value", str(driver.family)),
            standard=options.standard,
            include_dirs=include_dirs,
            defines=options.defines,
            extra_flags=options.extra_flags,
            full_compile=options.full_compile,
        )
        loaded = load_cache(cache_path)
        if loaded is None:
            # 没有缓存 / 缓存损坏：静默退化成全量扫描。
            cache = ScanCache(fingerprint=fingerprint, root=str(root))
        elif loaded.fingerprint != fingerprint:
            # 编译选项变了，上次的结论一律作废——但不要丢文件，覆盖掉即可。
            cache = ScanCache(fingerprint=fingerprint, root=str(root))
            stats.cache_loaded = True
            stats.fingerprint_matched = False
        else:
            cache = loaded
            stats.cache_loaded = True
            stats.fingerprint_matched = True

        pending: list[_CompileTask] = []
        for task in tasks:
            reused = cache.lookup(task.source, resolver)
            if reused is not None:
                tus.append(reused)
                stats.reused += 1
            else:
                pending.append(task)
        tasks = pending

    stats.compiled = len(tasks)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {
            pool.submit(driver.run, task.source, task.options, task.cwd, options.timeout): task
            for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                tus.append(future.result())
            except CompilationError as exc:
                failures.append(str(exc))
                tus.append(
                    TranslationUnit(source=task.source, error=str(exc))
                )
            except Exception as exc:  # noqa: BLE001 - 见下方说明
                # 以前只接 CompilationError。驱动抛出的其余异常（失效的 cwd
                # 是 NotADirectoryError、权限问题、畸形 argv…）会穿透线程池，
                # 让整个扫描带着回溯中断——500 个 TU 里有 1 条坏记录，其余
                # 499 个已完成的编译结果全部丢失。一个编译单元的出问题不该
                # 报废整次扫描，所以在这里兜住并记为该单元的失败。
                message = f"{task.source}: {type(exc).__name__}: {exc}"
                failures.append(message)
                tus.append(TranslationUnit(source=task.source, error=message))

    scan = ProjectScan(
        root=root,
        compiler=options.compiler,
        compiler_family=driver.family,
        standard=options.standard,
        tus=sorted(tus, key=lambda t: str(t.source)),
        baseline_seconds=0.0,
        warnings=failures,
    )

    # 增量：把这次真正编译过的结果写回缓存，并让统计可见。
    if cache is not None:
        for tu in scan.ok_tus:
            cache.record(tu, resolver)
        # 只保留本次扫描涉及的编译单元，避免缓存随项目演进而无限膨胀。
        cache.entries = {
            key: entry
            for key, entry in cache.entries.items()
            if key in {str(tu.source) for tu in scan.ok_tus}
        }
        save_cache(cache_path, cache)
    stats.total = len(scan.tus)
    stats.compiled = stats.total - stats.reused
    scan.cache_stats = stats

    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    # 注意：kinds 必须用**原始** TU 路径当 key（与 find_removal_candidates /
    # redundant_reparse_warnings 的查找口径一致），不能用 scan.header_stats 的
    # 归一化 key。build_header_stats 为了跨编译器逐头对齐，把头文件归一到绝对
    # realpath；但候选检测与缺 guard 提示用的是 TU 报告里的原始 include 路径，
    # 两者 key 空间不同——这里若用归一化 key，kinds.get(raw_include) 永远 miss，
    # 候选数会塌成 0（graph.py 归一化引入的回归，已由此行修正）。
    kinds = classify_headers(
        root, {h for tu in scan.ok_tus for h in tu.dependencies}
    )
    scan.candidates = find_removal_candidates(scan, sizes, kinds)
    scan.baseline_seconds = sum(tu.total_wall_seconds() for tu in scan.ok_tus)
    # Template instantiation cost.  This also fills each header's
    # template_declarations / template_surface, which the reports read.
    scan.template_profile = profile_project(scan)
    if options.pch:
        scan.pch = estimate_pch(scan, top_k=options.pch_top)
    if options.forward_decl:
        scan.forward_candidates = find_forward_declaration_candidates(scan)

    # Surface the redundant-reparse findings as warnings too, so a plain
    # `scan` run never hides a guaranteed win.
    for header, processed, tu_count in redundant_reparse_warnings(scan):
        if kinds.get(header) is not None and kinds[header].value == "project":
            scan.warnings.append(
                f"{header}: parsed {processed}x across {tu_count} TU(s) "
                "-- missing include guard?"
            )

    # Likewise surface compiler timers that misbehaved.  Silently correcting
    # the numbers would hide a real defect in the toolchain the user is
    # running, and they would have no way to find out.
    for tu in scan.ok_tus:
        for discarded in tu.timing_discarded:
            scan.warnings.append(
                f"{tu.source}: discarded impossible timing ({discarded}) "
                "-- the compiler's -ftime-report timer is unreliable here; "
                "remaining timings are still usable"
            )

    # A TU that only compiled after the driver retried launching the compiler
    # was a hair's breadth from being miscounted as a failure (the OS refused
    # to spawn the process, not the source's fault).  It succeeded in the end,
    # so we do not hide it -- but we say so, because a flaky launch is a sign
    # the machine was at its parallelism limit and the next run might not be
    # so lucky.  Lowering --jobs is the fix.
    retried_ok = [tu for tu in scan.ok_tus if tu.spawn_retries > 0]
    if retried_ok:
        scan.warnings.append(
            f"{len(retried_ok)} 个编译单元起初因系统资源紧张而启动失败，"
            "已自动重试成功；若频繁出现，可用 --jobs 降低并行度（如 -j 4）"
        )

    return scan
