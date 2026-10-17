"""A/A 对照：同输入、无改动，重复扫描 N 次，量出噪声底。

为什么必须先做这一步：后面所有「比竞品更快 / 更准」的说法，都要先知道
**自己重复自己**能差多少。结构指标（归因行数）应当是 100% 确定的；
计时指标一定会有抖动，抖动幅度就是判定「优化是否有效」的下限。
没有这个数，任何 5% 的收益都可能是噪声。

用法：``python experiments/aa_noise.py [--runs 3] [--tus 25]``
输出：``experiments/results/aa-<日期>.{md,json}``
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / "_work" / "compile_commands.json"
PROJECT = ROOT / "experiments" / "_work" / "json"


def _run_once(index: int, tus: int) -> tuple[float, dict]:
    out = ROOT / "experiments" / "_work" / f"aa-{os.getpid()}-{index}.json"
    cmd = [
        sys.executable,
        "-m",
        "heavyheader",
        "scan",
        str(PROJECT),
        "--compile-commands",
        str(DB),
        "--max-tus",
        str(tus),
        "--jobs",
        "1",
        "--format",
        "json",
        "-o",
        str(out),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    start = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        raise SystemExit(f"scan failed ({proc.returncode}): {proc.stderr[-800:]}")
    data = json.loads(out.read_text(encoding="utf-8"))
    out.unlink(missing_ok=True)
    return elapsed, data


def _top_headers(data: dict, n: int = 10) -> list[str]:
    rows = sorted(
        (h for h in data.get("headers", []) if h.get("kind") == "project"),
        key=lambda h: h.get("attributed_lines", 0),
        reverse=True,
    )
    return [Path(h["path"]).name for h in rows[:n]]


def _spread(values: list[float]) -> float:
    med = statistics.median(values)
    return (max(values) - min(values)) / med * 100 if med else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--tus", type=int, default=25)
    args = ap.parse_args()

    runs: list[tuple[float, dict]] = []
    for i in range(args.runs):
        print(f"run {i + 1}/{args.runs} ...", flush=True)
        runs.append(_run_once(i, args.tus))

    scans = [r[0] for r in runs]
    compiled = [float(r[1]["baseline_seconds"]) for r in runs]
    attributed = [int(r[1]["totals"]["attributed_lines"]) for r in runs]
    tus_counts = [int(r[1]["totals"]["translation_units"]) for r in runs]
    tops = [_top_headers(r[1]) for r in runs]

    # 逐 TU 的墙钟抖动：取每个源文件在多次运行间的极差，再取中位数。
    per_tu: dict[str, list[float]] = {}
    for _, data in runs:
        for tu in data.get("translation_units", []):
            per_tu.setdefault(Path(tu["source"]).name, []).append(
                float(tu.get("wall_seconds", 0.0))
            )
    tu_spreads = [
        _spread(v) for v in per_tu.values() if len(v) == len(runs) and statistics.median(v) > 0
    ]
    tu_spread_median = statistics.median(tu_spreads) if tu_spreads else 0.0

    identical_attr = len(set(attributed)) == 1
    identical_top = all(t == tops[0] for t in tops)
    no_timing = sum(1 for v in per_tu.values() if statistics.median(v) == 0)

    payload = {
        "runs": args.runs,
        "tus_per_run": tus_counts,
        "attributed_lines": attributed,
        "attributed_identical": identical_attr,
        "top10_identical": identical_top,
        "top10": tops[0] if tops else [],
        "scan_wall_seconds": [round(s, 4) for s in scans],
        "scan_wall_median": round(statistics.median(scans), 4),
        "scan_wall_spread_percent": round(_spread(scans), 2),
        "compiled_wall_seconds": [round(c, 4) for c in compiled],
        "compiled_wall_median": round(statistics.median(compiled), 4),
        "compiled_wall_spread_percent": round(_spread(compiled), 2),
        "per_tu_wall_spread_median_percent": round(tu_spread_median, 2),
        "tus_without_timing": no_timing,
        "scan_overhead_ratio": round(
            statistics.median(scans) / statistics.median(compiled), 3
        )
        if statistics.median(compiled)
        else None,
    }

    lines = [
        f"# A/A 对照：噪声底（{date.today().isoformat()}）",
        "",
        f"- 工程：nlohmann/json，编译数据库取前 {args.tus} 个 TU，`--jobs 1`",
        f"- 运行次数：{args.runs}（同一输入、不改任何文件）",
        "",
        "## 一、结构指标（应当完全确定）",
        "",
        "| 指标 | 结果 |",
        "|---|---|",
        f"| 归因行数 | {attributed[0]:,} × {args.runs}，完全一致：**{identical_attr}** |",
        f"| Top-10 项目头 | 完全一致：**{identical_top}** |",
        f"| 无计时的 TU | {no_timing} |",
        "",
        "## 二、计时指标（噪声底）",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 编译墙钟中位数 | {payload['compiled_wall_median']:.2f} s |",
        f"| 编译墙钟极差 | {payload['compiled_wall_spread_percent']:.1f}% |",
        f"| 扫描墙钟中位数 | {payload['scan_wall_median']:.2f} s |",
        f"| 扫描墙钟极差 | {payload['scan_wall_spread_percent']:.1f}% |",
        f"| 单 TU 墙钟极差（中位） | {payload['per_tu_wall_spread_median_percent']:.1f}% |",
        f"| 扫描 / 编译 | {payload['scan_overhead_ratio']}× |",
        "",
        "## 三、这组数字意味着什么",
        "",
        f"1. 结构指标 {args.runs}/{args.runs} 完全一致——归因行数可以当确定性指标用于门禁。",
        f"2. 单 TU 计时抖动中位数 {payload['per_tu_wall_spread_median_percent']:.1f}%，"
        f"因此任何低于该幅度的「省时」都不能算收益。",
        "3. 这是本机（Windows + MinGW，无其他负载）的噪声；换机器必须重测。",
        "",
        "## 口径与限制",
        "",
        "- `--jobs 1` 是为降低并行调度噪声；并行下的等待时间另测。",
        "- 编译墙钟取驱动实测值（含进程启动），与 `-ftime-report` 的 CPU 时间不同口径。",
        "- 未做 CPU 亲和性绑定；后台进程未完全隔离，极差已包含这部分干扰。",
        "",
    ]

    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"aa-{date.today():%Y%m%d}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    js = md.with_suffix(".json")
    js.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written: {md}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
