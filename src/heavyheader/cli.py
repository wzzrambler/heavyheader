"""HeavyHeader command line interface.

Seven verbs, each doing one thing:

======================  ====================================================
``scan``                analyse a tree, write a report
``verify``              recompile without candidate includes and measure
``gate``                fail a build when header cost regresses
``compare``             diff two saved scans
``history``             show the cost trend built from ``--history`` logs
``pr-comment``          render (and post) a PR comment from a scan comparison
``toolchains``          compare build cost across several compilers
======================  ====================================================

``argparse`` is used deliberately rather than ``click``/``typer``: the CLI is
the *public* surface of the tool and it must work on a bare Python install,
because it runs inside CI containers where installing dependencies is exactly
what you are trying to avoid.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

from . import __version__
from .baseline import (
    BaselineError,
    compare_scans,
    evaluate_gate,
    load_baseline,
    save_baseline,
)
from .drivers.base import CompilationError, detect_driver
from .models import ProjectScan
from .report.html import TrendPoint, render_html
from .report.jsonio import scan_from_dict, scan_to_dict, write_json
from .report.markdown import markdown_summary_line, render_markdown
from .scanner import ScanError, ScanOptions, scan_project
from .verify import VerifyOptions, verify_candidates
from .attrib.semantic import SemanticBackend, default_mingw_args

__all__ = ["main", "build_parser"]

_PROGRAM = "heavyheader"


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_PROGRAM,
        description=(
            "C/C++ 构建效能治理工具：用编译器自己的输出归因头文件成本，"
            "定位可精简的 include，并在 CI 中门禁成本回归。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
        "示例:\n"
        "  %(prog)s scan ./src --format html -o report.html\n"
        "  %(prog)s scan ./src --save baseline.json\n"
        "  %(prog)s verify ./src --top 5\n"
        "  %(prog)s gate ./src --baseline baseline.json --max-regression 5\n"
        "  %(prog)s scan ./src --history cost.jsonl\n"
        "  %(prog)s history cost.jsonl --serve\n"
        "  %(prog)s pr-comment --before baseline.json --after pr.json --post\n"
        "  %(prog)s toolchains ./src --compilers g++,clang++\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(
        sub: argparse.ArgumentParser,
        *,
        with_path: bool = True,
        with_compiler: bool = True,
    ) -> None:
        if with_path:
            sub.add_argument(
                "path",
                type=Path,
                nargs="?",
                default=Path("."),
                help="要分析的源码树（默认当前目录）",
            )
        if with_compiler:
            sub.add_argument(
                "--compiler", default="g++", help="编译器可执行文件（默认 g++）"
            )
        sub.add_argument("--std", default=None, help="语言标准，如 c++17")
        sub.add_argument("-I", "--include", dest="includes", action="append", default=[], type=Path, help="额外头文件搜索路径，可重复")
        sub.add_argument("-D", "--define", dest="defines", action="append", default=[], help="预定义宏，可重复")
        sub.add_argument("--flag", dest="flags", action="append", default=[], help="额外编译参数，可重复")
        sub.add_argument("--compile-commands", type=Path, default=None, help="compile_commands.json 路径")
        sub.add_argument("--max-tus", type=int, default=500, help="最多分析的编译单元数（默认 500）")
        sub.add_argument("--exclude", dest="excludes", action="append", default=[], help="扫描时跳过的目录名，可重复（如 test、third_party）")
        sub.add_argument("--jobs", "-j", type=int, default=0, help="并行编译数（默认 CPU 核数）")
        sub.add_argument("--full-compile", action="store_true", help="完整编译而非 -fsyntax-only（更慢，但含代码生成阶段）")
        sub.add_argument("--pch", action="store_true", help="附带估算预编译头（PCH）收益")
        sub.add_argument("--pch-top", type=int, default=5, help="PCH 考虑纳入的头文件数（默认 5）")
        sub.add_argument("--forward-decl", action="store_true", help="寻找可改为前向声明的 include")
        sub.add_argument("--timeout", type=float, default=300.0, help="单个编译单元超时秒数")

    scan = subparsers.add_parser("scan", help="分析源码树并生成报告")
    add_common(scan)
    scan.add_argument("--format", choices=["md", "html", "json"], default="md", help="输出格式（默认 md）")
    scan.add_argument("-o", "--output", type=Path, default=None, help="输出文件（默认打印到标准输出）")
    scan.add_argument("--save", type=Path, default=None, help="把扫描结果存为基线 JSON")
    scan.add_argument("--history", type=Path, default=None, help="追加到历史文件，用于趋势图")
    scan.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="增量扫描缓存文件：复用未改动的编译单元，只重编受影响的（默认不启用）",
    )
    scan.add_argument(
        "--probe",
        action="store_true",
        help=(
            "扫描后直接实测候选：逐个真的编译「去掉这行 include」的版本，"
            "只对编译通过的候选做耗时测量。不加这个开关时报告里只有估算"
        ),
    )
    scan.add_argument(
        "--probe-top",
        type=int,
        default=0,
        help="最多实测多少个候选（默认 0 = 全部；按预计收益从高到低）",
    )
    scan.add_argument(
        "--probe-budget",
        type=float,
        default=0.0,
        help="实测阶段的时间预算（秒，默认 0 = 不限）。到点干净停下并报告进度",
    )
    scan.add_argument(
        "--probe-jobs", type=int, default=4, help="实测阶段的并行编译数（默认 4）"
    )
    scan.add_argument(
        "--semantic",
        action="store_true",
        help=(
            "额外用 clang-include-cleaner（LLVM 工具链自带）做 AST 级交叉检查："
            "当它把同一个 include 也判定为可删时，作为佐证写进建议。需要本机装有"
            "该工具，未找到时自动跳过（结论仍以本工具实测为准）"
        ),
    )

    verify = subparsers.add_parser("verify", help="实测：移除候选 include 后重新编译并计时")
    add_common(verify)
    verify.add_argument("--top", type=int, default=5, help="验证前 N 个候选（默认 5）")
    verify.add_argument("--runs", type=int, default=3, help="每侧重复编译次数，取最小值（默认 3）")

    gate = subparsers.add_parser("gate", help="CI 门禁：头文件成本相对基线回归则失败")
    add_common(gate)
    gate.add_argument("--baseline", type=Path, required=True, help="基线 JSON 路径")
    gate.add_argument("--max-regression", type=float, default=5.0, help="允许的归因行数回归百分比（默认 5）")
    gate.add_argument("--update-baseline", action="store_true", help="通过门禁时更新基线")
    gate.add_argument("--report", type=Path, default=None, help="同时写出 HTML 报告")

    compare = subparsers.add_parser("compare", help="对比两份已保存的扫描结果")
    compare.add_argument("before", type=Path, help="基线 JSON")
    compare.add_argument("after", type=Path, help="新扫描 JSON")
    compare.add_argument("--format", choices=["md", "json"], default="md")

    history = subparsers.add_parser(
        "history", help="查看构建成本历史趋势（来自 scan --history 记录）"
    )
    history.add_argument("history_file", type=Path, help="scan --history 生成的 JSONL 文件")
    history.add_argument(
        "--serve",
        action="store_true",
        help="启动本地看板服务（仅绑回环地址 127.0.0.1，关掉终端即结束）",
    )
    history.add_argument(
        "--port", type=int, default=8080, help="--serve 时监听的端口（默认 8080）"
    )
    history.add_argument(
        "--no-browser", action="store_true", help="--serve 时不自动打开浏览器"
    )
    history.add_argument(
        "--html", type=Path, default=None, help="把看板 HTML 写到文件（不自启服务）"
    )

    prc = subparsers.add_parser(
        "pr-comment",
        help="把两次扫描的对比渲染成 PR 评论（可选经 gh 发布）",
    )
    prc.add_argument("before", type=Path, help="基线 JSON（如主分支的扫描结果）")
    prc.add_argument("after", type=Path, help="本次 PR 的扫描 JSON")
    prc.add_argument(
        "--post",
        action="store_true",
        help="经 gh 发布到 PR（需 gh CLI；默认只渲染到标准输出）",
    )
    prc.add_argument(
        "--pr",
        default=None,
        help="PR 编号；缺省时从 GITHUB_REF / PR_NUMBER 等环境变量推断",
    )
    prc.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="成本回归超阈值时退出码为 1（让 CI 步骤失败）",
    )
    prc.add_argument(
        "--update-baseline",
        action="store_true",
        help="门禁通过时用 after 覆盖 before 基线",
    )
    prc.add_argument(
        "--max-regression",
        type=float,
        default=5.0,
        help="允许的归因行数回归百分比（默认 5）",
    )
    prc.add_argument(
        "--heavy-header-lines",
        type=int,
        default=2000,
        help="判定「新增重型头文件」的行数阈值（默认 2000）",
    )
    prc.add_argument(
        "--out",
        type=Path,
        default=None,
        help="同时把评论 Markdown 写到文件",
    )
    prc.add_argument(
        "--title",
        default="HeavyHeader 构建成本报告",
        help="评论标题",
    )

    tool = subparsers.add_parser(
        "toolchains", help="同一份代码在多个编译器下的构建成本对比"
    )
    add_common(tool, with_compiler=False)
    tool.add_argument(
        "--compilers",
        default="g++,clang++",
        help="逗号分隔的编译器列表（默认 g++,clang++）",
    )
    tool.add_argument(
        "--format", choices=["md", "json"], default="md", help="输出格式（默认 md）"
    )
    tool.add_argument(
        "-o", "--output", type=Path, default=None, help="输出文件（默认打印到标准输出）"
    )
    tool.add_argument(
        "--top", type=int, default=15, help="头文件差异表最多显示多少行（默认 15）"
    )

    return parser


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------


def _scan_options_for(args, compiler: str) -> ScanOptions:
    """Build scan options for one compiler.

    ``toolchains`` runs the same argument set against several compilers, so the
    compiler is a parameter rather than read off ``args`` (that parser has no
    single ``--compiler``).  Every other flag is shared, which keeps the matrix
    comparable: all compilers are driven with identical include paths, defines
    and standards.
    """
    return ScanOptions(
        root=Path(getattr(args, "path", ".")),
        compiler=compiler,
        standard=args.std,
        include_dirs=list(args.includes),
        defines=list(args.defines),
        extra_flags=list(args.flags),
        compile_commands=args.compile_commands,
        max_tus=args.max_tus,
        jobs=args.jobs,
        full_compile=args.full_compile,
        timeout=args.timeout,
        exclude_dirs=set(getattr(args, "excludes", []) or []),
        pch=getattr(args, "pch", False),
        pch_top=getattr(args, "pch_top", 5),
        forward_decl=getattr(args, "forward_decl", False),
        probe=getattr(args, "probe", False),
        probe_top=getattr(args, "probe_top", 0),
        probe_budget=getattr(args, "probe_budget", 0.0),
        probe_jobs=getattr(args, "probe_jobs", 4),
        probe_runs=getattr(args, "probe_runs", 3),
    )


def _scan_options_from_args(args) -> ScanOptions:
    return _scan_options_for(args, args.compiler)


def _build_semantic_backend(args):
    """构造可选的 AST 交叉检查后端；未启用或工具缺失时返回 None。"""
    if not getattr(args, "semantic", False):
        return None
    compiler = getattr(args, "compiler", "")
    extra = default_mingw_args() if ("g++" in compiler or "mingw" in compiler.lower()) else None
    backend = SemanticBackend(extra_args=extra)
    if not backend.available:
        print(
            "警告：--semantic 已启用但未找到 clang-include-cleaner，跳过 AST 交叉检查。",
            file=sys.stderr,
        )
        return None
    return backend


def _semantic_build_path(args):
    from pathlib import Path

    cc = getattr(args, "compile_commands", None)
    return Path(cc).parent if cc else None


def _run_scan(args) -> ProjectScan:
    driver = detect_driver(args.compiler)
    cache_path = getattr(args, "cache", None)
    options = _scan_options_from_args(args)
    backend = _build_semantic_backend(args)
    if backend is not None:
        options.semantic = backend
        options.semantic_build_path = _semantic_build_path(args) or options.root
    scan = scan_project(options, driver=driver, cache_path=cache_path)
    # 复现命令：报告里每条实测结论都要能让读者在自己机器上重跑。
    # 没有它，「实测」就只是我们机器上的一串数字。
    scan.reproduce_command = _reproduce_command(args)
    if cache_path is not None:
        _report_cache_stats(scan)
    return scan


def _reproduce_command(args) -> str:
    """Rebuild the ``heavyheader scan`` invocation from the parsed args.

    Only flags that actually exist on the ``scan`` subparser are emitted --
    a command that cannot run is worse than no command.  ``--probe-runs``
    is deliberately absent: it is a library knob, not a CLI flag.
    """
    parts = [
        "heavyheader",
        "scan",
        str(getattr(args, "path", ".")),
        f"--compiler {args.compiler}",
    ]
    if getattr(args, "std", None):
        parts.append(f"--std {args.std}")
    for directory in getattr(args, "includes", []) or []:
        parts.append(f"-I {directory}")
    for define in getattr(args, "defines", []) or []:
        parts.append(f"-D {define}")
    for flag in getattr(args, "flags", []) or []:
        parts.append(str(flag))
    if getattr(args, "compile_commands", None):
        parts.append(f"--compile-commands {args.compile_commands}")
    if getattr(args, "probe", False):
        parts.append("--probe")
        if getattr(args, "probe_top", 0):
            parts.append(f"--probe-top {args.probe_top}")
        if getattr(args, "probe_budget", 0.0):
            parts.append(f"--probe-budget {args.probe_budget}")
    if getattr(args, "semantic", False):
        parts.append("--semantic")
    return " ".join(parts)


def _report_cache_stats(scan: ProjectScan) -> None:
    """把复用/重编计数打到 stderr，且不污染报告输出（报告可能重定向到文件）。"""
    stats = getattr(scan, "cache_stats", None)
    if stats is None or stats.total == 0:
        return
    if stats.cache_loaded and stats.fingerprint_matched:
        print(
            f"增量：复用 {stats.reused}/{stats.total} 个编译单元"
            f"（跳过 {stats.reused} 次编译，{stats.reused_percent:.0f}%），"
            f"重编 {stats.compiled} 个。",
            file=sys.stderr,
        )
    else:
        reason = (
            "编译选项已变化，缓存全部作废"
            if stats.cache_loaded
            else "没有可用缓存（首次运行）"
        )
        print(
            f"增量：{reason}，本次为全量扫描（{stats.compiled} 个编译单元）。",
            file=sys.stderr,
        )


def _emit(text: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    print(f"已写入 {output}", file=sys.stderr)


def _append_history(path: Path, scan: ProjectScan) -> None:
    """Append one line per scan so ``scan`` calls build a trend over time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "attributed_lines": scan.total_included_lines(),
        "seconds": round(scan.baseline_seconds, 4),
        "tus": len(scan.ok_tus),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_json_line(entry) + "\n")


