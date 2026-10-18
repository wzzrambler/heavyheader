"""聚合口径的 A/A：把一整批 TU 的耗时加起来测两次，噪声有多大？

单 TU 的噪声线是 2%（``TIME_NOISE_FLOOR``，由 ``noise_min3`` 与 ``aa_noise``
实测标定）。但头文件级候选的收益要按**一批 TU 的总和**来算——删掉
``prelude.h`` 里一行 include，影响的是 24 个 TU 各 0.1–0.3%，单看任何一个
都在噪声里，加起来才有机会浮出来。

问题是：加起来的噪声不是 2%。每个 TU 各自 min-of-3，误差部分独立、部分
相关（同一台机器、同一份热缓存），所以聚合噪声必须**实测**，不能用单个
TU 的 2% 去推断——那是把两种口径混为一谈，也正是这个项目反复踩过的坑。

协议：同一批 TU、同一编译器、代码一行不动，连续测 P 对「每个 TU min-of-R
再求和」，报告每对之间的相对差。

用法::

    python experiments/g2_aggregate_aa.py --project experiments/_work/doctest \
        --db experiments/_work/doctest/compile_commands.json --tus 24
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import _measure  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def measure_total(driver, scan, runs: int, timeout: float) -> tuple[float | None, str | None]:
    """整批 TU 的聚合耗时：每个 TU 取 min-of-runs，再求和。"""
    total = 0.0
    for tu in scan.ok_tus:
        options = getattr(tu, "compile_options", None) or DriverOptions(
            standard="c++17", extra_flags=list(TARGET_FLAGS)
        )
        seconds, _memory, error, _discarded = _measure(
            driver, tu.source, options, None, runs, timeout
        )
        if seconds is None:
            return None, f"{tu.source.name}: {error}"
        total += seconds
    return total, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    db = Path(args.db).resolve()
    driver = detect_driver(CLANG)
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=CLANG,
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

    totals: list[float] = []
    for i in range(args.pairs + 1):
        total, err = measure_total(driver, scan, args.runs, args.timeout)
        if total is None:
            print(f"第 {i + 1} 次测量失败：{err}")
            return 1
        totals.append(total)
        print(f"第 {i + 1} 次聚合耗时：{total * 1000:.0f} ms（{len(scan.ok_tus)} TU）", flush=True)

    deltas = [
        round(abs(totals[i + 1] - totals[i]) / min(totals[i + 1], totals[i]) * 100, 3)
        for i in range(len(totals) - 1)
    ]
    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "tus": len(scan.ok_tus),
        "runs_per_unit": args.runs,
        "pairs": args.pairs,
        "totals_ms": [round(t * 1000, 1) for t in totals],
        "delta_percent": deltas,
        "max_delta_percent": max(deltas) if deltas else None,
        "mean_delta_percent": round(sum(deltas) / len(deltas), 3) if deltas else None,
    }
    out = ROOT / "experiments" / "results" / f"g2-aggregate-aa-{project.name}-{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
