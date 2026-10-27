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
import dataclasses
import datetime as _dt
import shutil
import sys
import tempfile
from pathlib import Path

from . import __version__, codegen, evidence
from .attrib.edgecost import aggregate_edge_costs
from .attrib.fwdverify import run_forward_trial
from .attrib.pchbuild import run_pch_trial
from .attrib.semantic import SemanticBackend, default_mingw_args
from .baseline import (
    BaselineError,
    compare_scans,
    evaluate_gate,
    load_baseline,
    save_baseline,
)
from .blame import BlameError, blame_baselines
from .blame import render_markdown as render_blame
from .drivers.base import CompilationError, detect_driver
from .edgecollect import EdgeCostError, collect_edge_units
from .models import ProjectScan
from .report.edges import edges_to_dict, render_edges_markdown
from .report.html import TrendPoint, render_html
from .report.jsonio import scan_to_dict
from .report.markdown import (
    compile_health_note,
    markdown_summary_line,
    render_forward_trial,
    render_markdown,
    render_pch_trial,
)
from .scanner import ScanError, ScanOptions, discover_translation_units, scan_project
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
        "  %(prog)s scan ./src --history cost.jsonl\n"
        "  %(prog)s history cost.jsonl --serve\n"
        "  %(prog)s pr-comment --before baseline.json --after pr.json --post\n"
        "  %(prog)s toolchains ./src --compilers g++,clang++\n"
        "  %(prog)s pch-verify ./src --runs 2\n"
        "  %(prog)s forward-verify ./src --max-trials 10\n"
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
        sub.add_argument(
            "-I",
            "--include",
            dest="includes",
            action="append",
            default=[],
            type=Path,
            help="额外头文件搜索路径，可重复",
        )
        sub.add_argument(
            "-D",
            "--define",
            dest="defines",
            action="append",
            default=[],
            help="预定义宏，可重复",
        )
        sub.add_argument(
            "--flag",
            dest="flags",
            action="append",
            default=[],
            help="额外编译参数，可重复",
        )
        sub.add_argument(
            "--compile-commands",
            type=Path,
            default=None,
            help="compile_commands.json 路径",
        )
        sub.add_argument(
            "--max-tus",
            type=int,
            default=500,
            help="最多分析的编译单元数（默认 500）",
        )
        sub.add_argument(
            "--exclude",
            dest="excludes",
            action="append",
            default=[],
            help="扫描时跳过的目录名，可重复（如 test、third_party）",
        )
        sub.add_argument("--jobs", "-j", type=int, default=0, help="并行编译数（默认 CPU 核数）")
        sub.add_argument(
            "--full-compile",
            action="store_true",
            help="完整编译而非 -fsyntax-only（更慢，但含代码生成阶段）",
        )
        sub.add_argument("--pch", action="store_true", help="附带估算预编译头（PCH）收益")
        sub.add_argument("--pch-top", type=int, default=5, help="PCH 考虑纳入的头文件数（默认 5）")
        sub.add_argument("--forward-decl", action="store_true", help="寻找可改为前向声明的 include")
        sub.add_argument(
            "--header-sites",
            action="store_true",
            help="实验性：也把头文件里的 #include 当作候选（改动会影响所有解析它的编译单元）",
        )
        sub.add_argument(
            "--ungated-sites",
            action="store_true",
            help="实验性：完全关掉可行性闸门，所有收益非零的站点都进候选，"
            "由 verify 逐条用编译裁决（小工程验证便宜时换召回用）",
        )
        sub.add_argument(
            "--allow-unknown-sites",
            action="store_true",
            help="实验性：--header-sites 下，把「文本判定给不出结论」的站点也放进候选"
            "（用一次编译去换召回；这类候选会被标为低可信度）",
        )
        sub.add_argument(
            "--include-system-sites",
            action="store_true",
            help=(
                "实验性：--header-sites 下也列出系统/第三方头文件内部的 "
                "include 行（仅供查看，不建议改）"
            ),
        )
        sub.add_argument(
            "--cost-model",
            choices=("lines", "trace"),
            default="lines",
            help="实验性：候选排序依据。trace = 用 -ftime-trace 实测的每头文件自身耗时"
            "（需 clang，会额外真编译一遍全项目）；lines = 归因行数估算（默认）",
        )
        sub.add_argument(
            "--timeout", type=float, default=300.0, help="单个编译单元超时秒数"
        )

    scan = subparsers.add_parser("scan", help="分析源码树并生成报告")
    add_common(scan)
    scan.add_argument(
        "--format",
        choices=["md", "html", "json"],
        default="md",
        help="输出格式（默认 md）",
    )
    scan.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="输出文件（默认打印到标准输出）",
    )
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
        "--probe-compile",
        action="store_true",
        help=(
            "轻量版实测：每条候选删一次、编译一次，只回答「删了还能编吗」，"
            "不测时间、不跑复测。成本约为 --probe 的 1/6"
        ),
    )
    scan.add_argument(
        "--check-codegen",
        action="store_true",
        help=(
            "实测阶段加一道代码等价校验：删除后用汇编哈希比对，"
            "确认编出来的还是同一个程序（每个候选多两次编译）"
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
    verify.add_argument(
        "--top",
        type=int,
        default=5,
        help="验证前 N 个候选（默认 5）：疑似未使用优先，其次无法判定；"
        "「在用」默认不测，加 --ungated-sites 才测",
    )
    verify.add_argument("--runs", type=int, default=3, help="每侧重复编译次数，取最小值（默认 3）")
    verify.add_argument(
        "--confirm",
        type=int,
        default=2,
        help="判为 improved 后复测几遍，全部通过才保留结论（默认 2；1 = 不复测）",
    )

    verify.add_argument(
        "--check-codegen",
        dest="check_codegen",
        action="store_true",
        default=True,
        help=(
            "代码等价校验（默认开）：删除后用汇编哈希比对，确认编出来的还是同一个程序"
            "（每个候选多两次编译，实测约 +12%% 耗时）"
        ),
    )
    verify.add_argument(
        "--no-check-codegen",
        dest="check_codegen",
        action="store_false",
        help=(
            "关掉代码等价校验，只判「能编过」。更快，但「能编过」不等于「代码没变」"
        ),
    )

    verify.add_argument(
        "--tier",
        choices=["all", "verified", "equivalent", "compiles"],
        default="all",
        help="只看某一档证据的结论（默认 all）。verified = 汇编一致+省时过线+复测"
        "成立；equivalent = 汇编一致但收益未过线；compiles = 只验过能编过",
    )

    verified = subparsers.add_parser(
        "verified",
        help="已验证层级：只列编译器证据（汇编一致+复测）撑住的候选",
    )
    add_common(verified)
    verified.add_argument(
        "--top",
        type=int,
        default=20,
        help="验证前 N 个候选（默认 20）",
    )
    verified.add_argument(
        "--runs", type=int, default=3, help="每侧重复编译次数，取最小值（默认 3）"
    )
    verified.add_argument(
        "--confirm",
        type=int,
        default=2,
        help="判为 improved 后复测几遍（默认 2；1 = 不复测）",
    )
    verified.add_argument(
        "--include-equivalent",
        action="store_true",
        help="把「已证等价但收益未过线」那一档也列出来（默认不列：程序没变已证，"
        "值不值得删未证）",
    )
    verified.add_argument("-o", "--output", type=Path, default=None, help="同时写出 JSON")

    pchv = subparsers.add_parser(
        "pch-verify",
        help="实测：真的生成一份 PCH 并重编，量出它省了多少（而不是估算）",
    )
    add_common(pchv)
    pchv.add_argument(
        "--runs",
        type=int,
        default=2,
        help="每侧重复编译次数，取最小值（默认 2）",
    )
    pchv.add_argument(
        "--full",
        action="store_true",
        help="走完整编译（-c 产出目标文件）而不是 -fsyntax-only；更接近真实构建，也慢得多",
    )
    pchv.add_argument(
        "--max-units",
        type=int,
        default=0,
        help="最多测多少个编译单元（默认 0 = 该选项组内全部）",
    )
    pchv.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="PCH 与中间产物的存放目录（默认临时目录，跑完删除）。"
        "想复核 .gch 本身时指定一个目录把它留下",
    )
    pchv.add_argument(
        "--format", choices=["md", "json"], default="md", help="输出格式（默认 md）"
    )
    pchv.add_argument("-o", "--output", type=Path, default=None, help="输出文件")

    fwdv = subparsers.add_parser(
        "forward-verify",
        help="实测：真的把 include 换成前向声明再重编，看它编不编得过、省多少",
    )
    add_common(fwdv)
    fwdv.add_argument(
        "--runs", type=int, default=2, help="每侧重复编译次数，取最小值（默认 2）"
    )
    fwdv.add_argument(
        "--max-trials",
        type=int,
        default=10,
        help="最多真的测多少条候选（默认 10）。每条要编两次以上，"
        "大工程上全测会很慢",
    )
    fwdv.add_argument(
        "--no-codegen-check",
        action="store_true",
        help="跳过汇编指纹比对。默认比对：编译通过不等于程序没变，"
        "少了这一步最高只能报「仅编译通过」",
    )
    fwdv.add_argument(
        "--format", choices=["md", "json"], default="md", help="输出格式（默认 md）"
    )
    fwdv.add_argument("-o", "--output", type=Path, default=None, help="输出文件")

    gate = subparsers.add_parser("gate", help="CI 门禁：头文件成本相对基线回归则失败")
    add_common(gate)
    gate.add_argument("--baseline", type=Path, required=True, help="基线 JSON 路径")
    gate.add_argument(
        "--max-regression",
        type=float,
        default=5.0,
        help="允许的归因行数回归百分比（默认 5）",
    )
    gate.add_argument("--update-baseline", action="store_true", help="通过门禁时更新基线")
    gate.add_argument("--report", type=Path, default=None, help="同时写出 HTML 报告")

    configs = subparsers.add_parser(
        "configs",
        help="同一棵源码树按多套构建配置各扫一遍，只报判定变了的候选",
    )
    add_common(configs)
    configs.add_argument(
        "--config",
        dest="configs",
        action="append",
        default=[],
        metavar="名字:参数",
        help='一套配置，如 "mt:-DZSTD_MULTITHREAD"；第一套作基准，可重复',
    )
    configs.add_argument("--format", choices=["md", "json"], default="md")
    configs.add_argument(
        "-o", "--output", type=Path, default=None, help="输出文件（默认 stdout）"
    )

    compare = subparsers.add_parser("compare", help="对比两份已保存的扫描结果")
    compare.add_argument("before", type=Path, help="基线 JSON")
    compare.add_argument("after", type=Path, help="新扫描 JSON")
    compare.add_argument("--format", choices=["md", "json"], default="md")

    bl = subparsers.add_parser(
        "blame",
        help="成本考古：把头文件成本回归归因到 commit、文件与 include 行",
    )
    bl.add_argument("before", type=Path, help="回归前的基线 JSON")
    bl.add_argument("after", type=Path, help="回归后的基线 JSON")
    bl.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="git 仓库路径（默认取 before 基线记录的扫描根目录）",
    )
    bl.add_argument(
        "--from",
        dest="from_rev",
        default=None,
        help="起始 revision（默认取 before 基线扫描时记录的 commit）",
    )
    bl.add_argument(
        "--to",
        dest="to_rev",
        default=None,
        help="结束 revision（默认取 after 基线扫描时记录的 commit）",
    )
    bl.add_argument("--format", choices=["md", "json"], default="md")

    edge = subparsers.add_parser(
        "edge-cost",
        help="按 include 边归因解析成本（clang-first，需要真编译一次）",
    )
    edge.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("."),
        help="源码树（默认当前目录）",
    )
    edge.add_argument(
        "--compiler",
        default="clang++",
        help="编译器（默认 clang++，需支持 -ftime-trace）",
    )
    edge.add_argument("--std", default=None, help="语言标准，如 c++17")
    edge.add_argument(
        "-I", "--include", dest="includes", action="append", default=[], type=Path,
        help="额外头文件搜索路径，可重复",
    )
    edge.add_argument(
        "-D", "--define", dest="defines", action="append", default=[], help="预定义宏，可重复"
    )
    edge.add_argument(
        "--flag", dest="flags", action="append", default=[], help="额外编译参数，可重复"
    )
    edge.add_argument(
        "--compile-commands", type=Path, default=None, help="compile_commands.json 路径"
    )
    edge.add_argument("--max-tus", type=int, default=200, help="最多采集的编译单元数（默认 200）")
    edge.add_argument(
        "--jobs", "-j", type=int, default=1, help="并行编译数（默认 1；>1 会拉高耗时噪声）"
    )
    edge.add_argument("--top", type=int, default=15, help="报告里列出的边数（默认 15）")
    edge.add_argument(
        "--effective-only",
        action="store_true",
        help="只用 -H 有效树算闭包（会虚报收益，仅用于对照实验）",
    )
    edge.add_argument("--format", choices=["md", "json"], default="md")
    edge.add_argument("-o", "--output", type=Path, default=None, help="输出文件（默认标准输出）")

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
        header_sites=getattr(args, "header_sites", False),
        include_system_sites=getattr(args, "include_system_sites", False),
        allow_unknown_sites=getattr(args, "allow_unknown_sites", False),
        ungated_sites=getattr(args, "ungated_sites", False),
        cost_model=getattr(args, "cost_model", "lines"),
        probe=getattr(args, "probe", False),
        probe_compile=getattr(args, "probe_compile", False),
        probe_top=getattr(args, "probe_top", 0),
        probe_budget=getattr(args, "probe_budget", 0.0),
        probe_jobs=getattr(args, "probe_jobs", 4),
        probe_runs=getattr(args, "probe_runs", 3),
        check_codegen=getattr(args, "check_codegen", False),
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


