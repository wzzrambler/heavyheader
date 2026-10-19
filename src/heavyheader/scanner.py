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
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .attrib.cost import collect_trace_costs
from .attrib.diagnosis import build_diagnosis
from .attrib.forward import find_forward_declaration_candidates
from .attrib.graph import (
    HeaderSizes,
    build_header_stats,
    classify_headers,
    find_removal_candidates,
    redundant_reparse_warnings,
)
from .attrib.pch import estimate_pch
from .attrib.sites import find_header_candidates
from .attrib.templates import profile_project
from .cache import (
    CacheStats,
    ScanCache,
    StampResolver,
    compiler_fingerprint,
    load_cache,
    save_cache,
)
from .drivers.base import CompilationError, CompilerDriver, DriverOptions
from .models import ProjectScan, TranslationUnit, tu_compile_seconds

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
    #: 也把**头文件里**的 ``#include`` 当作候选来源（默认关）。
    #:
    #: 打开它才会出现「删掉 prelude.h 里一行、24 个编译单元一起受益」这类
    #: 候选——那是 TU 级候选天生看不见的空间（实测 json 全项目 83 条 TU 级
    #: 候选里只有 3 条删得掉）。代价是候选池变大、验证要重编一批单元，
    #: 因此默认关闭，由显式开关或实验脚本启用。
    header_sites: bool = False
    #: 把系统/第三方头文件内部的 ``#include`` 行也列出来（仅供查看）。
    #:
    #: 默认不开的原因不是「删不掉」，而是「删了也轮不到我们决定」——改
    #: ``<vector>`` 内部的一行不是本项目能承担的决策。诊断里会统计这类行有
    #: 多少，开着它可以看到具体是哪几行。
    include_system_sites: bool = False
    #: Rank by *measured* header cost instead of attributed lines.
    #:
    #: Lines are a structural proxy; the injection experiment showed how bad
    #: a proxy it is -- a removal that really saved 6.8% of wall time was
    #: ranked 25th of 61 by line count.  Turning this on compiles every unit
    #: once more with ``-ftime-trace`` (a real codegen pass: clang ignores
    #: the flag under ``-fsyntax-only``) and ranks by microseconds instead.
    #: Off by default because of that extra pass; clang-only, and the tool
    #: falls back to lines -- saying so in the report -- when no trace lands.
    cost_model: str = "lines"
    #: Run the probe/measure stage inside the scan.
    #:
    #: This is the framework change the controlled experiment forced.  ``scan``
    #: used to stop at a *ranked estimate* and leave the truth to a separate
    #: ``verify`` command that users had to know to run -- so the tool's
    #: headline output was a guess, and on nlohmann/json five of those guesses
    #: failed to compile.  With ``probe=True`` the candidate question is
    #: answered by compiling: probe every candidate (one compile each, in
    #: parallel), then fully measure only the survivors.  Estimates stay in
    #: the report as estimates; anything that matters is measured.
    probe: bool = False
    #: 只做编译可行性探测：把每条候选删一次、编译一次，回答「这条删了还能编
    #: 吗」，不测时间、不跑复测。成本约为 ``probe`` 的 1/6（每候选一次编译，
    #: 而不是两侧各 runs 次）。
    #:
    #: 存在的理由：闸门（``attrib.sites``）是文本启发式，实测放行的 16 条里
    #: 有 8 条编译失败。报告若把这 16 条并列成「候选」，读者会以为都能删。
    #: 加上这一步，候选表就能分成「已探测可编译 / 删了编不过」两类——
    #: 而这两类的证据等级相差一次真实编译。
    probe_compile: bool = False
    #: 把「文本判定给不出结论」的站点也放进候选（``--allow-unknown-sites``）。
    #:
    #: 默认不放：命中全是环境噪音时，放行等于用一次编译去赌。但闸门有假阴性
    #: （zlib 的 ``zconf.h -> <limits.h>`` 被判在用，实测却编得过），所以留了
    #: 这个开关让人自己选择用编译去买召回。
    allow_unknown_sites: bool = False
    #: 完全关掉可行性闸门（``--ungated-sites``）：所有收益非零的站点都进候选，
    #: 由 ``verify`` 一条条用编译裁决。
    #:
    #: 这是一个**有意的取舍**，不是「更宽松」：zlib 上闸门因 ``size`` 这个词
    #: 把 ``zutil.h -> <stdlib.h>`` 判成「在用」，而那条删掉后 9 个单元全通过、
    #: 实测省 2.125%（复测通过）。小工程验证便宜（zlib 全量探测 29 s），
    #: 关掉闸门换召回是划算的；大工程则应留着闸门控制成本。
    ungated_sites: bool = False
    #: How many candidates to probe.  ``0`` = all of them.
    probe_top: int = 0
    #: Wall-clock budget for the probe/measure stage, seconds.  ``0`` = unlimited.
    probe_budget: float = 0.0
    #: Parallel compiles during the probe stage.
    probe_jobs: int = 4
    #: Compiles per side during full measurement (probes always use 1).
    probe_runs: int = 3
    #: 实测时是否做代码等价校验（汇编哈希比对），见 VerifyOptions.codegen_check
    check_codegen: bool = False
    #: 可选 AST 交叉检查后端（attrib.semantic.SemanticBackend 实例）。
    #: ``None`` = 不跑——这是默认且诚实的行为：没有该工具时，结论仍以本工具
    #: 实证为准，绝不假装做过 AST 校验。类型用 object 以避免 scanner 反向依赖
    #: attrib.semantic；运行期传真实实例，verify_candidates 才认它。
    semantic: object | None = None
    #: clang-include-cleaner 的 ``-p`` 目录（compile_commands.json 所在）。
    #: 必须在启用 semantic 时提供，否则交叉检查静默跳过（理由见 attrib/semantic.py）。
    semantic_build_path: Path | None = None

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
        # Strip the entry's own source token: the driver appends the source
        # file itself, so leaving it here would pass the file TWICE.  GCC
        # accepts that silently -- and then compiles it twice, emitting TWO
        # ``-ftime-report`` TOTAL rows while every timing we read came from
        # the first one.  That single duplicated token silently corrupted
        # every measurement taken in compile_commands mode (see the
        # regression test in tests/test_scanner.py).
        argv = _strip_source_token(argv, str(source))
        options = DriverOptions(extra_flags=argv[1:])
        tasks.append(_CompileTask(source=source, options=options, cwd=directory))
        if len(tasks) >= max_tus:
            break
    return tasks


