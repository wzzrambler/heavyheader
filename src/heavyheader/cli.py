"""HeavyHeader command line interface.

Four verbs, each doing one thing:

======================  ====================================================
``scan``                analyse a tree, write a report
``verify``              recompile without candidate includes and measure
``gate``                fail a build when header cost regresses
``compare``             diff two saved scans
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
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser, *, with_path: bool = True) -> None:
        if with_path:
            sub.add_argument(
                "path",
                type=Path,
                nargs="?",
                default=Path("."),
                help="要分析的源码树（默认当前目录）",
            )
        sub.add_argument("--compiler", default="g++", help="编译器可执行文件（默认 g++）")
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

    return parser


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------


def _scan_options_from_args(args) -> ScanOptions:
    return ScanOptions(
        root=Path(getattr(args, "path", ".")),
        compiler=args.compiler,
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
    )


def _run_scan(args) -> ProjectScan:
    driver = detect_driver(args.compiler)
    return scan_project(_scan_options_from_args(args), driver=driver)


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
    import json

    points: list[TrendPoint] = []
    if not path.is_file():
        return points
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        stamp = entry.get("timestamp", "")
        label = stamp[5:16].replace("T", " ") if stamp else str(len(points))
        points.append(
            TrendPoint(
                label=label,
                attributed_lines=int(entry.get("attributed_lines", 0)),
                seconds=float(entry.get("seconds", 0.0)),
            )
        )
    return points


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
    results = verify_candidates(
        scan,
        driver,
        options,
        verify=VerifyOptions(top=args.top, runs=args.runs, timeout=args.timeout),
        include_dirs=list(args.includes),
    )

    print(f"# HeavyHeader 实测验证（每侧 {args.runs} 次，取最小值）\n")
    improved = 0
    for result in results:
        candidate = result.candidate
        print(f"## {candidate.header.name} ← {candidate.tu.name}")
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

    if result.passed:
        print(f"门禁通过：成本回归在 {args.max_regression:+.1f}% 预算内。")
        if args.update_baseline:
            save_baseline(args.baseline, scan)
            print(f"基线已更新：{args.baseline}")
        if args.report:
            _emit(render_html(scan), args.report)
        return 0

    print("门禁失败：", file=sys.stderr)
    for reason in result.reasons:
        print(f"  - {reason}", file=sys.stderr)
    for warning in result.warnings:
        print(f"  ! {warning}", file=sys.stderr)
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


_COMMANDS = {
    "scan": _cmd_scan,
    "verify": _cmd_verify,
    "gate": _cmd_gate,
    "compare": _cmd_compare,
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
