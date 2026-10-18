"""Markdown report rendering.

Markdown is the primary report format because it is the one a reviewer can
read inside a pull request, paste into an issue, or diff against last week's
version.  The HTML report is the pretty one; this one is the useful one.
"""

from __future__ import annotations

from pathlib import Path

from .. import PROJECT_URL
from ..attrib.advice import (
    MAINTENANCE_LEAN_LINES,
    MAINTENANCE_LEAN_SHARE,
    TIME_NOISE_FLOOR,
)
from ..attrib.diagnosis import BLOCK_REASONS
from ..models import AdviceVerdict, HeaderKind, ProjectScan

__all__ = ["render_markdown", "markdown_summary_line"]


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_seconds(value: float) -> str:
    if value >= 60:
        return f"{value / 60:.1f} min"
    if value >= 1:
        return f"{value:.2f} s"
    return f"{value * 1000:.0f} ms"


def _verdict_cell(candidate) -> str:
    """The one-word answer for the candidate table.

    An unprobed candidate shows "未实测", never a guess.  The whole point of
    this column is that it is never softer than the evidence behind it.
    """
    advice = candidate.advice
    if advice is None:
        return "未实测"
    label = advice.verdict_label
    if advice.verdict is AdviceVerdict.HUMAN and advice.lean:
        lean = {"remove": "偏删", "keep": "偏留"}.get(advice.lean, "")
        if lean:
            return f"{label}（{lean}）"
    return label


def _reason_line(reason) -> str:
    if reason.evidence:
        return f"  - **{reason.aspect.value}**：{reason.text}（{reason.evidence}）"
    return f"  - **{reason.aspect.value}**：{reason.text}"


_ADVICE_ORDER = {
    AdviceVerdict.REMOVE: 0,
    AdviceVerdict.HUMAN: 1,
    AdviceVerdict.KEEP: 2,
}

_KIND_LABEL = {
    HeaderKind.PROJECT: "项目",
    HeaderKind.SYSTEM: "系统/第三方",
    HeaderKind.EXTERNAL: "外部",
}