def _json_line(payload: dict) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


def _load_trend(path: Path) -> list[TrendPoint]:
    from .dashboard import load_trend

    return load_trend(path)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _cmd_scan(args) -> int:
    scan = _run_scan(args)

    if args.save:
        save_baseline(args.save, scan)
        print(f"基线已保存：{args.save}", file=sys.stderr)
    if args.history:
        _append_history(args.history, scan)

    if args.format == "json":
        payload = scan_to_dict(scan)
        if args.history:
            payload["trend"] = [
                {"label": p.label, "attributed_lines": p.attributed_lines, "seconds": p.seconds}
                for p in _load_trend(args.history)
            ]
        _emit(_json_pretty(payload), args.output)
    elif args.format == "html":
        trend = _load_trend(args.history) if args.history else None
        _emit(render_html(scan, trend=trend), args.output)
    else:
        _emit(render_markdown(scan), args.output)

    if scan.failed_tus:
        print(
            f"警告：{len(scan.failed_tus)} 个编译单元失败，报告中的数字可能偏低。",
            file=sys.stderr,
        )
    print(markdown_summary_line(scan), file=sys.stderr)
    return 0


def _json_pretty(payload: dict) -> str:
    import json

    return json.dumps(payload, indent=2, ensure_ascii=False)


def _cmd_verify(args) -> int:
    scan = _run_scan(args)
    if not scan.candidates:
        print("没有发现可精简的 include 候选。", file=sys.stderr)
        return 0

    driver = detect_driver(args.compiler)
    options = _scan_options_from_args(args).driver_options()
    backend = _build_semantic_backend(args)
    results = verify_candidates(
        scan,
        driver,
        options,
        verify=VerifyOptions(top=args.top, runs=args.runs, timeout=args.timeout),
        include_dirs=list(args.includes),
        semantic=backend,
        build_path=_semantic_build_path(args) or scan.root,
    )

    print(f"# HeavyHeader 实测验证（每侧 {args.runs} 次，取最小值）\n")
    improved = 0
    # 同名头会让两行输出长得一模一样（都叫 thing.h），读者无从判断哪条是哪条。
    # 出现重名时补上一级目录来区分。
    names = [r.candidate.header.name for r in results]
    duplicated = {name for name in names if names.count(name) > 1}
    for result in results:
        candidate = result.candidate
        shown = candidate.header.name
        if shown in duplicated:
            parent = candidate.header.parent.name
            if parent:
                shown = f"{parent}/{shown}"
        print(f"## {shown} ← {candidate.tu.name}")
        print(f"   状态：{result.status} — {result.message}")
        if result.seconds_saved is not None and result.status == "improved":
            improved += 1
            print(
                f"   实测节省：{result.seconds_saved * 1000:.0f} ms "
                f"({result.percent_saved:.1f}%)"
            )
        print()

    print(f"小结：{improved}/{len(results)} 个候选实测有效。")
    print(
        "注意：编译通过只说明语法上可移除，不等于语义上未使用。"
        "精确的未使用判定需要 AST 级分析，见 ROADMAP。"
    )
    return 0