def _cmd_pch_verify(args) -> int:
    """真的造一份 PCH，真的重编，把估算换成实测。

    与 ``scan --pch`` 的关系：那条命令给的是**估算**（省多少行），这条给的是
    **实测**（省多少秒，以及估算点名的单元里究竟有几个真的用上了 PCH）。
    两条路刻意分开——合在一条命令里会让「估算」借「实测」的可信度。
    """
    # 实测要用估算给出的头集合，所以这一轮扫描必须算 PCH 建议。
    args.pch = True
    # 扫描阶段一律走语法检查；试验阶段的口径由 --full 单独决定。
    args.full_compile = False
    scan = _run_scan(args)
    driver = detect_driver(args.compiler)

    workdir: Path | None = args.workdir
    temporary = workdir is None
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix="heavyheader-pch-"))
    trial = run_pch_trial(
        scan,
        driver=driver,
        workdir=workdir,
        runs=args.runs,
        full_compile=args.full,
        max_units=args.max_units or None,
        timeout=args.timeout,
    )

    if args.format == "json":
        _emit(_json_pretty(trial.to_dict()), args.output)
    else:
        _emit(render_pch_trial(trial, root=scan.root), args.output)

    if temporary:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"PCH 与中间产物保留在：{workdir}", file=sys.stderr)

    # 退出码：没测成要失败。0 收益是**结论**（值得知道），测不成是**没做**。
    if not trial.pch_built:
        print(f"错误：{trial.pch_error}", file=sys.stderr)
        return 2
    print(
        f"实测 {trial.measured_units} 个单元，{trial.adopted_units} 个用上 PCH，"
        f"节省 {trial.saved_percent:.1f}%。",
        file=sys.stderr,
    )
    return 0


