"""Markdown report rendering.

Markdown is the primary report format because it is the one a reviewer can
read inside a pull request, paste into an issue, or diff against last week's
version.  The HTML report is the pretty one; this one is the useful one.
"""

from __future__ import annotations

from pathlib import Path

from .. import PROJECT_URL
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

    # -- template instantiation cost -------------------------------------
    profile = getattr(scan, "template_profile", None)
    if profile is not None and profile.is_significant:
        lines.append("## 模板实例化成本")
        lines.append("")
        lines.append(
            f"全项目模板实例化合计 **{_fmt_seconds(profile.total_seconds)}**，"
            f"占总构建时间 **{profile.overall_percent:.1f}%**。"
        )
        lines.append("")
        lines.append(
            "> 每个编译单元的数字来自 `-ftime-report`，是精确的；"
            "下面「模板表面」一列是**文本启发式**（`template<` 声明数 × 引用它的 TU 数），"
            "用于提示成本可能来自哪里，不是实测归因。"
        )
        lines.append("")
        if profile.heavy_tus:
            lines.append("**模板实例化占比超过 25% 的编译单元：**")
            lines.append("")
            for source, seconds, percent in profile.per_tu:
                if percent >= 25.0:
                    lines.append(
                        f"- `{_rel(source, root)}`：{percent:.1f}%"
                        f"（{_fmt_seconds(seconds)}）"
                    )
            lines.append("")
        # 只列项目自己的头文件。系统头（<type_traits> 之类）模板声明数最多，
        # 但用户改不了它们，把它们排在前面只会淹没真正可操作的线索。
        surface = [
            (h, d, r)
            for h, d, r in profile.by_surface
            if d * r > 0
            and scan.header_stats.get(h) is not None
            and scan.header_stats[h].kind is HeaderKind.PROJECT
        ][:10]
        if surface:
            lines.append("| 头文件 | template< 声明数 | 被引用 TU | 模板表面 |")
            lines.append("|---|---:|---:|---:|")
            for header, declarations, reach in surface:
                lines.append(
                    f"| `{_rel(header, root)}` | {declarations} "
                    f"| {reach} | {declarations * reach} |"
                )
            lines.append("")
            lines.append(
                "模板表面大、又被大量 TU 引用的头文件，是模板实例化成本的主要嫌疑对象。"
                "这是**调查线索，不是结论**——编译器只报告阶段总量，不做按头文件的归因。"
                "系统头文件（如 `<type_traits>`）的模板声明数通常更高，但用户改不了，故未列入。"
            )
            lines.append("")

    # -- precompiled header ----------------------------------------------
    pch = getattr(scan, "pch", None)
    if pch is not None and pch.headers:
        lines.append("## 预编译头（PCH）收益估算")
        lines.append("")
        lines.append(
            f"建议将下列 {len(pch.headers)} 个头文件纳入 PCH。"
            f"它们的传递闭包共 **{_fmt_int(pch.closure_lines)} 行**，"
            f"其中 **{pch.eligible_tus}/{pch.total_tus}** 个编译单元已全部包含它们"
            f"（覆盖率 {pch.coverage:.0f}%）。"
        )
        lines.append("")
        lines.append("| 头文件 | 行数 | 被引用 TU |")
        lines.append("|---|---:|---:|")
        for header in pch.headers:
            stats = scan.header_stats.get(header)
            if stats:
                lines.append(
                    f"| `{_rel(header, root)}` | {_fmt_int(stats.line_count)} | {stats.tu_reach} |"
                )
        lines.append("")
        verdict = "值得做" if pch.is_worthwhile else "收益有限，谨慎采纳"
        lines.append(
            f"**估算净收益：{_fmt_int(pch.net_lines_saved)} 行"
            f"（占全项目头文件解析量 {pch.percent_of_project:.1f}%）——{verdict}。**"
        )
        lines.append("")
        lines.append("> 这是估算，不是承诺。四条限制：")
        for caveat in pch.caveats:
            lines.append(f"> - {caveat}")
        lines.append("")

    # -- forward declarations --------------------------------------------
    forwards = getattr(scan, "forward_candidates", None) or []
    if forwards:
        lines.append("## 可改为前向声明的 include")
        lines.append("")
        lines.append(
            "把 include 换成前向声明，是编译时间优化里收益最高的一类改动："
            "编译器读一行文本，而不是整个头文件的传递闭包。"
        )
        lines.append("")
        lines.append(
            "> **这是启发式，必须人工确认。**「只以指针/引用形式使用」是语义属性，"
            "我们用正则判断，可能被宏、typedef、模板参数欺骗。下表给出具体类型名、"
            "使用行号和阻断类型，供你在数秒内确认或否决——说不出理由的建议不可操作。"
        )
        lines.append("")
        lines.append("| 编译单元 | include | 可前向声明的类型 | 使用行号 | 阻断类型 |")
        lines.append("|---|---|---|---|---|")
        for candidate in forwards[:15]:
            blocking = (
                "、".join(f"`{t}`" for t in candidate.blocking_types) or "—"
            )
            lines.append(
                f"| `{_rel(candidate.tu, root)}` "
                f"| `{_rel(candidate.header, root)}` "
                f"| {'、'.join(f'`{t}`' for t in candidate.forwardable_types)} "
                f"| {', '.join(str(n) for n in candidate.evidence_lines[:6])} "
                f"| {blocking} |"
            )
        lines.append("")
        lines.append(
            "「阻断类型」非空表示该头文件里还有其他类型需要完整定义，"
            "**不能整个 include 换掉**，只能把可前向声明的类型拆出去。"
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
        f"由 [HeavyHeader]({PROJECT_URL}) 生成 · "
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