def _cmd_gate(args) -> int:
    try:
        baseline = load_baseline(args.baseline)
    except BaselineError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    scan = _run_scan(args)
    result = evaluate_gate(
        baseline,
        scan,
        max_regression_percent=args.max_regression,
    )

    comparison = result.comparison
    print(
        f"头文件解析量：{comparison.attributed_lines_before:,} → "
        f"{comparison.attributed_lines_after:,} "
        f"({comparison.attributed_percent:+.1f}%)"
    )
    print(
        f"编译耗时：{comparison.total_seconds_before:.2f}s → "
        f"{comparison.total_seconds_after:.2f}s "
        f"({comparison.seconds_percent:+.1f}%)"
    )

    # 警告无论通过与否都要打印。以前只在失败分支打印，于是「编译单元从 12
    # 掉到 3」这类信号恰好在门禁通过（最需要被看见的时候）被吞掉。
    for warning in result.warnings:
        print(f"警告：{warning}", file=sys.stderr)

    if result.passed:
        print(f"门禁通过：成本回归在 {args.max_regression:+.1f}% 预算内。")
        if args.update_baseline:
            # 别用一份没有成功编译单元的扫描覆盖掉好基线——下一轮会拿它
            # 算出莫名其妙的 +100% 回归。
            if scan.ok_tus:
                save_baseline(args.baseline, scan)
                print(f"基线已更新：{args.baseline}")
            else:
                print(
                    "警告：本次扫描没有成功的编译单元，拒绝覆盖基线"
                    f"（{args.baseline} 保持不变）。",
                    file=sys.stderr,
                )
        if args.report:
            _emit(render_html(scan), args.report)
        return 0

    print("门禁失败：", file=sys.stderr)
    for reason in result.reasons:
        print(f"  - {reason}", file=sys.stderr)
    if args.report:
        _emit(render_html(scan), args.report)
    return 1