def _diagnosis_section(scan: ProjectScan, root: Path) -> list[str]:
    """「没有可删的了」时的那一节：成本在哪、每条为什么被拦下、下一步看什么。

    三个工程里有两个（nlohmann/json、zlib）产出 0 条可行候选。只渲染一张空
    的候选表会让人以为「构建没问题」，而真实情况是每条都被判了「删了编不
    过」——所以这一节把**被拦下的原因**当作结论来报。
    """
    diagnosis = getattr(scan, "site_diagnosis", None)
    if diagnosis is None:
        return []
    lines: list[str] = []
    empty = diagnosis.is_empty_handed
    lines.append(
        "## "
        + (
            "没有可删的了：成本在哪、为什么删不掉"
            if empty
            else "成本去向：其余 include 行为什么没进候选表"
        )
    )
    lines.append("")
    if empty:
        lines.append(
            "> 这一轮**没有产出任何删除建议**。空表不等于构建没问题：下面先列出"
            "成本集中在哪，再列出收益最高的那几条分别因为什么被拦下。"
            "工具不会因为给不出建议而降低可行性门槛——宁可交白卷。"
        )
        lines.append("")
    labels = _unique_tails(
        [row.path for row in diagnosis.concentration]
        + [item.site_file for item in diagnosis.blocked_top]
        + [item.header for item in diagnosis.blocked_top],
        root,
    )
    considered = sum(diagnosis.counts.values())
    lines.append(
        f"可行性闸门：放行 **{diagnosis.candidate_count}** 条，"
        f"拦下 **{considered}** 条（均为收益非零、被判定「删了编不过」或"
        "不属于本项目可改的范围）。"
    )
    lines.append("")

    if diagnosis.concentration:
        lines.append("### 成本集中在哪")
        lines.append("")
        lines.append("| 头文件 | 归属 | 归因行数 | 占全项目解析量 |")
        lines.append("|---|---|---:|---:|")
        for row in diagnosis.concentration:
            lines.append(
                f"| `{labels.get(row.path, _rel(row.path, root))}` "
                f"| {_KIND_LABEL.get(row.kind, row.kind.value)} "
                f"| {_fmt_int(row.attributed_lines)} | {row.share * 100:.1f}% |"
            )
        lines.append("")
        lines.append(
            f"前 {len(diagnosis.concentration)} 个头文件占全项目解析量的 "
            f"**{diagnosis.top_share_percent}%**；其中项目自有头文件占 "
            f"**{diagnosis.project_share_percent}%**"
            "（其余来自系统/第三方头，删 include 行对这部分无能为力）。"
        )
        lines.append("")

    if diagnosis.blocked_top:
        lines.append("### 收益最高、但被拦下的几条")
        lines.append("")
        lines.append("| 位置 | include | 放大后行数 | 影响 TU | 拦下原因 | 证据 |")
        lines.append("|---|---|---:|---:|---|---|")
        for item in diagnosis.blocked_top:
            where = labels.get(item.site_file, _rel(item.site_file, root))
            if item.line:
                where = f"{where}:{item.line}"
            detail = item.detail or "—"
            lines.append(
                f"| `{where}` | `{labels.get(item.header, _rel(item.header, root))}` "
                f"| {_fmt_int(item.amplified_lines)} | {item.reach} "
                f"| {item.reason_label} | {detail} |"
            )
        lines.append("")
        counts = ", ".join(
            f"{label} {diagnosis.counts[reason]} 条"
            for reason, label in BLOCK_REASONS.items()
            if diagnosis.counts.get(reason)
        )
        if counts:
            lines.append(f"拦下原因分布：{counts}。")
            lines.append("")

    if diagnosis.advice:
        lines.append("### 下一步能做什么")
        lines.append("")
        for item in diagnosis.advice:
            lines.append(f"- {item}")
        lines.append("")
    return lines


def _unique_tails(paths, root: Path) -> dict[Path, str]:
    """项目内相对路径；项目外路径保留**足够长的尾部**以保证彼此不重名。

    为什么不复用 :func:`_rel`：它对项目外路径固定保留最后两段，注释里写着
    「makes each row unique」——但实测并不成立：clang 的
    ``lib/clang/23/include/limits.h`` 与 mingw 的
    ``x86_64-w64-mingw32/include/limits.h`` 最后两段都是 ``include/limits.h``，
    在诊断表里变成两行一模一样的路径。同名不同 SDK 的头必须能分辨，否则
    「谁被拦下了」这件事没法核对。
    """
    labels: dict[Path, str] = {}
    foreign: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            # 同一个路径可能同时出现在成本榜和被拦下榜里。不去重的话，
            # 下面数「有多少个路径共用这个尾部」会把它自己数两次，于是
            # 永远「不唯一」，一路退化成打印绝对路径。
            continue
        seen.add(path)
        try:
            labels[path] = str(path.relative_to(root))
        except ValueError:
            foreign.append(path)
    for path in foreign:
        for depth in range(2, len(path.parts) + 1):
            candidate = str(Path(*path.parts[-depth:]))
            clashes = sum(
                1
                for other in foreign
                if str(Path(*other.parts[-depth:])) == candidate
            )
            if clashes == 1:
                labels[path] = candidate
                break
        else:  # pragma: no cover - 同路径不可能不同，兜底而已
            labels[path] = str(path)
    return labels


