"""闸门有没有让我们漏掉省时的改动？——把「闸门关时能编过」的候选也测一遍

为什么必须做这个实验
--------------------
`g2-gate-variants` 的三档探测显示：闸门放行 16 条、通过率 50%；闸门全关 66 条、
通过率 47%。**闸门对「能不能编」几乎没有预测力**，它只是把候选砍到 1/4。

那它有没有砍掉真正省时的改动？这是 G2-1 判定的要害：如果闸门关掉时能编过的
候选里还有 improved 的，那么「闸门有效」这句话就只对了一半——它省了时间，
但可能漏了收益。

协议
----
1. 用 ``probe_only`` 把闸门关的全部 66 条过一遍（已在 g2-gate-variants 做过，
   这里重算以便独立复现）；
2. 取出「能编过、但不在闸门放行集合里」的候选——这些正是闸门漏掉的部分；
3. 对它们做 **单遍** 测量（``runs=1``）做初筛：单遍读数噪声大，只用来**排序**，
   不用来下结论；
4. 单遍读数超过阈值的，按正式协议复测（``runs=3`` + ``confirm_passes=2``），
   过线才算 improved。

第 3 步为什么可以偷工：完整的 31 条 × 24 单元 × 6 次编译要六个小时，而这一步
只回答「有没有漏」，不产出对外结论。第 4 步才是结论，成本按命中数付。

用法::

    python experiments/g2_recall_off.py --project experiments/_work/doctest \
        --db experiments/_work/doctest/compile_commands.json --tus 24
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.advice import AGGREGATE_NOISE_FLOOR  # noqa: E402
from heavyheader.attrib.graph import (  # noqa: E402
    HeaderSizes,
    build_header_stats,
    classify_headers,
)
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def _key(candidate) -> tuple[str, str]:
    return (str(Path(candidate.site).name), str(Path(candidate.header).name))


def _run(scan, driver, options, candidates, *, probe_only, runs=1, confirm=1):
    saved = scan.candidates
    scan.candidates = list(candidates)
    started = time.perf_counter()
    try:
        results = verify_candidates(
            scan,
            driver,
            options,
            verify=VerifyOptions(
                top=0,
                probe_only=probe_only,
                runs=runs,
                confirm_passes=confirm,
                jobs=4,
            ),
        )
    finally:
        scan.candidates = saved
    return results, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--compiler", default=CLANG)
    parser.add_argument(
        "--probe-limit",
        type=int,
        default=0,
        help="只对「收益上限」最高的前 N 条做编译探测（0 = 全部）。"
        "探测是固定成本的大头；采样会如实报覆盖率",
    )
    parser.add_argument(
        "--screen-limit",
        type=int,
        default=0,
        help="初筛只测「收益上限」最高的前 N 条（0 = 全部）。"
        "目标只是回答「有没有漏掉值得采纳的」，不必给每条排序",
    )
    parser.add_argument(
        "--screen-threshold",
        type=float,
        default=2.0,
        help="单遍初筛的阈值（%%）；超过它才进入正式复测",
    )
    args = parser.parse_args()

    project = Path(args.project).resolve()
    db = Path(args.db).resolve()
    driver = detect_driver(args.compiler)
    driver_options = DriverOptions(extra_flags=list(TARGET_FLAGS))

    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=args.compiler,
            compile_commands=db,
            max_tus=args.tus,
            jobs=1,
            extra_flags=list(TARGET_FLAGS),
        ),
        driver=driver,
    )
    if not scan.ok_tus:
        print("扫描没有成功单元")
        return 1
    scan.candidates = []

    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(project, {h for tu in scan.ok_tus for h in tu.dependencies})
    strict = find_header_candidates(scan, sizes, kinds)
    off = find_header_candidates(scan, sizes, kinds, require_plausibly_unused=False)
    strict_keys = {_key(c) for c in strict}
    print(f"闸门放行 {len(strict)} 条 / 闸门关 {len(off)} 条")

    # 探测是固定成本的大头（每条要把受影响单元各编一次）。按收益上限采样，
    # 并如实报覆盖率——「漏掉的收益」几乎只可能出现在上限高的那一头。
    off.sort(key=lambda c: -c.amplified_lines_saved)
    pool_total = len(off)
    pool = off[: args.probe_limit] if args.probe_limit else off
    if len(pool) < pool_total:
        covered = sum(c.amplified_lines_saved for c in pool)
        overall = sum(c.amplified_lines_saved for c in off) or 1
        print(
            f"探测采样：前 {len(pool)} / {pool_total} 条，"
            f"覆盖收益上限的 {covered / overall:.0%}（--probe-limit）",
            flush=True,
        )
    print("阶段 1：闸门关的候选只验编译……", flush=True)
    probed, probe_seconds = _run(
        scan, driver, driver_options, pool, probe_only=True
    )
    compiles = [r.candidate for r in probed if r.status == "compiles"]
    print(
        f"  能编过 {len(compiles)} / {len(pool)}（{probe_seconds:.0f}s）", flush=True
    )

    outside = [c for c in compiles if _key(c) not in strict_keys]
    inside = [c for c in compiles if _key(c) in strict_keys]
    print(f"  其中闸门没放行的 {len(outside)} 条 —— 这些正是可能被漏掉的", flush=True)

    # 采样而不是全量：初筛的目的只是「有没有漏掉值得采纳的」，不是给每条排序。
    # 按放大收益（独占行数 × 受影响单元数）从高到低取前 N 条，并如实报覆盖率。
    outside.sort(key=lambda c: -c.amplified_lines_saved)
    total_outside = len(outside)
    if args.screen_limit and args.screen_limit < len(outside):
        outside = outside[: args.screen_limit]
        print(
            f"  初筛采样：按收益上限取前 {len(outside)} / {total_outside} 条"
            f"（--screen-limit）",
            flush=True,
        )

    print("阶段 2：对闸门没放行但能编过的候选做单遍初筛……", flush=True)
    screened, screen_seconds = _run(
        scan, driver, driver_options, outside, probe_only=False, runs=1, confirm=1
    )
    rows = []
    for result in screened:
        saved = result.percent_saved
        rows.append(
            {
                "site": Path(result.candidate.site).name,
                "header": Path(result.candidate.header).name,
                "reach": len(result.candidate.affected_tus) or 1,
                "status": result.status,
                "percent_saved": None if saved is None else round(saved, 3),
                "baseline_ms": None
                if result.baseline_seconds is None
                else round(result.baseline_seconds * 1000, 1),
                "patched_ms": None
                if result.patched_seconds is None
                else round(result.patched_seconds * 1000, 1),
            }
        )
    rows.sort(key=lambda r: -(r["percent_saved"] or -999))
    candidates_for_confirm = [
        r
        for r in screened
        if r.status in ("improved", "no-change")
        and (r.percent_saved or 0.0) >= args.screen_threshold
    ]
    print(
        f"  初筛 {len(screened)} 条（{screen_seconds:.0f}s），"
        f"单遍读数 ≥{args.screen_threshold:.1f}% 的 {len(candidates_for_confirm)} 条进入复测",
        flush=True,
    )

    confirmed = []
    if candidates_for_confirm:
        print("阶段 3：按正式协议复测（runs=3 + confirm=2）……")
        confirm_results, confirm_seconds = _run(
            scan,
            driver,
            driver_options,
            [r.candidate for r in candidates_for_confirm],
            probe_only=False,
            runs=3,
            confirm=2,
        )
        for result in confirm_results:
            confirmed.append(
                {
                    "site": Path(result.candidate.site).name,
                    "header": Path(result.candidate.header).name,
                    "status": result.status,
                    "percent_saved": None
                    if result.percent_saved is None
                    else round(result.percent_saved, 3),
                    "message": result.message[:200],
                }
            )
    else:
        confirm_seconds = 0.0

    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "compiler": args.compiler,
        "tus": len(scan.ok_tus),
        "noise_floor_percent": AGGREGATE_NOISE_FLOOR * 100,
        "pools": {"strict": len(strict), "off": len(off)},
        "probe": {
            "candidates": len(pool),
            "available": pool_total,
            "compiles": len(compiles),
            "seconds": round(probe_seconds, 1),
        },
        "outside_strict": {
            "count": len(outside),
            "available": total_outside,
            "screen_seconds": round(screen_seconds, 1),
            "rows": rows,
        },
        "confirm": {
            "count": len(candidates_for_confirm),
            "seconds": round(confirm_seconds, 1),
            "rows": confirmed,
        },
        "strict_compiles": len(inside),
    }
    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"g2-recall-off-{project.name}-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# 闸门漏掉了吗：闸门关时能编过的候选实测（{payload['date']}，{project.name}）",
        "",
        f"- 工程：{project.name}，{len(scan.ok_tus)} 个 TU。",
        f"- 候选池：闸门放行 {len(strict)} 条 / 闸门关 {len(off)} 条。",
        f"- 闸门关时能编过 {len(compiles)} 条，其中 **{len(outside)} 条不在闸门放行集合里**"
        f"（闸门放行集合里有 {len(inside)} 条能编过）。",
        f"- 初筛协议：单遍测量（`runs=1`），阈值 {args.screen_threshold:.1f}%；"
        f"过阈值者按正式协议复测（`runs=3` + 复测 2 遍）。",
        "",
        "## 闸门没放行、但能编过的候选（单遍初筛）",
        "",
        "| 改动位置 | include | 受影响 TU | 单遍读数 | 基线 ms | 移除后 ms | 结论 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        pct = "—" if row["percent_saved"] is None else f"{row['percent_saved']:.3f}%"
        lines.append(
            f"| `{row['site']}` | `{row['header']}` | {row['reach']} | {pct} "
            f"| {row['baseline_ms'] or '—'} | {row['patched_ms'] or '—'} | {row['status']} |"
        )
    lines += ["", "## 正式复测结果", ""]
    if confirmed:
        lines += ["| 改动位置 | include | 结论 | 读数 |", "|---|---|---|---:|"]
        for row in confirmed:
            pct = "—" if row["percent_saved"] is None else f"{row['percent_saved']:.3f}%"
            lines.append(
                f"| `{row['site']}` | `{row['header']}` | {row['status']} | {pct} |"
            )
    else:
        lines.append("没有候选的单遍读数达到阈值，因此没有进入复测。")
    lines += [
        "",
        "> 单遍读数**不能**作为结论引用：聚合 A/A 实测显示单遍噪声可达 2.005%。",
        "> 只有通过复测的条目才算 improved。",
        "",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