def _cmd_compare(args) -> int:
    try:
        before = load_baseline(args.before)
        after = load_baseline(args.after)
    except BaselineError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    comparison = compare_scans(before, after)

    if args.format == "json":
        payload = {
            "attributed_lines_before": comparison.attributed_lines_before,
            "attributed_lines_after": comparison.attributed_lines_after,
            "attributed_percent": round(comparison.attributed_percent, 3),
            "seconds_before": round(comparison.total_seconds_before, 4),
            "seconds_after": round(comparison.total_seconds_after, 4),
            "seconds_percent": round(comparison.seconds_percent, 3),
            "tu_count_before": comparison.tu_count_before,
            "tu_count_after": comparison.tu_count_after,
            "regressed_headers": [
                {
                    "path": str(d.path),
                    "before": d.before,
                    "after": d.after,
                    "percent": round(d.percent, 2),
                }
                for d in comparison.regressed_headers[:20]
            ],
            "improved_headers": [
                {
                    "path": str(d.path),
                    "before": d.before,
                    "after": d.after,
                    "percent": round(d.percent, 2),
                }
                for d in comparison.improved_headers[:20]
            ],
        }
        print(_json_pretty(payload))
        return 0

    lines = [
        "# HeavyHeader 扫描对比",
        "",
        f"- 头文件解析量：{comparison.attributed_lines_before:,} → "
        f"{comparison.attributed_lines_after:,}（{comparison.attributed_percent:+.1f}%）",
        f"- 编译耗时：{comparison.total_seconds_before:.2f}s → "
        f"{comparison.total_seconds_after:.2f}s（{comparison.seconds_percent:+.1f}%）",
        f"- 编译单元：{comparison.tu_count_before} → {comparison.tu_count_after}",
        f"- 缺 guard 头文件：{comparison.unguarded_before} → {comparison.unguarded_after}",
        "",
    ]
    if comparison.regressed_headers:
        lines += ["## 成本上升的头文件", "", "| 头文件 | 之前 | 之后 | 变化 |", "|---|---:|---:|---:|"]
        for delta in comparison.regressed_headers[:15]:
            lines.append(
                f"| `{delta.path}` | {delta.before:,} | {delta.after:,} | {delta.percent:+.1f}% |"
            )
        lines.append("")
    if comparison.improved_headers:
        lines += ["## 成本下降的头文件", "", "| 头文件 | 之前 | 之后 | 变化 |", "|---|---:|---:|---:|"]
        for delta in comparison.improved_headers[:15]:
            lines.append(
                f"| `{delta.path}` | {delta.before:,} | {delta.after:,} | {delta.percent:+.1f}% |"
            )
        lines.append("")
    print("\n".join(lines))
    return 0