def _same_source(token: str, source: str | Path) -> bool:
    """Compare a command token with the entry's source path.

    ``compile_commands.json`` routinely disagrees with itself: the ``file``
    field uses forward slashes while the command line uses backslashes
    (MSBuild/Ninja), and Windows paths differ in drive-letter casing.  A
    literal string comparison therefore misses the trailing source token on
    POSIX -- the file is passed twice, GCC compiles it twice, and every
    timing read from the first ``-ftime-report`` TOTAL row is silently
    wrong.  Compare separator- and case-insensitively (only on the final
    token, so a flag's own value is never touched).
    """

    def canon(value: str) -> str:
        return value.replace("\\", "/").lower()

    return canon(token) == canon(str(source))


def _strip_source_token(argv: list[str], source: str) -> list[str]:
    """Remove the compilation database's own source file from ``argv``.

    Only the **final** token is considered, and only on a
    separator-/case-insensitive match: ``… -c foo.cpp`` is the canonical
    shape, and never touching anything else keeps a flag's own value
    (``-I <dir>``) safe even in the absurd case where it shares the
    source's name.  Anything that is not the trailing source token is left
    exactly as the build system wrote it.
    """
    if argv and _same_source(argv[-1], source):
        return argv[:-1]
    return argv


def _split_command(command: str) -> list[str]:
    """按命令行规则切分 compile_commands 里的一条命令。

    为什么不能无条件 ``posix=True``：Windows 上 MSBuild/Ninja 生成的
    ``compile_commands.json`` 常把路径写成 ``C:\\a\\b``。posix 模式把反斜杠当
    转义符，``-I C:\\a\\b`` 被切成 ``-I C:ab``——每个头文件搜索路径都指向不
    存在的位置，于是**所有编译单元都编译失败**，扫描结果看起来是「0 个单元」，
    而错误信息只是普通的「找不到头文件」。

    Windows 风格命令走 :func:`_split_windows_command`：反斜杠是普通字符，
    双引号成对包住的空格不切分（``-I"C:\\Program Files\\x"`` 是一个参数）。
    POSIX 系统的命令不含反斜杠，仍走 ``shlex``，行为不变。
    """
    if "\\" in command:
        return _split_windows_command(command)
    import shlex

    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _split_windows_command(command: str) -> list[str]:
    """Windows 参数切分：保留反斜杠，双引号内不切分。

    不用 ``shlex(posix=False)`` 的原因很具体：它不把引号内的空格当作受保护
    内容，``-I"C:\\Program Files\\x"`` 会被切成 ``-I"C:\\Program`` 与
    ``Files\\x"`` 两段——路径依旧断在空格上，问题只是换了种表现形式。
    """
    args: list[str] = []
    buffer: list[str] = []
    in_quotes = False
    for char in command:
        if char == '"':
            in_quotes = not in_quotes
            continue
        if char in " \t" and not in_quotes:
            if buffer:
                args.append("".join(buffer))
                buffer = []
            continue
        buffer.append(char)
    if buffer:
        args.append("".join(buffer))
    return args


