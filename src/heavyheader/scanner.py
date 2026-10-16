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

from .attrib.graph import (
    HeaderSizes,
    build_header_stats,
    classify_headers,
    find_removal_candidates,
    redundant_reparse_warnings,
)
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
        tasks = _tasks_from_compile_commands(cc_path, max_tus)
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


def _tasks_from_compile_commands(cc_path: Path, max_tus: int) -> list[_CompileTask]:
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


def _default_include_dirs(root: Path) -> list[Path]:
    """Common C++ layout conventions, so headers resolve without help."""
    candidates = [root, root / "include", root / "src", root / "include" / "src"]
    seen: dict[Path, None] = {}
    for candidate in candidates:
        if candidate.is_dir():
            seen.setdefault(candidate)
    return list(seen)


def scan_project(options: ScanOptions, driver: CompilerDriver | None = None) -> ProjectScan:
    """Run a full scan and return the aggregated result.

    Compilation is parallelised across translation units: the driver calls out
    to a subprocess, so threads give us real concurrency without the
    complexity of a process pool.
    """
    if driver is None:
        from .drivers.base import detect_driver

        driver = detect_driver(options.compiler)

    root = options.root.resolve()
    include_dirs = list(options.include_dirs) or _default_include_dirs(root)

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

    scan = ProjectScan(
        root=root,
        compiler=options.compiler,
        compiler_family=driver.family,
        standard=options.standard,
        tus=sorted(tus, key=lambda t: str(t.source)),
        baseline_seconds=0.0,
        warnings=failures,
    )

    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(root, set(scan.header_stats))
    scan.candidates = find_removal_candidates(scan, sizes, kinds)
    scan.baseline_seconds = sum(tu.total_wall_seconds() for tu in scan.ok_tus)

    # Surface the redundant-reparse findings as warnings too, so a plain
    # `scan` run never hides a guaranteed win.
    for header, processed, tu_count in redundant_reparse_warnings(scan):
        if kinds.get(header) is not None and kinds[header].value == "project":
            scan.warnings.append(
                f"{header}: parsed {processed}x across {tu_count} TU(s) "
                "-- missing include guard?"
            )

    return scan
