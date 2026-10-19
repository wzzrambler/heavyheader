"""计时漏斗：只测「已证实且预估收益过噪声线」的候选，量可采纳率的上界。

为什么需要它：计时闭环的采纳率 = improved / 送测候选数。旧协议按排序取前
K 条送测，池子里混了大量**计时层物理不可分辨**的候选（预估收益低于聚合噪声
线）——它们要么改善不了，要么测出来也过不了线，只稀释分母。本脚本把送测池
换成「解析层已证实（三真值全过）且预估收益 ≥ 噪声线」的候选，回答一个决策
相关的问题：**工具指着的大额候选，有多大比例真能换成墙上时间？**

协议与 g2_sites / corpus_timing 完全一致（交替 A/B、min-of-3、聚合噪声线
1%、improved 需复测），唯一的差别是送测池的选法。用法::

    python experiments/timing_funnel.py \\
        --precision-json experiments/results/candidate-precision-lua-20261019i.json \\
        --project <dir> --db <compile_commands.json> --compiler clang \\
        --out experiments/results/timing-funnel-lua-20261019.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import candidate_rank_key  # noqa: E402
from heavyheader.scanner import (  # noqa: E402
    ScanOptions,
    _split_command,
    _strip_output_flag,
    scan_project,
)
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402


def aggregate_baseline(db: Path, compiler: str) -> float:
    """串行编全部 TU 两次取 min——计时协议的聚合基线（与 g2 同口径）。"""
    entries = json.loads(db.read_text(encoding="utf-8"))
    cmds = [
        (_strip_output_flag(_split_command(e["command"])) + ["-fsyntax-only"],
         e["directory"])
        for e in entries
    ]
    best = float("inf")
    for _ in range(2):
        t0 = time.perf_counter()
        for cmd, cwd in cmds:
            subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=300)
        best = min(best, time.perf_counter() - t0)
    return best


def run(args) -> dict:
    precision = json.loads(args.precision_json.read_text(encoding="utf-8"))
    scan = scan_project(
        ScanOptions(
            root=args.project,
            compiler=args.compiler,
            compile_commands=args.db,
            max_tus=args.tus or 500,
            probe_compile=False,
            probe_jobs=args.jobs,
        )
    )
    aggregate = aggregate_baseline(args.db, args.compiler)
    print(f"全工程聚合基线 {aggregate:.2f}s（仅作背景；预筛用各候选自己单元的"
          f" {args.noise_fraction:.0%} 噪声线）")

    by_key = {(str(c.tu).lower(), c.header.name, c.line): c for c in scan.candidates}
    tu_total = {
        str(t.source).lower(): t.total_wall_seconds() for t in scan.ok_tus
    }
    pool = []
    skipped = {"sub-noise": 0, "not-proven": 0, "missing": 0, "no-estimate": 0}
    for row in precision["rows_by_rank"]:
        if not row.get("useful"):
            skipped["not-proven"] += 1
            continue
        key = (row["tu"].lower(), Path(row["header"]).name, row["line"])
        candidate = by_key.get(key)
        if candidate is None:
            skipped["missing"] += 1
            continue
        # 预估与噪声线同 scale：都按「受影响单元自己的编译时长」算。TU 级候选
        # 的 reach 是 1，噪声线 = 该单元总时长 × 噪声比例；拿全工程聚合当分母
        # 会把所有单 TU 候选全部误判为「不可分辨」。
        estimate = candidate.estimated_seconds_saved
        if estimate is None:
            skipped["no-estimate"] += 1
            continue
        noise = (tu_total.get(str(candidate.tu).lower()) or 0.0) * args.noise_fraction
        if estimate < noise:
            skipped["sub-noise"] += 1
            continue
        pool.append(
            replace(
                candidate,
                measured_seconds_saved=None,
                verified=False,
                probe_status="",
                advice=None,
            )
        )
    pool.sort(key=candidate_rank_key)
    print(f"已证实 {sum(1 for r in precision['rows_by_rank'] if r.get('useful'))} 条；"
          f"其中预估过噪声线并匹配到候选 {len(pool)} 条"
          f"（跳过：未证实 {skipped['not-proven']} / 噪声线以下 "
          f"{skipped['sub-noise']} / 未匹配 {skipped['missing']}）")
    if not pool:
        return {"pool": 0}

    driver = detect_driver(args.compiler)
    saved = scan.candidates
    scan.candidates = list(pool)
    try:
        fallback = DriverOptions(standard="c++17")
        results = verify_candidates(
            scan,
            driver,
            fallback,
            verify=VerifyOptions(
                top=len(pool), runs=args.runs, jobs=args.jobs, confirm_passes=2
            ),
        )
    finally:
        scan.candidates = saved

    improved = [r for r in results if r.status == "improved"]
    compiled = [
        r for r in results
        if r.status not in ("compile-error", "error", "not-found", "ambiguous")
    ]
    return {
        "project": str(args.project),
        "aggregate_seconds": round(aggregate, 3),
        "noise_fraction": args.noise_fraction,
        "pool": len(pool),
        "compiled": len(compiled),
        "improved": len(improved),
        "adopt_rate_on_pool": round(len(improved) / len(pool), 3) if pool else 0.0,
        "adopt_rate_on_compiled": (
            round(len(improved) / len(compiled), 3) if compiled else 0.0
        ),
        "details": [
            {
                "tu": Path(r.candidate.tu).name,
                "header": Path(r.candidate.header).name,
                "usage": r.candidate.usage_verdict,
                "estimate_s": round(r.candidate.estimated_seconds_saved or 0.0, 4),
                "status": r.status,
                "baseline_ms": (
                    None if r.baseline_seconds is None
                    else round(r.baseline_seconds * 1000, 1)
                ),
                "patched_ms": (
                    None if r.patched_seconds is None
                    else round(r.patched_seconds * 1000, 1)
                ),
                "percent_saved": (
                    None if r.percent_saved is None
                    else round(r.percent_saved, 3)
                ),
                "message": r.message[:200],
            }
            for r in results
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precision-json", type=Path, required=True)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--tus", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--noise-fraction", type=float, default=0.01)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if report.get("pool"):
        print(f"\n送测 {report['pool']} 条 → 编译通过 {report['compiled']} 条 → "
              f"improved {report['improved']} 条")
        print(f"池内采纳率 {report['adopt_rate_on_pool']:.0%}；"
              f"编译通过者中 {report['adopt_rate_on_compiled']:.0%}")
        for d in report["details"]:
            pct = "—" if d["percent_saved"] is None else f"{d['percent_saved']:+.2f}%"
            print(f"  {d['tu']:22} -> {d['header']:14} {d['status']:14} {pct}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