def _advice_section(scan: ProjectScan, root: Path) -> list[str]:
    """删除建议与取舍依据——本报告的核心小节。

    旧版到这里只有一句「疑似未使用」，把判断留给读者。这一节按三个维度
    （功能影响 / 维护成本 / 构建体验）把每条证据摆出来，最后给一个明确
    结论：建议删除 / 保留 / 需人工判断。没有证据支持的结论不存在——
    未实测的候选连结论都不给。
    """
    advised = [c for c in scan.candidates if c.advice is not None]
    if not advised:
        return []

    # 组内排序：有实测收益的按省下的秒数排，其余按独占闭包行数排——
    # 都是「读者先看哪个」的顺序，与 candidate_rank_key 同一精神。
    advised.sort(
        key=lambda c: (
            _ADVICE_ORDER[c.advice.verdict],
            -(c.measured_seconds_saved or 0.0),
            -c.estimated_lines_saved,
            str(c.tu),
        )
    )

    lines = [
        "## 删除建议：值不值得删",
        "",
        "每个候选一个明确结论，依据按三个维度分开列。结论规则是确定性的，",
        "写死在代码里（`attrib/advice.py`），不由模型临场发挥：",
        "",
        "- **建议删除**：实测编译通过 **且** 耗时下降超过 "
        f"{TIME_NOISE_FLOOR * 100:.0f}% 噪声线；",
        "- **保留**：编译器已经否决（编译不过），或定位不到该注释哪一行；",
        "- **需人工判断**：编译通过但收益在噪声内、或本轮没有实测。"
        "其中维护收益算式成立（全项目仅一个使用者，且独占闭包 ≥ "
        f"{MAINTENANCE_LEAN_LINES:,} 行或占本单元 ≥ "
        f"{MAINTENANCE_LEAN_SHARE * 100:.0f}%）的倾向删除，否则倾向保留。"
        "删除的代价固定为一次 review 加一遍该单元测试，算式写在每条理由里。",
        "",
        "> **编译通过 ≠ 功能正确。** 「建议删除」的全部含义是「这个编译单元"
        "不带这行也能编、而且更快」；合并前请运行该单元的测试。",
        "",
    ]

    groups: list[tuple[str, list] ] = [
        ("建议删除（实测有效）", [c for c in advised if c.advice.verdict is AdviceVerdict.REMOVE]),
        ("需人工判断（编译可行，但证据不足以下结论）",
         [c for c in advised if c.advice.verdict is AdviceVerdict.HUMAN]),
        ("保留（实测不可行，别再试）", [c for c in advised if c.advice.verdict is AdviceVerdict.KEEP]),
    ]

    for title, members in groups:
        if not members:
            continue
        lines.append(f"### {title}")
        lines.append("")
        for candidate in members:
            advice = candidate.advice
            location = f"第 {candidate.line} 行" if candidate.line else "行号未知"
            lines.append(
                f"#### `{_rel(candidate.tu, root)}` → `{_rel(candidate.header, root)}`"
                f"（{location}）——{_verdict_cell(candidate)}"
            )
            lines.append("")
            for reason in advice.reasons:
                lines.append(_reason_line(reason))
            lines.append("")

    if scan.reproduce_command:
        lines += [
            "### 复现方式",
            "",
            "以上每条结论都可以用同一条命令重跑复现：",
            "",
            "```bash",
            scan.reproduce_command,
            "```",
            "",
        ]
    return lines


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


