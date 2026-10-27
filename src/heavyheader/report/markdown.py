"""Markdown report rendering.

Markdown is the primary report format because it is the one a reviewer can
read inside a pull request, paste into an issue, or diff against last week's
version.  The HTML report is the pretty one; this one is the useful one.
"""

from __future__ import annotations

from pathlib import Path

from .. import PROJECT_URL, evidence
from ..attrib.advice import (
    MAINTENANCE_LEAN_LINES,
    MAINTENANCE_LEAN_SHARE,
    TIME_NOISE_FLOOR,
)
from ..attrib.diagnosis import BLOCK_REASONS
from ..models import AdviceVerdict, HeaderKind, ProjectScan, candidate_rank_key

__all__ = ["render_markdown", "markdown_summary_line", "render_pch_trial"]


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


def _downstream_cell(candidate, root: Path) -> str:
    """候选表的「下游引用」列。

    ``—`` 有两种含义必须分开：TU 级候选根本不做这个分析（删一行只影响
    一个编译单元，下游就是它自己）；头文件级候选是「算过、确实没有」。
    所以用 ``未分析`` / ``—`` 两种文案，绝不用同一个符号糊弄两种状态。
    名字只展示前两个，全量在 JSON 的 ``downstream_names`` 里。
    """
    if not candidate.is_header_site and not candidate.downstream_files:
        return "未分析"
    if not candidate.downstream_files:
        return "—"
    names = "、".join(candidate.downstream_names[:2])
    more = "…" if len(candidate.downstream_names) > 2 else ""
    return f"{len(candidate.downstream_files)} 文件（{names}{more}）"


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


def _pending_queue_section(scan: ProjectScan, root: Path, queue: list) -> list[str]:
    """还没实测的候选 = verify 的输入队列，按测量顺序列出来。

    旧版报告把「无法判定」当成留给读者的作业。这一节说清楚三件事：队列有
    多大、`verify` 按什么顺序测、测完它们会变成什么。无法判定层单列的依据
    是实测而不是修辞——Lua 语料上这一层 32/32 条通过三真值（编过 + 真少
    解析 + 程序没变，见 `experiments/failure_taxonomy.py` 的输出），
    「静态判不了」多数时候是判定层保守，不是候选有问题。
    """
    pending_pu = [c for c in queue if c.usage_verdict == "plausibly_unused"]
    pending_unknown = [c for c in queue if c.usage_verdict == "unknown"]
    pending_used = [c for c in queue if c.usage_verdict == "in_use"]
    lines = [
        "",
        f"### 待验证队列：{len(queue)} 条等 `verify` 裁定",
        "",
        "顺序即 `heavyheader verify --top N` 的测量顺序：**疑似未使用**先测",
        "（静态判定已经指向删除），**无法判定**次之（静态给不出结论，但反事实",
        "实验能裁——Lua 语料上这一层 32/32 条实测可删），**在用**默认不测",
        "（要测得加 `--ungated-sites`）。",
        "",
        f"- 疑似未使用 **{len(pending_pu)}** 条 · 无法判定 **{len(pending_unknown)}** 条"
        f" · 在用 **{len(pending_used)}** 条",
        "",
        "| 编译单元 | include | 预计省 (s) | 使用状态 | 判据 |",
        "|---|---|---:|---|---|",
    ]
    for candidate in queue[:10]:
        estimate = candidate.estimated_seconds_saved
        lines.append(
            f"| `{_rel(candidate.tu, root)}` "
            f"| `{_rel(candidate.header, root)}` "
            f"| {'—' if estimate is None else f'{estimate:.4f}'} "
            f"| {candidate.usage_label} "
            f"| {candidate.usage_reason or '—'} |"
        )
    lines.append("")
    return lines