def _cmd_history(args) -> int:
    from .dashboard import load_trend, render_dashboard, serve, summarize

    history_path = Path(args.history_file)
    points = load_trend(history_path)

    if args.serve:
        serve(history_path, port=args.port, open_browser=not args.no_browser)
        return 0

    if args.html is not None:
        output = Path(args.html)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_dashboard(points), encoding="utf-8")
        print(f"看板已写出：{output}", file=sys.stderr)
        return 0

    # 默认：终端摘要，能直接落进 CI 日志
    if not points:
        print(f"历史文件 {history_path} 没有可用记录。", file=sys.stderr)
        return 0

    s = summarize(points)
    lines = [
        f"# 构建成本趋势 · {history_path.name}",
        "",
        f"- 扫描次数：{s.count}",
        f"- 当前趋势：**{s.trend_label}**",
        f"- 当前归因行数：{s.last_lines:,}（起点 {s.first_lines:,}）",
        f"- 净变化：{s.delta_lines:+,} 行（{s.delta_percent:+.1f}%）",
        f"- 历史峰值：{s.peak_lines:,}（第 {s.peak_index + 1} 次扫描）",
        f"- 最大单步回归：{s.worst_step_percent:+.1f}%（{s.worst_step_label}）",
        f"- 最大单步改善：{s.best_step_percent:+.1f}%（{s.best_step_label}）",
        "",
        "运行 `heavyheader history <文件> --serve` 在浏览器查看走势图；"
        "或 `--html out.html` 导出一份自包含网页。",
    ]
    print("\n".join(lines))
    return 0