def _cmd_forward_verify(args) -> int:
    """真的把 ``#include`` 换成前向声明，重编、计时、比汇编指纹。

    与 ``scan --forward-decl`` 的关系：那条给的是**文本候选**（正则认为
    「这个类型只当指针/引用用」），这条给的是**编译器和汇编指纹的裁决**。
    两条路刻意分开——合在一条命令里会让文本判据借实测的可信度。
    """
    # 实测要吃文本候选，所以这一轮扫描必须算前向声明建议。
    args.forward_decl = True
    args.full_compile = False
    scan = _run_scan(args)
    driver = detect_driver(args.compiler)

    trial = run_forward_trial(
        scan,
        driver=driver,
        max_trials=args.max_trials,
        runs=args.runs,
        check_codegen=not args.no_codegen_check,
        timeout=args.timeout,
        jobs=args.jobs,
    )

    if args.format == "json":
        _emit(_json_pretty(trial.to_dict()), args.output)
    else:
        _emit(render_forward_trial(trial, root=scan.root), args.output)

    if trial.measured_units == 0 and trial.compile_error_units == 0:
        print("错误：没有一条候选被真的测过，见报告里的原因。", file=sys.stderr)
        return 2
    print(
        f"实测 {trial.measured_units} 条，已证等价 {trial.equivalent_units} 条，"
        f"省 {trial.equivalent_saved_seconds:.2f} s"
        f"（{trial.equivalent_saved_percent:.1f}%）。",
        file=sys.stderr,
    )
    return 0


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
    # ``blame`` 的原料之一：这份基线对应哪个 commit。非 git 树或 git 不可用
    # 时留 None——静默跳过，不让一个可选的归因功能把扫描本身搞失败。
    scan.git_commit = _detect_git_commit(options.root)
    if cache_path is not None:
        _report_cache_stats(scan)
    return scan


