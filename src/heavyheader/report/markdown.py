"""Markdown report rendering.

Markdown is the primary report format because it is the one a reviewer can
read inside a pull request, paste into an issue, or diff against last week's
version.  The HTML report is the pretty one; this one is the useful one.
"""

from __future__ import annotations

from pathlib import Path

from ..models import HeaderKind, ProjectScan

__all__ = ["render_markdown", "markdown_summary_line"]


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_seconds(value: float) -> str:
    if value >= 60:
        return f"{value / 60:.1f} min"
    if value >= 1:
        return f"{value:.2f} s"
    return f"{value * 1000:.0f} ms"


def _rel(path: Path, root: Path) -> str:
    """Project-relative path, or a disambiguating tail for foreign headers.

    Falling back to ``path.name`` for system headers produces rows like
    ``stddef.h`` appearing three times with different values, which reads
    like a bug in the tool.  Keeping the last two components makes each row
    unique while staying short enough for a table.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        parts = path.parts
        return str(Path(*parts[-2:])) if len(parts) > 1 else path.name


def render_markdown(scan: ProjectScan) -> str:
    """Render a full scan as Markdown."""
    root = scan.root
    ok = scan.ok_tus
    lines: list[str] = []

    lines.append("# HeavyHeader 构建成本报告")
    lines.append("")
    lines.append(f"- 项目根目录：`{root}`")
    lines.append(f"- 编译器：`{scan.compiler}`（{scan.compiler_family.value}）")
    if scan.standard:
        lines.append(f"- 语言标准：`{scan.standard}`")
    lines.append(f"- 编译单元：{len(ok)} 成功 / {len(scan.failed_tus)} 失败")
    lines.append(f"- 头文件依赖边：{_fmt_int(scan.total_dependency_edges())}")
    lines.append(f"- 全项目头文件解析量：**{_fmt_int(scan.total_included_lines())} 行**")
    lines.append(f"- 累计编译耗时：{_fmt_seconds(scan.baseline_seconds)}")
    lines.append("")

    # -- headline: where the cost is -------------------------------------
    # Filter to project headers *before* taking the top N.  Doing it the
    # other way round lets a handful of enormous system headers push every
    # actionable row off the page.
    project_headers = [
        stats for stats in scan.headers_by_cost() if stats.kind is HeaderKind.PROJECT
    ]
    lines.append("## 成本热点 Top 15")
    lines.append("")
    lines.append("按「归因行数」排序，即 `头文件行数 × 引用它的编译单元数`——")
    lines.append("它衡量的是这个头文件让整个项目多解析了多少行代码。")
    lines.append("")
    lines.append("| 头文件 | 行数 | 被引用 TU | 归因行数 | 传递闭包 | 最大深度 |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for stats in project_headers[:15]:
        lines.append(
            f"| `{_rel(stats.path, root)}` | {_fmt_int(stats.line_count)} "
            f"| {stats.tu_reach} | {_fmt_int(stats.attributed_lines)} "
            f"| {stats.transitive_headers} | {stats.max_depth} |"
        )
    lines.append("")

    # -- actionable: removal candidates ----------------------------------
    if scan.candidates:
        lines.append("## 成本候选（按预计收益排序）")
        lines.append("")
        lines.append(
            "> **这张表说的是「贵」，不是「能删」。** 「独占闭包」指删掉这条 include 后，"
            "只有它会停止被解析的头文件集合——如果某个头文件还能从别的 include 到达，"
            "删掉当前这条并不省钱。这是本表与普通 lint 工具的区别。"
        )
        lines.append(">")
        lines.append(
            "> 「使用状态」是文本启发式：源码中若未出现该头文件声明的任何标识符，"
            "标记为「疑似未使用」。它**不是**安全性保证——为副作用而包含的头文件"
            "（如 `<iostream>` 的流初始化）会被误判。权威结论来自 "
            "`heavyheader verify`：真的删掉、重新编译、实测耗时。"
        )
        lines.append("")
        lines.append("| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 置信度 |")
        lines.append("|---|---|---:|---:|---:|---|---|")
        for candidate in scan.candidates_by_estimate()[:20]:
            lines.append(
                f"| `{_rel(candidate.tu, root)}` "
                f"| `{_rel(candidate.header, root)}` "
                f"| {len(candidate.exclusive_headers)} "
                f"| {_fmt_int(candidate.estimated_lines_saved)} "
                f"| {candidate.estimated_percent:.1f}% "
                f"| {candidate.usage_label} "
                f"| {candidate.confidence} |"
            )
        lines.append("")

    # -- guaranteed wins: missing guards ---------------------------------
    redundant = [
        (h, s)
        for h, s in scan.header_stats.items()
        if s.inclusion_events > s.tu_reach
    ]
    if redundant:
        redundant.sort(key=lambda item: item[1].inclusion_events - item[1].tu_reach, reverse=True)
        lines.append("## 缺少 include guard 的头文件（必赚项）")
        lines.append("")
        lines.append(
            "这些头文件在**同一个编译单元内被重复解析**。对带 guard 的头文件来说"
            "这在物理上不可能发生，所以这是「缺少 guard」的确定性信号。"
            "补上 `#pragma once` 或 guard 宏永远安全，且收益立竿见影。"
        )
        lines.append("")
        lines.append("| 头文件 | 解析次数 | 涉及 TU | 多解析次数 |")
        lines.append("|---|---:|---:|---:|")
        for header, stats in redundant[:15]:
            lines.append(
                f"| `{_rel(header, root)}` | {stats.inclusion_events} "
                f"| {stats.tu_reach} | {stats.inclusion_events - stats.tu_reach} |"
            )
        lines.append("")

    # -- failures ---------------------------------------------------------
    if scan.failed_tus:
        lines.append("## 编译失败的单元")
        lines.append("")
        for tu in scan.failed_tus:
            lines.append(f"- `{_rel(tu.source, root)}`：{tu.error}")
        lines.append("")

    if scan.warnings:
        lines.append("## 扫描警告")
        lines.append("")
        for warning in scan.warnings[:20]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "由 [HeavyHeader](https://github.com/) 生成 · "
        "指标定义见 `docs/methodology.md`"
    )
    lines.append("")
    return "\n".join(lines)


def markdown_summary_line(scan: ProjectScan) -> str:
    """One-line summary, used by the CLI and by ``gate`` output."""
    unused = sum(1 for c in scan.candidates if c.usage_label == "疑似未使用")
    return (
        f"{len(scan.ok_tus)} TU · "
        f"{_fmt_int(scan.total_dependency_edges())} 条依赖边 · "
        f"{_fmt_int(scan.total_included_lines())} 行头文件解析量 · "
        f"{len(scan.candidates)} 个成本候选（其中 {unused} 个疑似未使用）"
    )
