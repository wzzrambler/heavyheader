"""语料计时选型：对筛选出的工程跑 TU 层计时闭环（usage 分层排序）。

与 g2_sites.py 的差别：ScanOptions 来自 benchmarks/run_all.py 的用例配置
（-I / -D / 排除项 / 标准），头文件级与 TU 层臂共用同一套候选与协议
（交替 min-of-3、聚合噪声线、improved 需复测）。输出与 g2_sites 同构。
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from run_all import CASES, clone  # noqa: E402

from heavyheader.attrib.advice import AGGREGATE_NOISE_FLOOR  # noqa: E402
from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import candidate_rank_key  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402

CLANGXX = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def run_arm(scan, driver, project, candidates, confirm: int, runs: int, jobs: int):
    saved = scan.candidates
    scan.candidates = list(candidates)
    try:
        fallback = DriverOptions(
            standard="c++17",
            include_dirs=[project / inc for inc in _includes(scan)],
            extra_flags=list(_extra(scan)),
        )
        return verify_candidates(
            scan,
            driver,
            fallback,
            verify=VerifyOptions(
                top=len(candidates), runs=runs, jobs=jobs, confirm_passes=confirm
            ),
        )
    finally:
        scan.candidates = saved


def _includes(scan):
    return [str(p) for p in scan.options.include_dirs] if getattr(scan, "options", None) else []


def _extra(scan):
    opts = getattr(scan, "options", None)
    if opts and opts.extra_flags:
        return opts.extra_flags
    return list(TARGET_FLAGS)


def summarize(results, label: str) -> dict:
    compiled = [
        r
        for r in results
        if r.status not in ("compile-error", "error", "not-found", "ambiguous")
    ]
    improved = [r for r in results if r.status == "improved"]
    return {
        "arm": label,
        "candidates": len(results),
        "compiled": len(compiled),
        "improved": len(improved),
        "adopt_rate": round(len(improved) / len(results), 3) if results else 0.0,
        "details": [
            {
                "site": Path(r.candidate.site).name,
                "header": Path(r.candidate.header).name,
                "reach": r.candidate.reach,
                "verdict": r.candidate.usage_verdict,
                "status": r.status,
                "baseline_ms": None if r.baseline_seconds is None else round(r.baseline_seconds * 1000, 1),
                "patched_ms": None if r.patched_seconds is None else round(r.patched_seconds * 1000, 1),
                "percent_saved": None if r.percent_saved is None else round(r.percent_saved, 3),
                "message": r.message[:200],
            }
            for r in results
        ],
    }


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="benchmarks/run_all.py 里的用例名")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--arm", default="tu", choices=["tu", "sites", "both"])
    args = ap.parse_args()

    case = next(c for c in CASES if c.name == args.case)
    project = clone(case)
    driver = detect_driver(CLANGXX)
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=CLANGXX,
            include_dirs=[project / inc for inc in case.includes],
            exclude_dirs=set(case.excludes),
            defines=list(case.defines),
            max_tus=case.max_tus,
            extra_flags=[f"-std={case.std}", *TARGET_FLAGS],
            jobs=1,
        ),
        driver=driver,
    )
    if not scan.ok_tus:
        print("扫描没有成功单元")
        return 1
    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(project, {h for tu in scan.ok_tus for h in tu.dependencies})

    from heavyheader.attrib.sites import find_header_candidates

    gated_sites = find_header_candidates(scan, sizes, kinds)
    tu_pool = sorted(scan.candidates, key=candidate_rank_key)
    print(
        f"TU: {len(scan.ok_tus)}  TU 候选 {len(tu_pool)}（疑似未使用 "
        f"{sum(1 for c in tu_pool if c.usage_verdict == 'plausibly_unused')}）"
        f"  site 闸门开 {len(gated_sites)}"
    )

    arms = []
    wanted = ["tu", "sites"] if args.arm == "both" else [args.arm]
    if "tu" in wanted and tu_pool:
        pool = [replace(c, measured_seconds_saved=None, verified=False, probe_status="", advice=None) for c in tu_pool[: args.top]]
        arms.append(summarize(run_arm(scan, driver, project, pool, 2, args.runs, args.jobs), "tu(usage-ranked)"))
    if "sites" in wanted and gated_sites:
        pool = [replace(c, measured_seconds_saved=None, verified=False, probe_status="", advice=None) for c in sorted(gated_sites, key=candidate_rank_key)[: args.top]]
        arms.append(summarize(run_arm(scan, driver, project, pool, 2, args.runs, args.jobs), "sites(header,gated)"))

    payload = {
        "date": date.today().isoformat(),
        "project": case.name,
        "tus": len(scan.ok_tus),
        "top": args.top,
        "runs_per_side": args.runs,
        "aggregate_noise_floor_percent": AGGREGATE_NOISE_FLOOR * 100,
        "arms": arms,
    }
    out_dir = ROOT / "experiments" / "results"
    suffix = f"-{args.case}-{args.arm}{args.top}-{date.today():%Y%m%d}"
    (out_dir / f"corpus-timing{suffix}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    for arm in arms:
        print(
            f"{arm['arm']}: candidates={arm['candidates']} compiled={arm['compiled']} "
            f"improved={arm['improved']} adopt={arm['adopt_rate']:.0%}"
        )
        for d in arm["details"]:
            pct = "-" if d["percent_saved"] is None else f"{d['percent_saved']:+.3f}%"
            print(f"  {d['site'][:36]:36s} -> {d['header'][:20]:20s} {d['status']:12s} {pct}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