def _cmd_pr_comment(args) -> int:
    from .prcomment import run_pr_comment

    return run_pr_comment(
        before=args.before,
        after=args.after,
        pr=args.pr,
        post=args.post,
        fail_on_regression=args.fail_on_regression,
        update_baseline=args.update_baseline,
        max_regression=args.max_regression,
        heavy_header_lines=args.heavy_header_lines,
        out=args.out,
        title=args.title,
    )


def _cmd_toolchains(args) -> int:
    from .toolchains import render_toolchains, scan_toolchains, to_dict

    # 去重但保序：`--compilers g++,g++` 是笔误，没必要把同一个编译器扫两遍，
    # 那只会白花一倍时间，还让「最快」在两次同源结果之间比出毫无意义的差值。
    compilers = list(
        dict.fromkeys(c.strip() for c in args.compilers.split(",") if c.strip())
    )
    if not compilers:
        print("错误：--compilers 为空，至少给一个编译器。", file=sys.stderr)
        return 2

    comparison = scan_toolchains(
        Path(getattr(args, "path", ".")),
        compilers,
        options_factory=lambda compiler: _scan_options_for(args, compiler),
    )

    if args.format == "json":
        _emit(_json_pretty(to_dict(comparison)), args.output)
    else:
        _emit(render_toolchains(comparison, top=args.top), args.output)

    # 一个都没成功 = 一次有效对比都没有，明确失败，不假装成功。
    if not comparison.ok_costs:
        print("错误：没有任何编译器成功编译，无法生成对比。", file=sys.stderr)
        return 2
    return 0


_COMMANDS = {
    "scan": _cmd_scan,
    "verify": _cmd_verify,
    "gate": _cmd_gate,
    "compare": _cmd_compare,
    "history": _cmd_history,
    "pr-comment": _cmd_pr_comment,
    "toolchains": _cmd_toolchains,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces this
        parser.error(f"unknown command {args.command!r}")
    try:
        return handler(args)
    except (ScanError, CompilationError, BaselineError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