def _detect_git_commit(root: Path) -> str | None:
    """Best-effort ``git rev-parse HEAD`` inside the scan root."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


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

    health = compile_health_note(scan)
    if health:
        print(f"警告：{health}", file=sys.stderr)
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
    if getattr(args, "check_codegen", False) and not codegen.supported(driver):
        # 不静默降级：读者必须知道这一轮的「建议删除」只代表「能编过」。
        print(
            f"注意：{driver.family.value} 上暂无汇编指纹比对（-S/-g0 是 GCC/Clang 写法），"
            "本轮未做代码等价校验。",
            file=sys.stderr,
        )
    options = _scan_options_from_args(args).driver_options()
    backend = _build_semantic_backend(args)
    results = verify_candidates(
        scan,
        driver,
        options,
        verify=VerifyOptions(
            top=args.top,
            runs=args.runs,
            timeout=args.timeout,
            confirm_passes=max(1, getattr(args, "confirm", 1)),
            codegen_check=getattr(args, "check_codegen", False),
        ),
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
    wanted = getattr(args, "tier", "all") or "all"
    shown = evidence.select(results, wanted)
    if wanted != "all":
        print(f"已按证据档过滤：只看 {wanted}（{evidence.describe(wanted)}）\n")
    for result in shown:
        candidate = result.candidate
        shown_name = candidate.header.name
        if shown_name in duplicated:
            parent = candidate.header.parent.name
            if parent:
                shown_name = f"{parent}/{shown_name}"
        print(f"## {shown_name} ← {candidate.tu.name}")
        print(f"   状态：{result.status} — {result.message}")
        print(f"   证据档：{_tier_line(result)}")
        if result.seconds_saved is not None and result.status == "improved":
            improved += 1
            print(
                f"   实测节省：{result.seconds_saved * 1000:.0f} ms "
                f"({result.percent_saved:.1f}%)"
            )
        print()

    print(f"小结：{improved}/{len(shown)} 个候选实测有效。")
    _print_tier_summary(results, shown, hint="--tier all 看全部")
    print(
        "注意：编译通过只说明语法上可移除，不等于语义上未使用。"
        "精确的未使用判定需要 AST 级分析，见 ROADMAP。"
    )
    return 0


def _tier_line(result) -> str:
    """一条结论站在哪一层证据上，用一句话说清（不写「已验证」了事）。"""
    tier = evidence.tier_of(result)
    text = f"{evidence.TIER_SHORT.get(tier, tier)} — {evidence.describe(tier)}"
    if tier == evidence.TIER_VERIFIED and getattr(result, "confirm_passes", 0):
        text += f"（复测 {result.confirm_passes} 遍仍成立）"
    return text


def _print_tier_summary(results: list, shown: list, hint: str = "") -> None:
    """分档计数 + 交叉表 + 「判据没推荐却被闸门抬上来」的条数。

    三条一起印是因为它们互相限定：只印分档会让人以为强证据那批就是推荐集；
    只印拦下数不说跑过多少，等于没说闸门有没有工作。
    """
    summary = evidence.summarise(results)
    counts = summary["tiers"]
    parts = "、".join(
        f"{evidence.TIER_SHORT.get(t, t or '无证据')} {n} 条"
        for t, n in counts.items()
    )
    print(f"证据分档（全部 {summary['measured']} 条）：{parts}。")
    status_line = "、".join(f"{k} {v}" for k, v in summary["statuses"].items())
    print(f"本轮状态：{status_line}。")
    if summary["gate_ran"]:
        print(
            f"代码等价闸门：跑了 {summary['gate_ran']} 条，"
            f"拦下 {summary['gate_blocked']} 条（汇编变了，已判 codegen-changed）。"
        )
    else:
        print("代码等价闸门：本轮未跑（--no-check-codegen 或该工具链给不出汇编）。")
    outside = summary["outside_recommendation"]
    if outside:
        print(
            f"其中 {outside} 条被文本判据判为「在用 / 无法判定」，"
            "是闸门证据把它们抬到上两档的——这批是已验证层级的全部增量。"
        )
    if len(shown) != len(results):
        print(
            f"（当前只显示 {len(shown)}/{len(results)} 条。{hint}）"
            if hint
            else f"（当前只显示 {len(shown)}/{len(results)} 条。）"
        )
    elif hint:
        print(f"（{hint}）")


def _cmd_verified(args) -> int:
    """已验证层级：只把**编译器证据**撑住的候选列出来。

    与 ``verify`` 的差别不是输出格式，是**筛选立场**：``verify`` 按文本判据
    排的顺序报（疑似未使用优先），这里按证据强度报。实测里两者几乎不重叠
    （Lua：最高档 9 条里推荐集贡献 0 条）——省时最多的那几条恰好是保守判据
    不敢推荐的重复包含。
    """
    scan = _run_scan(args)
    if not scan.candidates:
        print("没有发现可精简的 include 候选。", file=sys.stderr)
        return 0

    driver = detect_driver(args.compiler)
    if not codegen.supported(driver):
        # 这一层的全部价值就建立在汇编证据上，给不出汇编就必须明说，不能降级。
        print(
            f"错误：{driver.family.value} 上暂无汇编指纹比对（-S/-g0 是 GCC/Clang "
            "写法），已验证层级无法取证。改用 verify 看「仅编译通过」那一档。",
            file=sys.stderr,
        )
        return 2
    options = _scan_options_from_args(args).driver_options()
    results = verify_candidates(
        scan,
        driver,
        options,
        verify=VerifyOptions(
            top=args.top,
            runs=args.runs,
            timeout=args.timeout,
            confirm_passes=max(1, args.confirm),
            codegen_check=True,
            # 这一层按预估收益排，不按文本判据排。判据在这里没有投票权：
            # 实测 8 条落地收益里 7 条被判 in_use / unknown，按判据排的
            # top-N 永远轮不到它们，而它们正是省时最多的那几条。
            order="estimate",
        ),
        include_dirs=list(args.includes),
        semantic=_build_semantic_backend(args),
        build_path=_semantic_build_path(args) or scan.root,
    )

    tiers = [evidence.TIER_VERIFIED]
    if args.include_equivalent:
        tiers.append(evidence.TIER_EQUIVALENT)
    layer = [r for r in results if evidence.tier_of(r) in tiers]
    layer = evidence.sort_by_evidence(layer)

    print("# HeavyHeader 已验证层级\n")
    print(
        "这一层**只收编译器证据**：删掉这一行之后，两侧各编到汇编、归一化后比哈希，"
        "指纹一致才进得了这一层。代码生成一致性是量出来的，不是推断出来的；"
        "它的证明范围受编译器、编译选项与归一化规则限制，本工具把它当保守门槛，"
        "不当形式化证明。\n"
    )
    print(
        "> 它只对**本轮**负责。收益贴着噪声线的候选换一轮可能不在名单上——Lua 实测"
        "同池同协议跑两次，进这一层的成员零重合（2 条 vs 3 条，都在 2%–4% 附近）。\n"
    )
    if not layer:
        print("本轮没有任何候选拿到这一档证据（闸门全拦下，或收益全在噪声线下）。")
    for result in layer:
        candidate = result.candidate
        saved = result.seconds_saved
        gain = (
            f"省 {saved * 1000:.0f} ms（{result.percent_saved:.1f}%）"
            if saved is not None and result.percent_saved is not None
            else "收益未过噪声线（可删，未必值得删）"
        )
        print(
            f"- `{candidate.tu.name}` → `{candidate.header.name}`：{gain}；"
            f"判据 {candidate.usage_verdict or '?'}；{_tier_line(result)}"
        )
    _print_tier_summary(
        results,
        layer,
        hint="--include-equivalent 可把「已证等价但收益未过线」那档一并列出",
    )

    if args.output is not None:
        payload = evidence.summarise(results)
        payload["layer"] = [
            {
                "tu": str(r.candidate.tu),
                "header": str(r.candidate.header),
                "line": r.candidate.line,
                "usage_verdict": r.candidate.usage_verdict,
                "tier": evidence.tier_of(r),
                "status": r.status,
                "seconds_saved": r.seconds_saved,
                "percent_saved": r.percent_saved,
                "confirm_passes": getattr(r, "confirm_passes", 0),
            }
            for r in layer
        ]
        args.output.write_text(_json_pretty(payload), encoding="utf-8")
        print(f"\n已写入 {args.output}")
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


def _cmd_configs(args) -> int:
    """多套构建配置各扫一遍，只汇报判定变化。

    判定层本来就按每条编译命令里的 ``-D`` 求值 ``#ifdef``，所以这里不需要新的
    分析逻辑：把同一份 ``compile_commands.json`` 派生几份、各扫一遍、按
    ``(单元, 头, 行)`` 对齐即可。实测 zstd：`pool.c → allocations.h` 在单线程
    配置里可删（−46.5%），加 ``-DZSTD_MULTITHREAD`` 后删了编不过。
    """
    from .configmatrix import (
        ConfigSpec,
        render_matrix,
        table_of,
        temporary_database,
        verdict_changes,
    )

    if not args.configs:
        print(
            '错误：至少给一套配置，如 --config "single:" --config "mt:-DZSTD_MULTITHREAD"',
            file=sys.stderr,
        )
        return 2
    try:
        specs = [ConfigSpec.parse(raw) for raw in args.configs]
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    names = [spec.name for spec in specs]
    if len(set(names)) != len(names):
        print("错误：配置名重复（名字用来对齐报告表格）。", file=sys.stderr)
        return 2
    if args.compile_commands is None:
        print(
            "错误：configs 需要 --compile-commands（否则没有可派生的编译命令）。",
            file=sys.stderr,
        )
        return 2

    import json

    base_entries = json.loads(Path(args.compile_commands).read_text(encoding="utf-8"))
    tables = {}
    summaries = {}
    for spec in specs:
        db_path, holder = temporary_database(base_entries, spec.flags)
        try:
            original = args.compile_commands
            args.compile_commands = db_path
            try:
                scan = _run_scan(args)
            finally:
                args.compile_commands = original
        finally:
            holder.cleanup()
        tables[spec.name] = table_of(scan.candidates)
        verdicts: dict[str, int] = {}
        for row in tables[spec.name].values():
            verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
        summaries[spec.name] = {
            "tu": len(scan.ok_tus),
            "failed": len(scan.failed_tus),
            "baseline_seconds": scan.baseline_seconds,
            "candidates": len(tables[spec.name]),
            "verdicts": verdicts,
        }
        print(
            f"[{spec.name:<10}] 单元 {len(scan.ok_tus)} "
            f"候选 {len(tables[spec.name])} {verdicts}",
            file=sys.stderr,
        )

    base = names[0]
    changes = {
        spec.name: verdict_changes(tables[base], tables[spec.name])
        for spec in specs[1:]
    }
    if args.format == "json":
        payload = {
            "configs": [
                {"name": s.name, "flags": list(s.flags)} for s in specs
            ],
            "summaries": summaries,
            "changes": {
                name: [change.__dict__ for change in items]
                for name, items in changes.items()
            },
        }
        _emit(_json_pretty(payload), args.output)
        return 0
    _emit(render_matrix(specs, summaries, changes), args.output)
    return 0


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
        lines += [
            "## 成本上升的头文件",
            "",
            "| 头文件 | 之前 | 之后 | 变化 |",
            "|---|---:|---:|---:|",
        ]
        for delta in comparison.regressed_headers[:15]:
            lines.append(
                f"| `{delta.path}` | {delta.before:,} | "
                f"{delta.after:,} | {delta.percent:+.1f}% |"
            )
        lines.append("")
    if comparison.improved_headers:
        lines += [
            "## 成本下降的头文件",
            "",
            "| 头文件 | 之前 | 之后 | 变化 |",
            "|---|---:|---:|---:|",
        ]
        for delta in comparison.improved_headers[:15]:
            lines.append(
                f"| `{delta.path}` | {delta.before:,} | "
                f"{delta.after:,} | {delta.percent:+.1f}% |"
            )
        lines.append("")
    print("\n".join(lines))
    return 0


def _cmd_blame(args) -> int:
    try:
        report = blame_baselines(
            args.before,
            args.after,
            repo=args.repo,
            from_rev=args.from_rev,
            to_rev=args.to_rev,
        )
    except (BaselineError, BlameError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        payload = {
            "attributed_lines_before": report.total_before,
            "attributed_lines_after": report.total_after,
            "net_delta": report.net_delta,
            "from_rev": report.from_rev,
            "to_rev": report.to_rev,
            "explained_delta": report.explained_delta,
            "unexplained": report.unexplained,
            "entries": [
                {
                    "commit": e.commit.sha,
                    "author": e.commit.author,
                    "date": e.commit.date,
                    "subject": e.commit.subject,
                    "file": str(e.file),
                    "change": e.change,
                    "target": e.target,
                    "predicted_delta": e.predicted_delta,
                    "observed_delta": e.observed_delta,
                    "confidence": e.confidence,
                    "note": e.note,
                }
                for e in report.entries
            ],
        }
        print(_json_pretty(payload))
        return 0

    print(render_blame(report))
    if report.unexplained:
        print(f"注意：{report.unexplained:+,} 行未能归因到具体改动。", file=sys.stderr)
    return 0


def _with_cli_overrides(task, args):
    """把命令行给的 std / -I / -D / --flag 叠加到某个编译单元的参数上。

    compile_commands 里的单元自带完整参数，命令行只作为**补充**（例如补一个
    构建系统没导出的包含路径）。不覆盖原值，避免把能编译的命令行改坏。
    """
    options = dataclasses.replace(
        task.options,
        standard=args.std or task.options.standard,
        include_dirs=[*task.options.include_dirs, *args.includes],
        defines=[*task.options.defines, *args.defines],
        extra_flags=[*task.options.extra_flags, *args.flags],
    )
    return dataclasses.replace(task, options=options)


def _cmd_edge_cost(args) -> int:
    root = args.path.resolve()
    if not root.exists():
        print(f"错误：路径不存在 {root}", file=sys.stderr)
        return 2
    try:
        driver = detect_driver(args.compiler)
    except CompilationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    tasks = discover_translation_units(
        root,
        compile_commands=args.compile_commands,
        max_tus=args.max_tus,
    )
    if args.std or args.includes or args.defines or args.flags:
        tasks = [_with_cli_overrides(task, args) for task in tasks]

    try:
        units = collect_edge_units(
            tasks,
            driver,
            jobs=max(1, args.jobs),
            timeout=600.0,
            effective_only=args.effective_only,
        )
    except EdgeCostError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    costs = aggregate_edge_costs(u.edges for u in units if u.ok)
    text = (
        _json_pretty(edges_to_dict(costs, units))
        if args.format == "json"
        else render_edges_markdown(costs, units, top=args.top)
    )
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        failed = sum(1 for u in units if not u.ok)
        print(
            f"已写入 {args.output}（{len(costs)} 条边，"
            f"{len(units) - failed}/{len(units)} 个编译单元成功）",
            file=sys.stderr,
        )
    else:
        print(text)
    failed = [u for u in units if not u.ok]
    if failed:
        print(f"注意：{len(failed)} 个编译单元采集失败，已在报告中列出。", file=sys.stderr)
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
    "verified": _cmd_verified,
    "gate": _cmd_gate,
    "configs": _cmd_configs,
    "compare": _cmd_compare,
    "blame": _cmd_blame,
    "edge-cost": _cmd_edge_cost,
    "history": _cmd_history,
    "pr-comment": _cmd_pr_comment,
    "toolchains": _cmd_toolchains,
    "pch-verify": _cmd_pch_verify,
    "forward-verify": _cmd_forward_verify,
}


def _make_redirected_output_utf8() -> None:
    """把被重定向的 stdout/stderr 切成 UTF-8。

    为什么必须做：Windows 在**管道**下不用宽字符 API，而是按 ANSI 代码页
    （GitHub runner 上是 cp1252）编码。我们的输出是中文，于是
    ``print("头文件解析量：…")`` 直接抛 ``UnicodeEncodeError``——CI 上表现为
    exit 1 加一段 traceback，看起来像「门禁判定失败」，实际是**打印失败**。
    任何人把输出接进 tee、日志文件或 CI 都会撞上，不只我们自己的流水线。

    交互式控制台不动：那边由 WriteConsoleW 处理 Unicode，改成 UTF-8 反而可能
    在 cp936 终端上显示乱码。只有「不是终端」时才切成 UTF-8——CI 日志与
    重定向文件本来就按 UTF-8 解释。

    ``errors="replace"`` 是最后一道保险：任何环境下都不允许因为打印而崩溃。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None or stream.isatty():
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):  # pragma: no cover - 平台差异
            continue


def main(argv: list[str] | None = None) -> int:
    _make_redirected_output_utf8()
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