def _advice_section(scan: ProjectScan, root: Path) -> list[str]:
    """删除建议与取舍依据——本报告的核心小节。

    旧版到这里只有一句「疑似未使用」，把判断留给读者。这一节按三个维度
    （功能影响 / 维护成本 / 构建体验）把每条证据摆出来，最后给一个明确
    结论：建议删除 / 保留 / 需人工判断。没有证据支持的结论不存在——
    未实测的候选连结论都不给。
    """
    # 还没实测的候选不是「留给读者判断」，是 verify 的输入队列。按 rank 顺序
    # （疑似未使用在前、无法判定次之）列出来：一条命令之后它们各自去哪里，
    # 报告说清楚。无法判定层值得单列的原因有实测支撑——Lua 语料上这一层
    # 32/32 条全部通过三真值（见 experiments/failure_taxonomy 数据），
    # 「静态判不了」多数时候是判定层保守，不是候选有问题。
    queue = sorted(
        (c for c in scan.candidates if c.advice is None), key=candidate_rank_key
    )
    advised = [c for c in scan.candidates if c.advice is not None]
    if not advised and not queue:
        return []
    if not advised:
        # 纯扫描（没跑 verify）时这一节只剩队列本身——那正是此刻最该说的。
        return _pending_queue_section(scan, root, queue)

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
        (
            "> **本轮已做代码等价校验**（`--check-codegen`）：被判「建议删除」的候选，"
            "去掉那行之后编出来的汇编与原来逐字节一致（调试信息与非代码行已归一化）。"
            "这排除了「编译通过但重载解析变了」的情况，但不等于业务正确性——测试仍然要跑。"
            if getattr(scan, "codegen_checked", False)
            else "> **本轮未做代码等价校验。** 加 `--check-codegen` 会让每个候选多两次编译，"
            "用来确认「删掉这行之后编出来的还是同一个程序」——没有这一步时，"
            "「能编过」不等于「行为没变」。"
        ),
        "",
    ]

    # 还没实测的候选不是「留给读者判断」，是 verify 的输入队列。按 rank 顺序
    # （疑似未使用在前、无法判定次之）列出来：一条命令之后它们各自去哪里，
    # 报告说清楚。无法判定层值得单列的原因有实测支撑——Lua 语料上这一层
    # 32/32 条全部通过三真值（见 experiments/failure_taxonomy 数据），
    # 「静态判不了」多数时候是判定层保守，不是候选有问题。
    queue = sorted(
        (c for c in scan.candidates if c.advice is None), key=candidate_rank_key
    )
    if queue:
        lines += _pending_queue_section(scan, root, queue)

    groups: list[tuple[str, list] ] = [
        ("建议删除（实测有效）", [c for c in advised if c.advice.verdict is AdviceVerdict.REMOVE]),
        ("需人工判断（编译可行，但证据不足以下结论）",
         [c for c in advised if c.advice.verdict is AdviceVerdict.HUMAN]),
        (
            "保留（实测不可行，别再试）",
            [c for c in advised if c.advice.verdict is AdviceVerdict.KEEP],
        ),
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


def _tier_label(result) -> str:
    """表格里那一格：档位简称 + 复测遍数。

    档位全称放在本节口径里解释，格子里只放能一眼扫完的短标签——但
    「仅编译通过」这一档必须连着警告，否则读者会把它读成「验证过」。
    """
    tier = evidence.tier_of(result)
    short = evidence.TIER_SHORT.get(tier, tier or "无证据")
    if tier == evidence.TIER_EQUIVALENT:
        return f"{short}（收益未过线）"
    if tier == evidence.TIER_COMPILES:
        return f"{short}（**程序可能变了**）"
    if tier == evidence.TIER_VERIFIED and getattr(result, "confirm_passes", 0):
        return f"{short}（复测 {result.confirm_passes} 遍）"
    return short


def _verified_layer(results: list, root: Path) -> list[str]:
    """已验证层级：编译器证据撑住的候选，与文本判据是否推荐无关。

    这一节的存在理由是实测出来的：落地收益最多的那几条（重复包含的
    stdio/stdlib/string）恰好被文本判据判成 ``in_use`` / ``unknown``。旧报告
    只按判据排，于是**省时最多的建议从不出现**。这里改按证据强度列。
    """
    layer = [r for r in results if evidence.tier_of(r) in evidence.GATED_TIERS]
    if not layer:
        return []
    summary = evidence.summarise(results)
    lines = [
        "### 已验证层级（按证据强度，不按文本判据）",
        "",
        f"本轮 {summary['measured']} 条实测里，{len(layer)} 条拿到编译器证据："
        "两侧各编到汇编、归一化预处理行标记与局部标号后比哈希，指纹一致。"
        "**「程序没变」是量出来的，不是推断出来的。**",
        "",
        "| 编译单元 | include | 文本判据 | 证据档 | 实测节省 |",
        "|---|---|---|---|---:|",
    ]
    for result in evidence.sort_by_evidence(layer):
        candidate = result.candidate
        saved = result.seconds_saved
        gain = (
            f"**−{result.percent_saved:.1f}%**（{saved * 1000:.0f} ms）"
            if saved and result.percent_saved
            else "未过噪声线"
        )
        lines.append(
            f"| `{_rel(candidate.tu, root)}` "
            f"| `{_rel(candidate.header, root)}` "
            f"| {candidate.usage_verdict or '?'} "
            f"| {_tier_label(result)} "
            f"| {gain} |"
        )
    lines += [""]
    outside = summary["outside_recommendation"]
    if outside:
        lines += [
            f"> **这一节里 {outside} 条被文本判据判为「在用 / 无法判定」**——"
            "判据说不清的地方，编译器说得清。已验证层级的增量几乎全在这里；"
            "反过来说，只按判据排序的报告永远看不到它们。",
            "",
        ]
    if summary["gate_ran"]:
        lines += [
            f"> 代码等价闸门本轮跑了 {summary['gate_ran']} 条、"
            f"拦下 {summary['gate_blocked']} 条（汇编变了）。"
            "**只放行不拦截的闸门等于没有闸门**，所以拦下数必须和放行数一起看。",
            "",
        ]
    return lines


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
            "两个档位可选：`--probe-compile` 只验「删了还能不能编」（每候选一次编译）；"
            "`--probe` 在它之上再测耗时（两侧各 N 次取最小，判 improved 后还要复测）。",
            "",
        ]

    mode = getattr(scan, "probe_mode", "") or "probe"
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    labels = {
        "compiles": "编译通过（未计时）",
        "compile-error": "编译失败",
        "improved": "省时（过噪声线）",
        "no-change": "无显著变化",
        "ambiguous": "定位歧义",
        "not-found": "找不到该行",
        "error": "出错",
    }
    summary = "、".join(
        f"{labels.get(k, k)} {v} 个" for k, v in sorted(counts.items())
    )

    lines = [
        "## 实测结论（`--probe-compile`：只验编译）"
        if mode == "probe-compile"
        else "## 实测结论（`--probe`）",
        "",
        f"本轮实测 {len(results)} 个候选：{summary}。",
        "",
        "> **口径**：`compiles` = 去掉这行后能编译（未计时）；`improved` = 编译通过"
        "且耗时下降超过 2% 噪声线；`compile-error` = 编译器拒绝，附第一条错误；"
        "`ambiguous` / `not-found` = 定位不到该注释哪一行，工具不猜。",
        "",
    ]
    if mode == "probe-compile":
        lines += [
            "> 本轮只做了编译探测：**「能编译」不等于「省时」**。要拿到秒数，"
            "对下面列出的 `compiles` 候选跑 `heavyheader verify` 或 `scan --probe`。",
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
        has_tier = any(getattr(r, "evidence_tier", "") for r in improved)
        header = "| 编译单元 | include | 基线 | 移除后 | 节省 |"
        sep = "|---|---|---:|---:|---:|"
        if has_tier:
            header += " 证据档 |"
            sep += "---|"
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
            if has_tier:
                row += f" {_tier_label(result)} |"
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

    lines += _verified_layer(results, root)

    compiles = [r for r in results if r.status == "compiles"]
    if compiles:
        lines += [
            "### 编译探测通过（这些还能编，但省不省时未知）",
            "",
            "| 位置 | include | 受影响 TU |",
            "|---|---|---:|",
        ]
        for result in compiles[:30]:
            candidate = result.candidate
            lines.append(
                f"| `{_rel(candidate.site, root)}` "
                f"| `{_rel(candidate.header, root)}` "
                f"| {len(candidate.affected_tus) or 1} |"
            )
        lines += [
            "",
            "> 这一列只证明「删掉这行还能编」。省不省时需要计时，本表**不提供**该结论。",
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
    health = compile_health_note(scan)
    if health:
        # 紧跟在单元计数后面，而不是丢进文末的「扫描警告」：这是决定
        # 「下面这些数字能不能用」的前提，读者不该先读完报告才发现。
        # 只加粗引导词——整行加粗会与 note 里已有的反引号/行内强调打架。
        lines.append(f"- **报告可信度**：{health}")
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
        lines.append(
            "| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 |"
            " 使用状态 | 判据 | 下游引用 | 结论 |"
        )
        lines.append("|---|---|---:|---:|---:|---|---|---|---|")
        for candidate in scan.candidates_by_estimate()[:20]:
            lines.append(
                f"| `{_rel(candidate.site, root)}` "
                f"| `{_rel(candidate.header, root)}` "
                f"| {len(candidate.exclusive_headers)} "
                f"| {_fmt_int(candidate.estimated_lines_saved)} "
                f"| {candidate.estimated_percent:.1f}% "
                f"| {candidate.usage_label} "
                f"| {candidate.usage_reason or '—'} "
                f"| {_downstream_cell(candidate, root)} "
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
        if pch.rebuild_units:
            share = pch.rebuild_share * 100.0
            lines.append(
                f"**维护税：**闭包里被最多单元包含的是 "
                f"`{_rel(pch.rebuild_header, root)}`。改动它会让整份 .gch 作废，"
                f"按本次扫描的编译方式计约 **{pch.rebuild_seconds:.1f} s**"
                "（扫描若用完整编译，这个数会更高）。"
                f"计的是**所有使用 PCH 的 {pch.rebuild_units} 个单元**——它们都要"
                "重新生成 .gch 再编译自己。"
            )
            lines.append("")
            lines.append(
                f"不放进 PCH 时，改这个头只需重编它自己触达的 "
                f"{pch.baseline_rebuild_units} 个单元；放进去后是 "
                f"{pch.rebuild_units} 个。"
            )
            lines.append("")
        if pch.fragile_headers:
            lines.append(
                f"**把这个 PCH 变得危险的是下面 {len(pch.fragile_headers)} 个头**——"
                "它们只被一部分单元使用，却因为被固化进 .gch，改一次就要全体"
                "使用 PCH 的单元陪着重编："
            )
            lines.append("")
            lines.append("| 头文件 | 本来只影响 | 改一次实际影响 | 放大 |")
            lines.append("|---|---:|---:|---:|")
            for path, reach, amp in pch.fragile_headers[:10]:
                lines.append(
                    f"| `{_rel(path, root)}` | {reach} 个单元 | "
                    f"{pch.rebuild_units} 个单元 | {amp:.1f} 倍 |"
                )
            if len(pch.fragile_headers) > 10:
                lines.append(f"| …另有 {len(pch.fragile_headers) - 10} 个 | | | |")
            lines.append("")
            lines.append(
                "规避办法：让 PCH 只包含全工程公共依赖的头，把这些只服务部分单元的"
                "头留在 PCH 之外（代价是那部分单元享受不到收益）。"
            )
            lines.append("")
        if pch.macro_prefixed_count:
            lines.append(
                f"**{pch.macro_prefixed_count} 个单元在包含这些头之前就改过宏**——"
                "PCH 会把宏状态固化在这些单元原本的 include 位置之前，"
                "被预编译的头看到的宏就与它本该看到的不同。这类单元一旦采用 PCH，"
                "可能直接编不过，也可能编过但语义变了（那个更危险）。"
            )
            lines.append("")
            lines.append("| 编译单元 | 行号 | 宏指令 |")
            lines.append("|---|---:|---|")
            for source, lineno, text in pch.macro_prefixed_units[:10]:
                shown = text if len(text) <= 60 else text[:57] + "..."
                lines.append(
                    f"| `{_rel(source, root)}` | {lineno} | `{shown}` |"
                )
            if pch.macro_prefixed_count > 10:
                lines.append(f"| …另有 {pch.macro_prefixed_count - 10} 个 | | |")
            lines.append("")
            lines.append(
                "这是**风险清单，不是失败预言**：json 单元测试上实测的 10 个"
                "预警里有 9 个加了 PCH 后编不过、1 个编得过，而没被预警的 14 个"
                "全部编得过——零漏报。想知道这批单元里到底哪几个会炸，"
                "跑 `heavyheader pch-verify`。"
            )
            lines.append("")
        lines.append("> 这是估算，不是承诺。五条限制：")
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
        lines.append(
            "这张表只是**文本候选**。要结论就用 `forward-verify`：它真的把 include "
            "换成前向声明再重编，并用汇编指纹判定代码是否等价。"
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


def render_iwyu_comparison(comparison, scan: ProjectScan | None = None) -> str:
    """IWYU 建议与本工具候选的对位结果。

    这张表回答的是「哪几条既站得住、又能省」，不是「谁更准」。所以
    **不做准确率、不做谁对谁错**——IWYU 判正确性，本工具判成本，两者
    答的不是同一个问题，把它们排成一场比赛是误导。
    """
    lines: list[str] = []
    lines.append("# IWYU 合并报告")
    lines.append("")
    lines.append(
        "include-what-you-use 判**正确性**（这条 include 该不该出现），"
        "本工具判**成本**（它让你多等几秒、删了会不会出事）。"
        "两边都指向「删」的，才是最该动手的那几条。"
    )
    lines.append("")
    lines.append(
        "> **边界**：本项目的 IWYU 解析验证于按官方格式构造的样本，"
        "**未经端到端实测**（构建 IWYU 需按 LLVM 版本源码编译，本机未做）。"
        "在真的跑通一次之前，下面的对位只可作为线索，不能作为结论。"
    )
    lines.append("")

    total_lines = comparison.ignored_lines + sum(
        len(r.add) + len(r.remove) + len(r.full_include_list) + 2 for r in comparison.records
    )
    if comparison.records and comparison.ignored_lines > total_lines / 2:
        lines.append(
            f"> **这份日志有一半以上的行（{comparison.ignored_lines} 行）没被识别成 "
            "IWYU 输出。** 拿错文件时这里不会报错，只会给出一张空表——"
            "所以宁可多说一句：请确认它是 `CXX=include-what-you-use ... 2> iwyu.out` 的产物。"
        )
        lines.append("")
    if comparison.incomplete_records:
        lines.append(
            f"> **{comparison.incomplete_records} 条记录不完整**（缺 `total` 段，"
            "日志可能被截断）。它们的「IWYU 没建议删」是**假**的——它只是没说到那里，"
            "不能当成「IWYU 认为没问题」。"
        )
        lines.append("")

    lines.append("## 总览")
    lines.append("")
    lines.append("| 口径 | 条数 |")
    lines.append("|---|---:|")
    lines.append(f"| IWYU 的删除建议 | {_fmt_int(comparison.iwyu_removals)} |")
    lines.append(f"| 其中本工具也判「删」 | {_fmt_int(comparison.agreement)} |")
    lines.append(f"| 本工具判 KEEP、IWYU 说删（分歧） | {_fmt_int(comparison.conflicts)} |")
    lines.append(
        f"| 本工具独有、IWYU 未提及的候选 | {_fmt_int(comparison.our_unmatched)} |"
    )
    lines.append("")

    if not comparison.pairs:
        lines.append(
            "IWYU 没有给出任何删除建议，或这份日志里没有可识别的 `#include` 删除行。"
        )
        lines.append("")
        return "\n".join(lines)

    lines.append("## 逐条对位")
    lines.append("")
    lines.append(
        "「对位方式」说明两边是靠什么认出是同一条的：**行号**最可靠；"
        "**文件名**是退回的模糊口径（同名不同目录会串），读者有权知道自己被哪种给的结论。"
    )
    lines.append("")
    lines.append("| 源文件 | include | 对位方式 | 本工具结论 | 预计省行数 | 两边一致 |")
    lines.append("|---|---|---|---|---:|:--:|")
    for pair in comparison.pairs:
        # 三种状态必须分开，不能都塞进「无」：没有候选 / 有候选但没跑
        # --probe / 有结论。「未实测」与主报告候选表的措辞一致。
        if not pair.has_candidate:
            verdict = "我们无此候选"
        elif pair.our_verdict is None:
            verdict = "未实测"
        else:
            verdict = pair.our_verdict
        saved = (
            _fmt_int(pair.estimated_lines_saved)
            if pair.estimated_lines_saved is not None
            else "—"
        )
        lines.append(
            f"| `{pair.source}` | `{pair.include}` | {pair.matched_by} "
            f"| {verdict} | {saved} | {'是' if pair.we_agree else '否'} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_pch_trial(trial, root: Path | None = None) -> str:
    """PCH 实测闭环的报告。

    与上面「收益估算」那一段必须分开：估算说的是**行数**，实测说的是
    **秒**，两者不能换算（预处理占比推算实测收益会高估约 1.6 倍）。合在
    一张表里会诱导读者把它们当成同一件事的两个精度。
    """
    lines: list[str] = ["# 预编译头（PCH）实测", ""]

    if not trial.pch_built:
        lines.append("**没有测成。**")
        lines.append("")
        lines.append(trial.pch_error or "预编译头未能生成。")
        lines.append("")
        lines.append(
            "这不是「PCH 没有收益」，是「这次没能量出收益」——两者在报告里"
            "必须分开写，把失败写成 0 收益会让人以为试过了。"
        )
        return "\n".join(lines) + "\n"

    rel_root = root or Path(".")
    headers = "、".join(f"`{_rel(h, rel_root)}`" for h in trial.headers) or "（无）"
    lines.append(f"把 {headers} 预编译成一份真实的 .gch，然后逐单元重编计时。")
    lines.append("")
    lines.append(
        f"生成 .gch 耗时 **{trial.pch_build_seconds:.2f} s**；"
        f"实测 **{trial.measured_units}** 个编译单元（每侧编译 {trial.runs} 次取最快）。"
    )
    lines.append("")

    # 主数字取 adopted 口径：那是**预编译头本身**带来的节省。全组口径含那些
    # PCH 从未生效的单元，那部分是噪声，不该出现在头条上。
    headline = trial.adopted_saved_percent
    verdict = (
        f"**实测节省 {headline:.1f}%**"
        f"（{_fmt_seconds(trial.adopted_baseline_seconds)} → "
        f"{_fmt_seconds(trial.adopted_pch_seconds)}）"
        if trial.adopted_saved_seconds > 0
        else f"**实测没有节省**（{_fmt_seconds(trial.adopted_baseline_seconds)} → "
        f"{_fmt_seconds(trial.adopted_pch_seconds)}，"
        f"{headline:+.1f}%）"
    )
    lines.append(verdict)
    lines.append("")
    if trial.adopted_units != trial.measured_units:
        gap = trial.measured_units - trial.adopted_units
        broken = sum(1 for unit in trial.units if not unit.pch_ok)
        why = (
            f"，其中 {broken} 个加了 PCH 后编不过"
            if broken
            else f"，其中 {gap} 个编译器未采用 .gch"
        )
        lines.append(
            f"全组口径（含 {gap} 个用不上 PCH 的单元{why}）"
            f"是 {trial.saved_percent:+.1f}%——两组数字都列出，是因为只报前者会把"
            "噪声算成收益，只报后者会让人以为整组单元都享受得到。"
        )
        lines.append("")

    # -- 估算 vs 实测：只比覆盖率，不比数值 -----------------------------
    lines.append("| 指标 | 估算（行数口径） | 实测（秒口径） |")
    lines.append("|---|---:|---:|")
    lines.append(
        f"| 覆盖率 | {trial.estimated_coverage:.0f}%"
        f"（{trial.candidate_units}/{trial.total_units} 个单元含这些头）"
        f" | {trial.measured_coverage:.0f}%"
        f"（{trial.adopted_units}/{trial.measured_units} 个真的用上 .gch）|"
    )
    lines.append(
        f"| 全项目口径 | — "
        f"| {trial.project_coverage:.0f}%"
        f"（{trial.adopted_units} 个 / 全项目 {trial.total_units} 个单元）|"
    )
    lines.append("")
    lines.append(
        "两个覆盖率不是同一个分母：估算按「包含这些头的单元」算，是**上界**；"
        "实测按「编译器真的采用了 .gch 的单元」算，且只测了编译选项完全一致的一组，"
        f"是**下界**（这一组指纹 `{trial.group_signature}`，共 {trial.group_size} 个单元）。"
        "行数与秒之间不做换算。"
    )
    lines.append("")

    if trial.break_even_units is not None:
        lines.append(
            f"按实测的单单元收益摊，需要约 **{trial.break_even_units} 个单元**"
            "才能把生成 .gch 的一次性开销赚回来。"
        )
        lines.append("")

    # -- 逐单元 ----------------------------------------------------------
    lines.append("| 编译单元 | 无 PCH | 有 PCH | 变化 | 采用 |")
    lines.append("|---|---:|---:|---:|:--:|")
    for unit in trial.units[:20]:
        name = f"`{_rel(unit.source, rel_root)}`"
        if not unit.pch_ok:
            # 编译失败的那一次必然「更快」——它提前退出了。把那个数字写进
            # 「变化」列，读者会读成「PCH 让它快了 500 ms」。
            lines.append(
                f"| {name} | {_fmt_seconds(unit.baseline_seconds)} "
                f"| 编译失败 | — | 否 |"
            )
            continue
        delta = unit.delta_seconds
        mark = f"-{_fmt_seconds(delta)}" if delta > 0 else f"+{_fmt_seconds(-delta)}"
        lines.append(
            f"| {name} | {_fmt_seconds(unit.baseline_seconds)} "
            f"| {_fmt_seconds(unit.pch_seconds)} | {mark} "
            f"| {'是' if unit.pch_adopted else '否'} |"
        )
    if len(trial.units) > 20:
        lines.append(f"| …另有 {len(trial.units) - 20} 个 | | | | |")
    lines.append("")

    # -- 静态预警 vs 实测 ------------------------------------------------
    if trial.macro_prefixed_count:
        lines.append("### 静态预警与实测的对照")
        lines.append("")
        lines.append(
            "「预置宏」是编译前就能算出来的：这个单元在包含候选头之前就 "
            "`#define`/`#undef` 过，PCH 会把宏状态固化在头本该看到的位置之前。"
            "下面这张表是它的实际表现——预警的价值由实测打分，不由我们声明。"
        )
        lines.append("")
        lines.append("| | 加 PCH 后编不过 | 加 PCH 后编得过 |")
        lines.append("|---|---:|---:|")
        lines.append(
            f"| **有预置宏**（预警） | {trial.prefixed_and_broken} "
            f"| {trial.prefixed_and_ok} |"
        )
        lines.append(
            f"| 无预置宏 | {trial.clean_and_broken} | {trial.clean_and_ok} |"
        )
        lines.append("")
        if trial.prefixed_and_broken + trial.clean_and_broken == 0:
            lines.append("本轮没有单元编不过，预警的准确率无从检验。")
        else:
            lines.append(
                f"**召回 {trial.warning_recall:.0f}%、精确 {trial.warning_precision:.0f}%**："
                f"所有编不过的 {trial.prefixed_and_broken + trial.clean_and_broken} 个单元里，"
                f"{trial.prefixed_and_broken} 个被预警覆盖；被预警的 "
                f"{trial.macro_prefixed_count} 个里 {trial.prefixed_and_broken} 个真的编不过。"
            )
            lines.append("")
            lines.append(
                "召回比精确更重要：漏报会让一个单元在「看起来成功了」的情况下改变语义，"
                "虚报只是让人多检查一个单元。"
            )
        lines.append("")

    for note in trial.notes:
        lines.append(f"> - {note}")
    lines.append("")
    return "\n".join(lines) + "\n"


_FORWARD_STATUS_TEXT = {
    "verified": "已证等价",
    "equivalent": "等价·噪声内",
    "compiles-only": "仅编译通过",
    "codegen-differs": "代码生成改变",
    "compile-error": "编不过",
    "ambiguous": "无法定位 include",
    "skipped": "未测",
    "needs-split": "需先拆头",
}


def render_forward_trial(trial, root: Path | None = None) -> str:
    """前向声明实测闭环的报告。

    与 ``scan --forward-decl`` 的分工必须写清楚：那条命令给的是**文本候选**
    （正则认为「只当指针用」），这条给的是**把 include 换成前向声明之后编译器
    说的话**。前者的判断可以是错的，后者不能。
    """
    rel_root = root or Path(".")
    lines: list[str] = ["# 前向声明实测", ""]
    lines.append(
        f"对 **{trial.considered}** 条文本候选中的 "
        f"**{trial.measured_units + trial.compile_error_units}** 条真的做了替换"
        f"（注释掉 `#include`、在原位置写入前向声明，每侧编译 {trial.runs} 次取最快）。"
    )
    lines.append("")

    if trial.measured_units == 0 and trial.compile_error_units == 0:
        lines.append("**没有一条候选被真的测过。**")
        lines.append("")
        for note in trial.notes:
            lines.append(f"- {note}")
        if trial.needs_split_units:
            lines.append(
                f"- {trial.needs_split_units} 条候选所在的头里还有需要完整定义的"
                "类型，整条 `#include` 换掉必然编不过；先把那些类型拆到单独的头"
            )
        if trial.skipped_units:
            lines.append(f"- {trial.skipped_units} 条候选定位不到唯一的 include 行")
        lines.append("")
        lines.append(
            "这不是「前向声明没有收益」，是「这次没能量出来」——两者在报告里"
            "必须分开写。"
        )
        return "\n".join(lines) + "\n"

    if trial.equivalent_units:
        verdict = (
            f"**已证等价 {trial.equivalent_units} 条，合计 "
            f"{_fmt_seconds(trial.equivalent_baseline_seconds)} → "
            f"{_fmt_seconds(trial.equivalent_baseline_seconds - trial.equivalent_saved_seconds)}"
            f"（省 {trial.equivalent_saved_percent:.1f}%）**"
        )
    else:
        verdict = "**没有一条候选做到「已证等价」，因此没有可报收益。**"
    lines.append(verdict)
    lines.append("")
    if trial.codegen_checked:
        codegen_note = "本轮做了汇编指纹比对。"
    elif getattr(trial, "codegen_requested", True):
        codegen_note = (
            "**本轮没有做汇编指纹比对**（这套工具链给不出 `-S` 汇编），"
            "所以下面最多只到「仅编译通过」，不判定代码是否等价。"
        )
    else:
        codegen_note = (
            "本轮按 `--no-codegen-check` 关掉了汇编指纹比对，"
            "所以下面最多只到「仅编译通过」——那是开关的选择，不是工具的能力上限。"
        )
    lines.append(
        "「已证等价」= 编译通过 **且** 汇编指纹一致 **且** 收益超过噪声地板。"
        + codegen_note
    )
    lines.append("")

    lines.append("| 编译单元 | 头 | 类型 | 原 | 换后 | 变化 | 结论 |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    for unit in trial.trials[:20]:
        name = f"`{_rel(unit.tu, rel_root)}`"
        header = f"`{_rel(unit.header, rel_root)}`"
        types = "、".join(unit.types) or "—"
        status = _FORWARD_STATUS_TEXT.get(unit.status, unit.status)
        if not unit.measured:
            # 编不过的那一侧「更快」——它提前退出了。把那个数字写进「变化」列，
            # 读者会读成「前向声明让它快了 500 ms」。
            lines.append(
                f"| {name} | {header} | {types} | "
                f"{_fmt_seconds(unit.baseline_seconds) if unit.baseline_seconds else '—'} "
                f"| — | — | {status} |"
            )
            continue
        saved = unit.saved_seconds or 0.0
        mark = f"-{_fmt_seconds(saved)}" if saved > 0 else f"+{_fmt_seconds(-saved)}"
        lines.append(
            f"| {name} | {header} | {types} "
            f"| {_fmt_seconds(unit.baseline_seconds or 0.0)} "
            f"| {_fmt_seconds(unit.patched_seconds or 0.0)} | {mark} | {status} |"
        )
    if len(trial.trials) > 20:
        lines.append(f"| …另有 {len(trial.trials) - 20} 条 | | | | | | |")
    lines.append("")

    # -- 写进源码的那几行，原样给出 --------------------------------------
    written = [u for u in trial.trials if u.declarations][:3]
    if written:
        lines.append("写进源码的前向声明（原位置，受同一个 `#ifdef` 管辖）：")
        lines.append("")
        lines.append("```cpp")
        for unit in written:
            lines.append(f"// {_rel(unit.tu, rel_root)}")
            lines.append(unit.declarations)
        lines.append("```")
        lines.append("")

    # -- 没测成的，把原因摆出来 ------------------------------------------
    failed = [
        u
        for u in trial.trials
        if u.status in ("compile-error", "codegen-differs", "needs-split", "skipped", "ambiguous")
    ]
    if failed:
        lines.append("### 没能采纳的候选")
        lines.append("")
        for unit in failed[:10]:
            status = _FORWARD_STATUS_TEXT.get(unit.status, unit.status)
            detail = unit.error or unit.note or "（无更多信息）"
            lines.append(
                f"- `{_rel(unit.header, rel_root)}` → "
                f"`{_rel(unit.tu, rel_root)}`：**{status}**，{detail}"
            )
        if len(failed) > 10:
            lines.append(f"- …另有 {len(failed) - 10} 条")
        lines.append("")
        if trial.compile_error_units:
            lines.append(
                "「编不过」是**结论**，不是工具出错：前向声明换上去之后这个单元"
                "确实用到了完整类型，文本判据看不到这一点。"
            )
            lines.append("")

    if trial.slower_units:
        lines.append(
            f"有 {trial.slower_units} 条替换后反而更慢——已列在表里，未从合计中"
            "倒扣：这份报告回答的是「采纳这些建议能省多少」，一条让编译变慢的"
            "建议本来就不该被采纳。"
        )
        lines.append("")

    for note in trial.notes:
        lines.append(f"> - {note}")
    lines.append("")
    return "\n".join(lines) + "\n"


#: 失败占比超过这个值就不能再说「数字可能偏低」了——那句话暗示数字大体可用、
#: 只是少算了一点；而实际情况是报告只描述了项目的一小部分。
_PARTIAL_COVERAGE_RATIO = 0.2
#: 失败占比超过这个值时，报告不足以支撑任何结论。
_UNUSABLE_RATIO = 0.5

#: 三种最常见的原因，都是本项目真实踩到过的，不是凭空列的检查清单。
#: 保持纯文本（不带 Markdown 强调）：同一段话既要进 Markdown 报告又要进
#: stderr，强调由调用方加，否则一处加粗会破坏另一处的渲染。
_COMMON_CAUSES = (
    "最常见的原因：`-I` 相对的是进程当前目录而不是扫描根、缺 `-D` 宏、缺第三方头"
)


def compile_health_note(scan: ProjectScan) -> str | None:
    """失败单元占比高时，把报告的可信度说清楚。全部成功则返回 ``None``。

    分档不是修辞。2026-10-27 在 json 语料上真实发生过一次：72 个单元里 71 个
    因 `-I` 参照系错误而失败，而当时的提示是「数字可能偏低」——71/72 不是偏低，
    是这份报告根本不能拿来做决策。措辞必须跟着占比走：

    - 少量失败（≤ 20%）：数字可能偏低。
    - 部分失败（20%–50%）：只描述成功那部分，别当全项目结论。
    - 多数失败（> 50%）：不足以支撑任何结论，先修好编译。

    后两档附上常见原因，因为「71 个找不到头文件」和「我的搜索路径参照系错了」
    之间还差一步推理，而这一步此前是被留给读者的。
    """
    total = len(scan.tus)
    failed = len(scan.failed_tus)
    if not failed or not total:
        return None

    ratio = failed / total
    percent = f"{ratio * 100:.0f}%"
    if ratio > _UNUSABLE_RATIO:
        return (
            f"{failed}/{total} 个编译单元失败（{percent}）——超过一半，"
            f"这份报告不足以支撑任何结论，请先修好编译。{_COMMON_CAUSES}。"
        )
    if ratio > _PARTIAL_COVERAGE_RATIO:
        return (
            f"{failed}/{total} 个编译单元失败（{percent}）。"
            f"报告只描述成功编译的那部分，不要当作整个项目的结论。{_COMMON_CAUSES}。"
        )
    return f"{failed}/{total} 个编译单元失败，报告中的数字可能偏低。"


def markdown_summary_line(scan: ProjectScan) -> str:
    """One-line summary, used by the CLI and by ``gate`` output."""
    unused = sum(1 for c in scan.candidates if c.usage_label == "疑似未使用")
    return (
        f"{len(scan.ok_tus)} TU · "
        f"{_fmt_int(scan.total_dependency_edges())} 条依赖边 · "
        f"{_fmt_int(scan.total_included_lines())} 行头文件解析量 · "
        f"{len(scan.candidates)} 个成本候选（其中 {unused} 个疑似未使用）"
    )