def _strip_output_flag(argv: list[str]) -> list[str]:
    out: list[str] = []
    skip_next = False
    for token in argv:
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

    # Tasks coming from compile_commands.json already carry their own flags, so
    # the shared ``-I``/``-D``/``-std`` must not be layered on top of them --
    # the entry is the authority on how that file is built.
    #
    # But the caller's explicit ``--flag`` is a different thing: it is the one
    # place a user can override what the database says (``--target=`` and
    # ``--gcc-toolchain=`` when clang must be pointed at MinGW instead of the
    # MSVC headers it defaults to on Windows).  Ignoring it in
    # compile_commands mode was silent -- the flag was accepted by argparse,
    # reported by ``--reproduce``, and then dropped here, so every unit failed
    # to compile against the wrong standard library with no hint why.
    for task in tasks:
        if not task.options.extra_flags:
            task.options.include_dirs = include_dirs
            task.options.defines = options.defines
            task.options.standard = options.standard
            task.options.full_compile = options.full_compile
            task.options.extra_flags = list(options.extra_flags)
        elif options.extra_flags:
            task.options.extra_flags = list(task.options.extra_flags) + list(
                options.extra_flags
            )

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
                tu = future.result()
            except CompilationError as exc:
                failures.append(str(exc))
                tus.append(
                    TranslationUnit(source=task.source, error=str(exc))
                )
                continue
            except Exception as exc:  # noqa: BLE001 - 见下方说明
                # 以前只接 CompilationError。驱动抛出的其余异常（失效的 cwd
                # 是 NotADirectoryError、权限问题、畸形 argv…）会穿透线程池，
                # 让整个扫描带着回溯中断——500 个 TU 里有 1 条坏记录，其余
                # 499 个已完成的编译结果全部丢失。一个编译单元的出问题不该
                # 报废整次扫描，所以在这里兜住并记为该单元的失败。
                message = f"{task.source}: {type(exc).__name__}: {exc}"
                failures.append(message)
                tus.append(TranslationUnit(source=task.source, error=message))
                continue
            # 把这个单元真实的编译参数挂在结果上。探针/测量阶段要靠它复现
            # 同一次编译——compile_commands.json 驱动的项目里，每个单元的
            # -I 都不一样，用共享的 ScanOptions 重建会丢参数，然后给出
            # 「移除后编译失败」的假结论。
            tu.compile_options = task.options
            tus.append(tu)

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
    # Blowup Factor 的分母：每个参与解析的文件只计一次。头文件用
    # header_stats 的 key（已归一化为绝对 realpath），源文件用 TU 的 source，
    # 取并集去重——同一个头文件被 200 个 TU 包含，在分母里也只能算一次，
    # 否则这个比值会退化成「包含次数的平均值」，失去「每行被解析几次」的含义。
    scan.total_project_lines = sizes.total(
        set(scan.header_stats) | {tu.source for tu in scan.ok_tus}
    )
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
    # 头文件里的 include（默认关）。放在 TU 级之后，是为了让「改自己源文件的
    # 一行」永远排在「改公共头文件的一行」前面：前者风险低得多，收益相同时
    # 没有理由推荐动公共头。
    if options.header_sites:
        costs = None
        if options.cost_model == "trace":
            costs = collect_trace_costs(
                scan,
                driver,
                DriverOptions(),
                workdir=Path(tempfile.mkdtemp(prefix="heavyheader-trace-")),
                timeout=options.timeout,
            )
            if not costs.available:
                # 不要静默回退：用户显式要了实测，拿不到必须说清楚。
                scan.warnings.append(
                    "未采集到 -ftime-trace 数据（可能不是 clang，或全部单元编译失败），"
                    "排序回退到归因行数"
                )
        scan.cost_model = costs.basis if costs is not None else "lines"
        scan.cost_profile = costs
        # 同时收集被闸门拦下的行：0 条候选时报告不能只剩一张空表。
        blocked: list = []
        header_site_candidates = find_header_candidates(
            scan,
            sizes,
            kinds,
            costs=costs,
            blocked=blocked,
            include_system_sites=options.include_system_sites,
            include_unknown=options.allow_unknown_sites,
            require_plausibly_unused=not options.ungated_sites,
        )
        scan.candidates = scan.candidates + header_site_candidates
        scan.site_diagnosis = build_diagnosis(
            scan, blocked, candidate_count=len(header_site_candidates)
        )
    # 「累计编译耗时」对外呈现的是**墙钟**，所以这里优先用驱动实测的
    # wall_seconds。-ftime-report 的 TOTAL 是 CPU 时间，Windows 上两者可差
    # 30 倍（0.02s vs 0.60s，实测），用 CPU 时间会让报告里的「累计耗时」
    # 与 verify 的墙钟节省率口径不一致，百分比失真。旧数据没有 wall_seconds
    # 时退回 TOTAL，并保持原样而不是猜一个值。
    scan.baseline_seconds = sum(tu_compile_seconds(tu) for tu in scan.ok_tus)
    # Template instantiation cost.  This also fills each header's
    # template_declarations / template_surface, which the reports read.
    scan.template_profile = profile_project(scan)
    if options.pch:
        scan.pch = estimate_pch(scan, top_k=options.pch_top)
    if options.forward_decl:
        scan.forward_candidates = find_forward_declaration_candidates(scan)

    # 探针/测量阶段：把「这一行能不能删」从估算变成实测。
    # 放在候选生成之后、报表之前——报表里「实测」一栏要有东西可写。
    #
    # 两个档位，成本差一个量级：
    #   --probe-compile  每条候选删一次、编一次，只回答「还能不能编」
    #   --probe          再加上「省不省时」（两侧各 runs 次取最小 + 复测）
    # 前者是给「候选表里一半其实编不过」准备的：不测时间，但把两类分开。
    if options.probe_compile and options.probe:
        # 两个开关同给时以 --probe 为准（它包含探测阶段），但仍要说出来，
        # 否则用户会以为白花了 --probe-compile 的那次编译。
        scan.warnings.append(
            "--probe-compile 与 --probe 同时给出，前者被后者覆盖（--probe 已含探测阶段）"
        )
    if options.probe:
        from .verify import VerifyOptions, verify_candidates

        scan.probe_mode = "probe"
        scan.probe_results = verify_candidates(
            scan,
            driver,
            options.driver_options(),
            verify=VerifyOptions(
                top=options.probe_top,
                runs=options.probe_runs,
                budget_seconds=options.probe_budget,
                jobs=options.probe_jobs,
                codegen_check=options.check_codegen,
            ),
            semantic=options.semantic,
            build_path=options.semantic_build_path,
        )
    elif options.probe_compile:
        from .verify import VerifyOptions, verify_candidates

        scan.probe_mode = "probe-compile"
        scan.probe_results = verify_candidates(
            scan,
            driver,
            options.driver_options(),
            verify=VerifyOptions(
                top=options.probe_top,
                probe_only=True,
                budget_seconds=options.probe_budget,
                jobs=options.probe_jobs,
                codegen_check=options.check_codegen,
            ),
            semantic=None,
            build_path=options.semantic_build_path,
        )

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