def _probe_section(scan: ProjectScan, root: Path) -> list[str]:
    """The measured verdict, or an honest statement that nothing was measured.

    The distinction matters more than it looks.  A report that shows only
    estimates reads like a list of confirmed problems; the same report with
    ``--probe`` run shows which of them survived a real compile.  When the
    stage did not run, this section says so in as many words -- "not measured"
    must never be allowed to look like "no problem".
    """
    results = scan.probe_results
    if not results:
        return [
            "## 实测状态",
            "",
            "**本轮没有做实测。** 上表全部是估算——按行数折算的预计收益，"
            "不是秒数，也不代表这些 include 删掉之后还能编译。",
            "",
            "要拿到可引用的结论，加 `--probe`：工具会逐个真的编译「去掉这行 include」"
            "的版本，只对编译通过的候选做耗时测量。",
            "",
        ]

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = "、".join(f"{k} {v} 个" for k, v in sorted(counts.items()))

    lines = [
        "## 实测结论（`--probe`）",
        "",
        f"本轮实测 {len(results)} 个候选：{summary}。",
        "",
        "> **口径**：`compiles` = 去掉这行后能编译（未计时）；`improved` = 编译通过"
        "且耗时下降超过 2% 噪声线；`compile-error` = 编译器拒绝，附第一条错误；"
        "`ambiguous` / `not-found` = 定位不到该注释哪一行，工具不猜。",
        "",
    ]

    improved = [r for r in results if r.status == "improved"]
    if improved:
        improved.sort(key=lambda r: -(r.seconds_saved or 0.0))
        has_memory = any(
            r.baseline_memory_kb is not None and r.patched_memory_kb is not None
            for r in improved
        )
        has_size = any(
            r.baseline_object_bytes is not None and r.patched_object_bytes is not None
            for r in improved
        )
        has_test = any(getattr(r, "test_status", "") for r in improved)
        header = "| 编译单元 | include | 基线 | 移除后 | 节省 |"
        sep = "|---|---|---:|---:|---:|"
        if has_memory:
            header += " GGC 内存 |"
            sep += "---:|"
        if has_size:
            header += " 目标文件 |"
            sep += "---:|"
        if has_test:
            header += " 单元测试 |"
            sep += "---|"
        lines += ["### 实测有效的改动", "", header, sep]
        for result in improved:
            saved = result.percent_saved or 0.0
            row = (
                f"| `{_rel(result.candidate.tu, root)}` "
                f"| `{_rel(result.candidate.header, root)}` "
                f"| {result.baseline_seconds * 1000:.0f} ms "
                f"| {result.patched_seconds * 1000:.0f} ms "
                f"| **{saved:.1f}%** |"
            )
            if has_memory:
                if (
                    result.baseline_memory_kb is not None
                    and result.patched_memory_kb is not None
                ):
                    row += (
                        f" {result.baseline_memory_kb / 1024:.0f} kB → "
                        f"{result.patched_memory_kb / 1024:.0f} kB |"
                    )
                else:
                    row += " 该工具链不提供 |"
            if has_size:
                if (
                    result.baseline_object_bytes is not None
                    and result.patched_object_bytes is not None
                ):
                    delta = result.patched_object_bytes - result.baseline_object_bytes
                    row += (
                        f" {result.baseline_object_bytes:,} B → "
                        f"{result.patched_object_bytes:,} B（{delta:+,} B） |"
                    )
                else:
                    row += " 未测 |"
            if has_test:
                status = getattr(result, "test_status", "")
                label = {
                    "passed": "✅ 通过",
                    "failed": "❌ 失败（结论已降级）",
                    "error": "⚠️ 未能取证",
                }.get(status, "未跑")
                row += f" {label} |"
            lines.append(row)
        lines += [
            "",
            "> GGC 内存是编译器自报的分配峰值（`-ftime-report`），不是进程 RSS；",
            "> 目标文件大小为一次完整编译实测。两列缺列表示该工具链不提供或未测，",
            "> **不是 0**。每个候选的完整取舍依据见上一节「删除建议」。",
            "",
        ]
        timed_notes = [r.timing_note for r in improved if r.timing_note]
        if timed_notes:
            lines += [
                f"> **计时口径提醒**：{timed_notes[0]}。涉及 "
                f"{len(timed_notes)} 个候选，其秒数可与墙钟计时互证，"
                "但不可与编译器内部阶段计时混谈。",
                "",
            ]

    rejected = [
        r
        for r in results
        if r.status in ("compile-error", "ambiguous", "not-found", "error")
    ]
    if rejected:
        lines += [
            "### 实测被拒的候选（这些不要再去试）",
            "",
            "| 编译单元 | include | 结论 | 编译器说 |",
            "|---|---|---|---|",
        ]
        for result in rejected[:20]:
            detail = " ".join(str(result.message).split())[:110]
            lines.append(
                f"| `{_rel(result.candidate.tu, root)}` "
                f"| `{_rel(result.candidate.header, root)}` "
                f"| {result.status} | {detail} |"
            )
        lines += [
            "",
            "这些行同样有价值：它们把「试过、不行」记录下来，"
            "下一轮扫描不会再把同一个候选排到前面。",
            "",
        ]
    return lines


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
    lines.append(f"- 唯一文件行数：{_fmt_int(scan.total_project_lines)} 行")
    if scan.total_project_lines > 0:
        lines.append(
            f"- **Blowup Factor：{scan.blowup_factor():.1f}×**"
            "（平均每行被解析的次数；与 Header Hero 同定义，见 `docs/competitors.md`）"
        )
    else:
        lines.append("- Blowup Factor：未统计（分母缺失，**不代表没有放大**）")
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

    # -- developer-facing: incremental build tax -------------------------
    # 与上一节是两个不同的成本口径，必须分开讲：全量成本高不等于编辑循环慢。
    # 一个只在 1 个 TU 里用的巨型头，attributed_lines 很高但增量税很低；
    # 反过来，一个被 200 个轻量 TU 共享的小头，增量税可能高得多。
    taxed = sorted(
        (stats for stats in project_headers if stats.incremental_seconds > 0),
        key=lambda stats: stats.incremental_seconds,
        reverse=True,
    )
    if taxed:
        lines.append("## 实验性：增量构建税（未校准，勿据此决策）")
        lines.append("")
        lines.append(
            "上一节回答「整个项目为它付了多少解析量」；这一节试答「**你改它一行，"
            "下一次增量构建要等多久**」。这是编辑循环里的实际痛感，也是现有竞品没有"
            "给出的视角——但它目前**只是原型**：受控实验（`experiments/incremental_tax.py`）"
            "显示预测比实测**偏高约 1.7×**，因为扫描的 `--full-compile` 会为每个单元"
            "额外链接一次，而真实增量构建只编译不链接。校准完成前，本表不得作为结论引用。"
        )
        lines.append("")
        lines.append("| 头文件 | 重编 TU | 预计等待 | 占单次全量构建 |")
        lines.append("|---|---:|---:|---:|")
        for stats in taxed[:10]:
            share = (
                stats.incremental_seconds / scan.baseline_seconds * 100
                if scan.baseline_seconds
                else 0.0
            )
            lines.append(
                f"| `{_rel(stats.path, root)}` | {stats.tu_reach} "
                f"| {_fmt_seconds(stats.incremental_seconds)} | {share:.0f}% |"
            )
        lines.append("")
        lines.append(
            "> 口径：扫描期每个 TU 实测耗时之和（默认 `-fsyntax-only`，不含链接）。"
            "它是一个**预测**，预测误差与验证方法见 `docs/methodology.md` 的"
            "「增量构建税」一节。`0` 表示该单元没有可用计时，不是「瞬间完成」。"
        )
        lines.append("")
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
            "> 「结论」列是明确判断（建议删除/保留/需人工判断），依据见下一节；"
            "「未实测」表示这一轮没有跑 `--probe`，此时不给结论——"
            "没测过就下判断是编造。"
        )
        lines.append("")
        lines.append("| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 结论 |")
        lines.append("|---|---|---:|---:|---:|---|---|")
        for candidate in scan.candidates_by_estimate()[:20]:
            lines.append(
                f"| `{_rel(candidate.tu, root)}` "
                f"| `{_rel(candidate.header, root)}` "
                f"| {len(candidate.exclusive_headers)} "
                f"| {_fmt_int(candidate.estimated_lines_saved)} "
                f"| {candidate.estimated_percent:.1f}% "
                f"| {candidate.usage_label} "
                f"| {_verdict_cell(candidate)} |"
            )
        lines.append("")

    # -- when the gate hands back nothing --------------------------------
    lines += _diagnosis_section(scan, root)

    # -- the decision: worth removing or not ------------------------------
    lines += _advice_section(scan, root)

    # -- measured: what the probe stage actually established --------------
    lines += _probe_section(scan, root)


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
