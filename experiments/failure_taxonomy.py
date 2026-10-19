"""候选失败原因分类：把「16 条里只有 1 条被采纳」拆开，定位瓶颈在哪里。

三个问题，逐层回答：

1. **每条候选是怎么失败的**——不是笼统一句「有用率低」，而是分成：
   用法判定挡住（带符号证据）/ 编译失败（判定假阳性）/ 零解析收益（空头承诺）/
   静默改程序（汇编指纹变）/ 收益在噪声区间 / 压根不适合删除。
2. **瓶颈在候选生成还是在实验判定**——同时探测「被判 in_use 但实际可删」的
   镜像样本（按预估收益取前 K 条），量出保守漏报；与假阳性对照，才知道该往
   哪边使劲。
3. **新核心指标：每投入多少分析时间，产出多少条经复测的有效优化**——扫描与
   探测都计墙钟时间与编译次数；「已证实」= 编得过 + 真少解析文件 + 程序没变
   （解析层），计时层过噪声线的再高一级。

判据全部来自编译器，不来自人工判断。用法::

    python experiments/failure_taxonomy.py \
        --project <dir> --db <compile_commands.json> --compiler clang++ \
        --tus 35 --out experiments/results/failure-taxonomy-<name>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from candidate_precision import _codegen_same, _compile, _entries, _key  # noqa: E402

from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

#: 探测什么：全部疑似未使用 + 全部无法判定 + 预估收益前 K 条 in_use（镜像样本）
DEFAULT_MIRROR_TOP_K = 40


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def run(args) -> dict:
    entries = _entries(args.db) if args.db else {}

    # ---- 阶段一：扫描（计时）------------------------------------------------
    t0 = time.perf_counter()
    scan = scan_project(
        ScanOptions(
            root=args.project,
            compiler=args.compiler,
            include_dirs=list(args.includes),
            compile_commands=args.db,
            max_tus=args.tus or 500,
            probe_compile=False,
            probe_jobs=args.jobs,
        )
    )
    scan_seconds = time.perf_counter() - t0

    # ---- 选探测集：疑似未使用 + 无法判定 + in_use 镜像样本 ------------------
    candidates = list(scan.candidates)
    pus = [c for c in candidates if c.usage_verdict == "plausibly_unused"]
    unk = [c for c in candidates if c.usage_verdict == "unknown"]
    in_use = [c for c in candidates if c.usage_verdict == "in_use"]
    in_use.sort(key=lambda c: (-(c.estimated_seconds_saved or 0.0), str(c.tu)))
    mirror = in_use[: args.mirror_top_k]
    probe_set = [c for c in pus + unk + mirror if c.line is not None]

    unprobeable = [c for c in pus + unk + mirror if c.line is None]

    # ---- 阶段二：基线编译（每个 TU 一次，之后都是缓存）----------------------
    tus_needed = {_key(c.tu) for c in probe_set}
    t0 = time.perf_counter()
    baseline: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {key: pool.submit(_compile, entries[key]) for key in tus_needed if key in entries}
        for key, fut in futures.items():
            count, _rc = fut.result()
            baseline[key] = count if count is not None else 0
    baseline_seconds = time.perf_counter() - t0
    compiles = len(futures)

    # ---- 阶段三：删行探测（并行）--------------------------------------------
    t0 = time.perf_counter()

    def probe(candidate):
        key = _key(candidate.tu)
        entry = entries.get(key)
        if entry is None or candidate.line is None:
            return candidate, None, None
        after, rc = _compile(entry, candidate.line)
        return candidate, after, rc

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(probe, probe_set))
    probe_seconds = time.perf_counter() - t0
    compiles += len(probe_set)

    # ---- 阶段四：汇编等价闸门（对每条「编得过且真少解析」的候选）------------
    #: 镜像样本也过闸门——「in_use 保守漏报」必须同时满足「程序没变」才算真机会。
    need_codegen = [
        (c, rc)
        for c, after, rc in results
        if rc == 0
        and after is not None
        and (baseline.get(_key(c.tu), 0) - after) > 0
    ]
    t0 = time.perf_counter()

    def gate(item):
        candidate, rc = item
        key = _key(candidate.tu)
        return candidate, _codegen_same(
            entries[key], candidate.line, args.compiler
        )

    with ThreadPoolExecutor(max_workers=max(1, args.jobs // 2)) as pool:
        codegen_pairs = list(pool.map(gate, need_codegen)) if need_codegen else []
    codegen_seconds = time.perf_counter() - t0
    compiles += 2 * len(need_codegen)

    def _codegen_of(candidate) -> str | None:
        key = (str(candidate.tu), candidate.header.name, candidate.line)
        return dict(
            ((str(c.tu), c.header.name, c.line), v) for c, v in codegen_pairs
        ).get(key)

    # ---- 分类 ---------------------------------------------------------------
    timing_map = _load_timing(args.timing)

    def classify(candidate, after, rc) -> str:
        if after is None or rc != 0:
            if candidate.usage_verdict == "plausibly_unused":
                return "verdict_false_positive"
            return (
                "usage_rejected_compile_error"
                if candidate.usage_verdict == "in_use"
                else "unknown_rejected_compile_error"
            )
        shrink = baseline.get(_key(candidate.tu), 0) - after
        if shrink <= 0:
            if candidate.usage_verdict == "plausibly_unused":
                return "promise_vacuous"
            return (
                "usage_rejected_no_benefit"
                if candidate.usage_verdict == "in_use"
                else "unknown_rejected_no_benefit"
            )
        if _codegen_of(candidate) == "changed":
            if candidate.usage_verdict == "plausibly_unused":
                return "silent_change"
            return (
                "usage_rejected_silent_change"
                if candidate.usage_verdict == "in_use"
                else "unknown_rejected_silent_change"
            )
        if candidate.usage_verdict != "plausibly_unused":
            return (
                "usage_rejected_conservative_miss"
                if candidate.usage_verdict == "in_use"
                else "unknown_rejected_conservative_miss"
            )
        verdict = timing_map.get((_key(candidate.tu), candidate.header.name))
        if verdict == "above-noise":
            return "proven_timing_above_noise"
        if verdict == "below-noise":
            return "proven_timing_below_noise"
        return "parse_proven_timing_unmeasured"

    rows = []
    for candidate, after, rc in results:
        rows.append(
            {
                "tu": os.path.basename(str(candidate.tu)),
                "header": candidate.header.name,
                "line": candidate.line,
                "usage": candidate.usage_verdict,
                "reason": candidate.usage_reason,
                "estimate_s": round(candidate.estimated_seconds_saved or 0.0, 4),
                "category": classify(candidate, after, rc),
                "parse_shrink": (
                    None
                    if after is None or rc != 0
                    else baseline.get(_key(candidate.tu), 0) - after
                ),
            }
        )
    for candidate in unprobeable:
        rows.append(
            {
                "tu": os.path.basename(str(candidate.tu)),
                "header": candidate.header.name,
                "line": None,
                "usage": candidate.usage_verdict,
                "reason": candidate.usage_reason,
                "estimate_s": round(candidate.estimated_seconds_saved or 0.0, 4),
                "category": "not_probeable",
                "parse_shrink": None,
            }
        )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1

    total_analysis_seconds = scan_seconds + baseline_seconds + probe_seconds + codegen_seconds
    proven = sum(
        1
        for row in rows
        if row["category"].startswith("parse_proven")
        or row["category"] == "proven_timing_above_noise"
    )
    misses = sum(
        1
        for row in rows
        if row["category"].endswith("conservative_miss")
    )
    return {
        "project": str(args.project),
        "compiler": args.compiler,
        "tus": len(scan.ok_tus),
        "mirror_top_k": args.mirror_top_k,
        "candidates_total": len(candidates),
        "usage_split": {
            "plausibly_unused": len(pus),
            "unknown": len(unk),
            "in_use": len(in_use),
        },
        "probe_set": len(probe_set),
        "categories": counts,
        "rows": rows,
        "timing": {
            "jobs": args.jobs,
            "scan_seconds": round(scan_seconds, 1),
            "baseline_seconds": round(baseline_seconds, 1),
            "probe_seconds": round(probe_seconds, 1),
            "codegen_seconds": round(codegen_seconds, 1),
            "total_seconds": round(total_analysis_seconds, 1),
            "total_minutes": round(total_analysis_seconds / 60, 2),
            "compiler_invocations": compiles,
        },
        "headline": {
            "parse_proven": proven,
            "conservative_misses": misses,
            "proven_per_100_tu": (
                round(proven / len(scan.ok_tus) * 100, 1) if scan.ok_tus else 0.0
            ),
            "analysis_minutes_per_proven": (
                round(total_analysis_seconds / 60 / proven, 2) if proven else None
            ),
        },
    }


def _load_timing(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (_key(item["tu"]), item["header"]): item["verdict"] for item in data
    }


def render(report: dict) -> str:
    cats = report["categories"]
    n = report["probe_set"]
    proven = report["headline"]["parse_proven"]
    misses = report["headline"]["conservative_misses"]
    timing = report["timing"]
    lines = [
        "# 候选失败原因分类",
        "",
        f"- 工程：`{Path(report['project']).name}`（{report['tus']} 个编译单元）",
        f"- 候选总数 **{report['candidates_total']}**：疑似未使用 "
        f"{report['usage_split']['plausibly_unused']} / 无法判定 "
        f"{report['usage_split']['unknown']} / 在用 "
        f"{report['usage_split']['in_use']}",
        f"- 探测 {n} 条（疑似未使用 + 无法判定 + in_use 镜像前 "
        f"{report['mirror_top_k']}），编译器调用 {timing['compiler_invocations']} 次",
        "",
        "## A. 疑似未使用候选的失败原因（工具推送了的）",
        "",
        "| 类别 | 条数 | 含义 |",
        "|---|---:|---|",
        f"| **判定假阳性**（判可删、编译失败） | "
        f"{cats.get('verdict_false_positive', 0)} | "
        "usage 判定漏判——消耗了编译预算的纯浪费 |",
        f"| **空头承诺**（编得过、0 解析收益） | "
        f"{cats.get('promise_vacuous', 0)} | "
        "独占闭包预测错——删了不省任何解析 |",
        f"| **静默改程序**（汇编指纹变） | "
        f"{cats.get('silent_change', 0)} | "
        "编译通过且少解析，但程序被悄悄改了 |",
        f"| **已证实**（三条真值全过） | **{proven}** | "
        "编得过 + 真少解析 + 程序没变 |",
        "",
        "## B. 被判「在用」的镜像样本实测（工具没有推送的）",
        "",
        "| 类别 | 条数 | 含义 |",
        "|---|---:|---|",
        f"| 判定正确（实测编译失败） | "
        f"{cats.get('usage_rejected_compile_error', 0)} | "
        "真依赖——规则正确拦下，没浪费一次无效推荐 |",
        f"| 无收益（编得过、0 解析） | "
        f"{cats.get('usage_rejected_no_benefit', 0)} | "
        "删了也无害，但不构成机会 |",
        f"| **保守漏报（实测可删、程序没变）** | "
        f"**{misses}** | "
        "**被规则挡住的真实机会——零假阳性的代价** |",
        f"| 静默改程序（实测汇编变） | "
        f"{cats.get('usage_rejected_silent_change', 0)} | "
        "删除会让程序变化，拦下是对的 |",
        "",
        "## C. 核心指标：分析时间 → 已证实优化",
        "",
        f"- 分析耗时 **{timing['total_minutes']} 分钟**（扫描 "
        f"{timing['scan_seconds']}s + 基线 {timing['baseline_seconds']}s + "
        f"探测 {timing['probe_seconds']}s + 闸门 {timing['codegen_seconds']}s，"
        f"{timing['jobs']} 并行）",
        f"- **每 100 个编译单元找到 {report['headline']['proven_per_100_tu']} 条"
        "已证实优化**（解析层）",
        f"- **每 1 条已证实优化消耗 "
        f"{report['headline']['analysis_minutes_per_proven']} 分钟分析时间**",
        f"- 另有 **{misses} 条保守漏报**：usage 判定「在用」但实测可删"
        "（占镜像样本的 "
        f"{_pct(misses, sum(v for k, v in cats.items() if 'rejected' in k))}）",
        "",
    ]
    detail = [r for r in report["rows"] if r["usage"] == "plausibly_unused"]
    if detail:
        lines += [
            "## D. 疑似未使用候选逐条结局",
            "",
            "| TU | 头 | 判定理由 | 结局 |",
            "|---|---|---|---|",
        ]
        for r in detail:
            lines.append(
                f"| {r['tu']} | {r['header']} | {(r['reason'] or '—')[:40]} | "
                f"{r['category']} |"
            )
        lines.append("")
    misses_detail = [
        r for r in report["rows"] if r["category"].endswith("conservative_miss")
    ]
    if misses_detail:
        lines += [
            "## E. 保守漏报逐条（被判「在用」、实测可删）",
            "",
            "| TU | 头 | 判定理由 | 少解析文件 | 预估秒 |",
            "|---|---|---|---:|---:|",
        ]
        for r in sorted(misses_detail, key=lambda r: -(r["parse_shrink"] or 0)):
            lines.append(
                f"| {r['tu']} | {r['header']} | {(r['reason'] or '—')[:40]} | "
                f"{r['parse_shrink']} | {r['estimate_s']} |"
            )
        lines.append("")
    return "\n".join(lines)


def combine(paths: list[Path], out: Path) -> None:
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    total_proven = sum(r["headline"]["parse_proven"] for r in reports)
    total_misses = sum(r["headline"]["conservative_misses"] for r in reports)
    total_tus = sum(r["tus"] for r in reports)
    total_minutes = sum(r["timing"]["total_minutes"] for r in reports)
    lines = [
        "# 失败原因分类汇总（核心指标）",
        "",
        "| 工程 | TU | 候选 | 疑似未使用 | 已证实 | 保守漏报 | 每 100 TU | 分析分钟 | 分钟/条 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in reports:
        lines.append(
            f"| {Path(r['project']).name} | {r['tus']} | {r['candidates_total']} | "
            f"{r['usage_split']['plausibly_unused']} | "
            f"{r['headline']['parse_proven']} | "
            f"{r['headline']['conservative_misses']} | "
            f"{r['headline']['proven_per_100_tu']} | "
            f"{r['timing']['total_minutes']} | "
            f"{r['headline']['analysis_minutes_per_proven']} |"
        )
    lines += [
        f"| **合计** | **{total_tus}** | — | — | **{total_proven}** | "
        f"**{total_misses}** | **{total_proven / total_tus * 100:.1f}** | "
        f"**{total_minutes:.1f}** | **{total_minutes / total_proven:.2f}** |",
        "",
        "读法：",
        "",
        "1. **每 100 TU 已证实数**回答「真实项目里平均能找到多少条可复现优化」——",
        "   数字随工程冗余度变化（本组：Lua 55.9、doctest 12.5、spdlog 0、zlib 2.9），",
        "   这正是「语料决定上限」的实测证据：维护精良的工程天然没有冗余可删。",
        "2. **分钟/条**回答「找到的收益能不能抵消验证成本」——可以和使用者人工",
        "   逐个试删的成本直接对照（一次候选的人工验证 = 一次完整编译 + 语义判断）。",
        "3. **保守漏报**回答「零假阳性是不是靠保守换来」——漏报集中在 usage 判定",
        "   层（被判「在用/无法判定」的候选里实测可删的比例），是下一轮优化的主战场。",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--compiler", default="clang++")
    ap.add_argument("--tus", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--include", dest="includes", action="append", default=[])
    ap.add_argument("--mirror-top-k", type=int, default=DEFAULT_MIRROR_TOP_K)
    ap.add_argument("--timing", type=Path, help="计时层结论 JSON（可选）")
    ap.add_argument("--combine", nargs="+", type=Path, help="汇总多个结果 JSON")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    if args.combine:
        combine(args.combine, args.out)
        return 0

    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = render(report)
    args.out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
