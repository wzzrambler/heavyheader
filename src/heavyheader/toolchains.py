"""多编译器对比：同一份代码在多个编译器下的构建成本差异。

``scan`` 一次只回答「在一个工具链下，成本花在哪些头文件上」。工具链迁移要回答
的是另一个问题：**换一个编译器，是更快还是更慢，差在哪里**。两者共享同一套
扫描与归因，但输出结构不同——``compare`` 比较的是同一工具链的时间前后，这里
比较的是不同工具链之间的横向矩阵，所以单独成模块，而不是硬塞进 ``compare``。

三条口径上的坚持，避免把矩阵读成它没有的意思：

1. **耗时只作参考，不是结论。** 它取决于机器、负载、是否 ``-fsyntax-only``；
   矩阵里的耗时是「本机这次」的实测，用来提示「换编译器大概是什么量级」，
   不构成任何绝对承诺。
2. **结构指标（归因行数、依赖边、TU 数）才是确定性的。** 它们由编译器真实
   报告的 include 图算出，同机多次一致。
3. **跨编译器的逐头文件对比只比项目自己的头。** 系统头的路径在 GCC / Clang /
   MSVC 下各不相同（``/usr/include/c++/13/vector`` vs MSVC 的安装路径），按路径
   拼接毫无意义，混进「同名头差异」只会制造假差异。项目头是同一份源码，比它
   才有意义；系统 / 第三方头的总量差异体现在「非项目头」那一列。

第二个编译器没装是常态。矩阵**不会因此整体失败**：装没装是我们探测得出来的
事实，如实写成一行「失败：找不到编译器」，其余编译器照常对比。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .drivers.base import CompilationError, CompilerDriver, detect_driver
from .models import HeaderKind, ProjectScan
from .scanner import ScanError, ScanOptions, scan_project

__all__ = [
    "ToolchainCost",
    "HeaderSpread",
    "ToolchainComparison",
    "build_costs",
    "compare_toolchains",
    "scan_toolchains",
    "render_toolchains",
    "to_dict",
]

#: 低于这个相对差就当作噪声，不判定「谁更快」。
#: 与 ``verify`` 的 2% 噪声地板同一口径：单次编译耗时在这个量级上的差异
#: 主要来自调度与缓存，不足以支撑一个结论。报「快 1%」比不报更误导。
_TIME_NOISE_PERCENT = 2.0


@dataclass(slots=True)
class ToolchainCost:
    """一个编译器在这份代码上的成本快照。"""

    compiler: str
    family: str = "unknown"
    #: 是否至少有编译单元成功编译。0 个成功单元视为该编译器不可用/不可比。
    ok: bool = False
    #: 该编译器整体不可用（找不到、扫描报错）时的说明。
    error: str | None = None
    tu_count: int = 0
    failed_tu_count: int = 0
    #: 归因行数（全部头文件，含系统/第三方头）——与 gate 的主指标同一口径。
    attributed_lines: int = 0
    #: 其中来自项目自己头文件的部分——用户可操作的那部分。
    project_attributed_lines: int = 0
    dependency_edges: int = 0
    #: 该编译器下所有成功单元 TOTAL 行之和。0 表示编译器没报告 TOTAL。
    seconds: float = 0.0
    candidate_count: int = 0
    unguarded_count: int = 0
    #: 保留原始扫描，供逐头文件对比；失败时为 None。
    scan: ProjectScan | None = None

    @property
    def non_project_attributed_lines(self) -> int:
        return max(0, self.attributed_lines - self.project_attributed_lines)

    @property
    def time_discarded(self) -> bool:
        """没有可信耗时（编译器没报 TOTAL），不该参与「谁更快」的比较。"""
        return self.seconds <= 0.0


@dataclass(slots=True)
class HeaderSpread:
    """一个项目头在不同编译器下的归因行数，以及它们的分歧。"""

    path: Path
    #: compiler -> attributed_lines；缺席的编译器记 0。
    lines: dict[str, int] = field(default_factory=dict)

    @property
    def spread(self) -> int:
        values = list(self.lines.values())
        return max(values) - min(values) if values else 0

    @property
    def spread_percent(self) -> float:
        """差值占**较大值**的百分比。

        不用「占最小值」：某个编译器压根没包含这个头时最小值为 0，百分比会
        变成无穷大或未定义。以较大值为分母始终落在 0–100%，含义也直白——
        「分歧相当于最大值的一成还是全部」。
        """
        values = list(self.lines.values())
        if not values:
            return 0.0
        high = max(values)
        return self.spread / high * 100.0 if high else 0.0


@dataclass(slots=True)
class ToolchainComparison:
    """多编译器对比结果。"""

    root: Path
    costs: list[ToolchainCost] = field(default_factory=list)
    header_spreads: list[HeaderSpread] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def compilers(self) -> list[str]:
        return [cost.compiler for cost in self.costs]

    @property
    def ok_costs(self) -> list[ToolchainCost]:
        return [cost for cost in self.costs if cost.ok]

    @property
    def comparable(self) -> bool:
        """至少两个编译器成功，才谈得上「对比」。"""
        return len(self.ok_costs) >= 2

    def fastest(self) -> ToolchainCost | None:
        """可信耗时里最快的那个。没有可信耗时则返回 None（不猜）。"""
        ranked = [c for c in self.ok_costs if not c.time_discarded]
        if not ranked:
            return None
        return min(ranked, key=lambda c: c.seconds)


# --------------------------------------------------------------------------
# building the matrix
# --------------------------------------------------------------------------


@dataclass(slots=True)
class _Outcome:
    """一次编译尝试的原始结果：成功就是 scan，失败就是 error。"""

    compiler: str
    family: str
    scan: ProjectScan | None
    error: str | None


def _unguarded(scan: ProjectScan) -> int:
    """缺 guard 的头数：被处理的次数多于到达它的 TU 数。

    与 ``baseline._unguarded_count`` 同一判定，这里内联一份以免把跨模块的
    私有函数当成公共 API 依赖。
    """
    return sum(
        1
        for stats in scan.header_stats.values()
        if stats.inclusion_events > stats.tu_reach
    )


def build_costs(outcomes: Sequence[_Outcome]) -> list[ToolchainCost]:
    """把每次编译尝试折叠成一行成本。

    ``ok`` 的判据是「至少有一个编译单元真的编译成功」——一个单元都没成功，
    说明这个编译器根本没跑起来（没装、路径错、全量报错），此时把它当成
    「成本为 0、快得离谱」会彻底误导读者，必须显式标成失败。
    """
    costs: list[ToolchainCost] = []
    for outcome in outcomes:
        scan = outcome.scan
        if scan is None:
            costs.append(
                ToolchainCost(
                    compiler=outcome.compiler,
                    family=outcome.family,
                    ok=False,
                    error=outcome.error or "未知错误",
                )
            )
            continue
        ok_tus = scan.ok_tus
        if not ok_tus:
            first_error = next(
                (tu.error for tu in scan.failed_tus if tu.error), None
            )
            costs.append(
                ToolchainCost(
                    compiler=outcome.compiler,
                    family=outcome.family,
                    ok=False,
                    failed_tu_count=len(scan.tus),
                    error=outcome.error or first_error or "没有任何编译单元编译成功",
                    scan=None,
                )
            )
            continue
        project_lines = sum(
            s.attributed_lines
            for s in scan.header_stats.values()
            if s.kind is HeaderKind.PROJECT
        )
        costs.append(
            ToolchainCost(
                compiler=outcome.compiler,
                family=outcome.family,
                ok=True,
                error=outcome.error,
                tu_count=len(ok_tus),
                failed_tu_count=len(scan.failed_tus),
                attributed_lines=scan.total_included_lines(),
                project_attributed_lines=project_lines,
                dependency_edges=scan.total_dependency_edges(),
                seconds=scan.baseline_seconds,
                candidate_count=len(scan.candidates),
                unguarded_count=_unguarded(scan),
                scan=scan,
            )
        )
    return costs


def _canon_header(path: Path, root: Path) -> Path:
    """把一个头文件路径归一到它真实所在的文件。

    同一个文件，GCC/Clang 报的是相对（正斜杠）路径，MSVC 报的是绝对（反斜杠）
    路径。直接按字符串当 key 会把同一个头算成两个，跨编译器逐头对比就错位了。
    这里统一成 realpath，让不同工具链对同一文件的报告落到同一个 key 上。
    """
    normalised = Path(os.path.normpath(str(path).replace("\\", "/")))
    try:
        return normalised.resolve()
    except OSError:
        return normalised


def _header_spreads(costs: Sequence[ToolchainCost], root: Path) -> list[HeaderSpread]:
    """项目头在各编译器间的归因行数分歧，按差值从大到小。

    只统计出现在**至少两个成功编译器**里、且数值确有分歧的项目头：数值一致
    的头没有「差异」可言，列出来只会淹没真正的分歧。

    不同编译器给出的路径形式不同（见 ``_canon_header``），先归一到真实文件
    再对齐，否则同名头会被当成两个、差异表出现重复行。
    """
    ok = [c for c in costs if c.ok and c.scan is not None]
    if len(ok) < 2:
        return []

    # 每个编译器的 raw path -> 归一后的真实文件（per-cost 用列表存，因为
    # ToolchainCost 是不可哈希的 slots dataclass，不能当 dict key）。
    per_cost: list[tuple[ToolchainCost, dict[Path, Path], dict[Path, Path]]] = []
    canon_paths: set[Path] = set()
    for cost in ok:
        mapping = {p: _canon_header(p, root) for p in cost.scan.header_stats}
        raw_for = {canon: raw for raw, canon in mapping.items()}
        per_cost.append((cost, mapping, raw_for))
        canon_paths.update(mapping.values())

    spreads: list[HeaderSpread] = []
    for canon in canon_paths:
        lines: dict[str, int] = {}
        for cost, _mapping, raw_for in per_cost:
            raw = raw_for.get(canon)
            stats = cost.scan.header_stats.get(raw) if raw else None
            lines[cost.compiler] = (
                stats.attributed_lines
                if stats and stats.kind is HeaderKind.PROJECT
                else 0
            )
        if all(v == 0 for v in lines.values()):
            continue
        spread = HeaderSpread(path=canon, lines=lines)
        if spread.spread == 0:
            continue
        spreads.append(spread)

    # 显示路径化归到相对 root，方便阅读（不同编译器报的绝对/相对都统一）。
    root_resolved = Path(root).resolve()
    for spread in spreads:
        try:
            spread.path = spread.path.relative_to(root_resolved)
        except ValueError:
            pass
    return sorted(spreads, key=lambda s: s.spread, reverse=True)


def compare_toolchains(
    root: Path, costs: Sequence[ToolchainCost]
) -> ToolchainComparison:
    """把各编译器成本折叠成对比矩阵（纯函数，不跑编译器）。"""
    costs = list(costs)
    comparison = ToolchainComparison(
        root=root, costs=costs, header_spreads=_header_spreads(costs, root)
    )

    failed = [c for c in costs if not c.ok]
    for cost in failed:
        comparison.warnings.append(
            f"{cost.compiler} 未能产出可用结果：{cost.error}"
        )
    if not comparison.comparable:
        comparison.warnings.append(
            f"只有 {len(comparison.ok_costs)} 个编译器成功编译，无法做横向对比——"
            "至少需要两个。请确认 --compilers 里的其余编译器已安装并在 PATH 上"
        )
    return comparison


def scan_toolchains(
    root: Path,
    compilers: Sequence[str],
    *,
    options_factory: Callable[[str], ScanOptions],
    driver_factory: Callable[[str], CompilerDriver] = detect_driver,
) -> ToolchainComparison:
    """依次用每个编译器扫描同一份代码，再折叠成对比矩阵。

    **串行**而不是并行：并行跑两个扫描会让它们互相争抢 CPU，把墙钟时间抬高，
    而「谁更快」恰恰是这个功能要回答的问题。串行慢一点，但数字可信。
    """
    outcomes: list[_Outcome] = []
    for compiler in compilers:
        driver: CompilerDriver | None = None
        family = "unknown"
        try:
            driver = driver_factory(compiler)
            family = getattr(driver.family, "value", str(driver.family))
            scan = scan_project(options_factory(compiler), driver=driver)
            outcomes.append(_Outcome(compiler, family, scan, None))
        except (ScanError, CompilationError, OSError) as exc:
            outcomes.append(_Outcome(compiler, family, None, str(exc)))
    return compare_toolchains(root, build_costs(outcomes))


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _fmt_int(value: int) -> str:
    return f"{value:,}" if value else "—"


def _relative_time(cost: ToolchainCost, fastest: ToolchainCost | None) -> str:
    if cost.time_discarded or fastest is None or fastest.seconds <= 0:
        return "—"
    if cost is fastest:
        # 「基准」而不是「最快」：最快只是本次计时的参照点，差在噪声内时
        # 并没有谁真的更快。是否下结论交给渲染末尾的噪声判定。
        return "基准"
    delta = (cost.seconds - fastest.seconds) / fastest.seconds * 100.0
    return f"+{delta:.1f}%"


def _short_error(error: str | None, limit: int = 70) -> str:
    text = (error or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_toolchains(comparison: ToolchainComparison, *, top: int = 15) -> str:
    """把矩阵渲染成 Markdown。"""
    lines: list[str] = [
        f"# 多编译器构建成本对比 · {comparison.root.name or comparison.root}",
        "",
        f"编译器：{', '.join(comparison.compilers)}",
        "",
        "| 编译器 | TU | 归因行数 | 项目头 | 非项目头 | 依赖边 | 耗时(s) | 相对基准 | 备注 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    fastest = comparison.fastest()
    for cost in comparison.costs:
        if not cost.ok:
            lines.append(
                f"| `{cost.compiler}` | — | — | — | — | — | — | — | "
                f"失败：{_short_error(cost.error)} |"
            )
            continue
        note = ""
        if cost.failed_tu_count:
            note = f"{cost.failed_tu_count} 个 TU 编译失败"
        if cost.time_discarded:
            note = (note + "；" if note else "") + "无 TOTAL 计时"
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                cost.compiler,
                cost.tu_count,
                _fmt_int(cost.attributed_lines),
                _fmt_int(cost.project_attributed_lines),
                _fmt_int(cost.non_project_attributed_lines),
                _fmt_int(cost.dependency_edges),
                f"{cost.seconds:.2f}" if not cost.time_discarded else "—",
                _relative_time(cost, fastest),
                note or "—",
            )
        )

    if fastest is not None and comparison.comparable:
        timed = [c for c in comparison.ok_costs if not c.time_discarded]
        slowest = max(timed, key=lambda c: c.seconds, default=fastest)
        if slowest is not fastest and slowest.seconds > 0:
            gain = (slowest.seconds - fastest.seconds) / slowest.seconds * 100.0
            if gain < _TIME_NOISE_PERCENT:
                # 单次计时的 1% 差异基本是调度噪声，据此宣布「谁更快」是不诚实的。
                lines += [
                    "",
                    f"各编译器耗时差异在噪声范围内（<{_TIME_NOISE_PERCENT:.0f}%），"
                    "不判定快慢；结构指标一致说明解析量本身没有差别。",
                ]
            else:
                lines += [
                    "",
                    f"本机实测：`{fastest.compiler}` 最快，比 `{slowest.compiler}` "
                    f"少花 {gain:.1f}% 的墙钟时间。",
                ]

    if comparison.header_spreads:
        headers = comparison.compilers
        header_cells = " | ".join(f"`{h}`" for h in headers)
        # 头文件（左对齐）+ 每个编译器（右对齐）+ 差值 + 差异
        divider = "|---|" + "---:|" * len(headers) + "---:|---:|"
        lines += [
            "",
            f"## 项目头文件的跨编译器差异（前 {min(top, len(comparison.header_spreads))}）",
            "",
            f"| 头文件 | {header_cells} | 差值 | 差异(占较大值) |",
            divider,
        ]
        for spread in comparison.header_spreads[: max(0, top)]:
            values = " | ".join(
                _fmt_int(spread.lines.get(h, 0)) for h in headers
            )
            lines.append(
                f"| `{spread.path.as_posix()}` | {values} | "
                f"{spread.spread:,} | {spread.spread_percent:.1f}% |"
            )
    elif comparison.comparable:
        lines += [
            "",
            "## 项目头文件的跨编译器差异",
            "",
            "所有项目头在各编译器下的归因行数一致——说明结构解析与工具链无关，"
            "差异只体现在耗时与非项目头（系统/第三方）的解析量上。",
        ]

    if comparison.warnings:
        lines += ["", "## 提示", ""]
        for warning in comparison.warnings:
            lines.append(f"- {warning}")

    if not comparison.comparable:
        lines += [
            "",
            "## 说明",
            "",
            "少于两个编译器成功，本次不构成对比。",
        ]

    lines += [
        "",
        "> **口径**：结构指标（TU / 归因行数 / 依赖边）由编译器真实报告的 "
        "include 图算出，是确定性的；**耗时只作参考**，它随机器与负载变化，"
        "且此处为 `-fsyntax-only` 的实测。逐头文件对比只统计项目自己的头"
        "（系统头路径在不同工具链下不同，按路径比较没有意义）。",
        "",
    ]
    return "\n".join(lines)


def to_dict(comparison: ToolchainComparison) -> dict:
    """矩阵的 JSON 形态，供 CI 或下游脚本消费。"""
    fastest = comparison.fastest()
    return {
        "root": str(comparison.root),
        "compilers": comparison.compilers,
        "comparable": comparison.comparable,
        "fastest": fastest.compiler if fastest else None,
        "rows": [
            {
                "compiler": cost.compiler,
                "family": cost.family,
                "ok": cost.ok,
                "error": cost.error,
                "tu_count": cost.tu_count,
                "failed_tu_count": cost.failed_tu_count,
                "attributed_lines": cost.attributed_lines,
                "project_attributed_lines": cost.project_attributed_lines,
                "non_project_attributed_lines": cost.non_project_attributed_lines,
                "dependency_edges": cost.dependency_edges,
                "seconds": round(cost.seconds, 4),
                "candidate_count": cost.candidate_count,
                "unguarded_count": cost.unguarded_count,
            }
            for cost in comparison.costs
        ],
        "header_spreads": [
            {
                "path": spread.path.as_posix(),
                "lines": spread.lines,
                "spread": spread.spread,
                "spread_percent": round(spread.spread_percent, 2),
            }
            for spread in comparison.header_spreads
        ],
        "warnings": list(comparison.warnings),
    }
