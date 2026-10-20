"""重复测量探针：把「池内采纳率 25%」拆成可核验的逐条证据。

为什么需要它：单个比例会同时掩盖三件事——

1. **单条收益**：5 条 improved 各自省了多少，是不是都贴着阈值；
2. **重复测量的离散度**：min-of-3 只留一个最小值，看不出这条候选的收益是
   稳定的还是抽签抽出来的；
3. **复测是否复现**：2–3% 的收益离 2% 噪声线不远，单遍判定在阈值附近等于
   抽签（``VerifyOptions.confirm_passes`` 的注释里记着：有一轮 improved 复测
   0/4 复现）。

本脚本对同一批候选两侧各编译 ``runs`` 次，**保留每一次的读数**（不做 min），
再对首轮达线的候选独立复测一遍，全部落盘。

选池规则复用 ``timing_funnel.select_pool``——两处必须同一个分母，否则
「25%」和它的逐条证据就不是同一件事。用法::

    python experiments/repeat_probe.py \\
        --precision-json experiments/results/candidate-precision-lua-20261020k.json \\
        --project %TEMP%/upstream/lua-5.4.7 \\
        --db %TEMP%/upstream/lua-5.4.7/compile_commands.json \\
        --compiler clang --tus 34 --runs 5 \\
        --out experiments/results/repeat-probe-lua-20261020.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from timing_funnel import select_pool  # noqa: E402

from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import tu_compile_seconds  # noqa: E402
from heavyheader.scanner import ScanOptions, default_include_dirs, scan_project  # noqa: E402
from heavyheader.verify import (  # noqa: E402
    _comment_out,
    _patched_options,
    _unit_options,
)

#: 与 src/heavyheader/attrib/advice.py 的 TIME_NOISE_FLOOR 一致：improved 的
#: 判定线是 2%，不是 1%（1% 是聚合口径的噪声线）。
IMPROVED_FLOOR = 0.02


def _samples(driver, path: Path, options, runs: int, timeout: float):
    """编译 ``runs`` 次，返回每一次的墙钟（毫秒），不做 min。

    先跑一次**不记录**的预热：冷启动那一次比后续慢得多，混进样本会同时抬高
    min 和离散度。verify 的协议里基线侧有缓存预热（``_warm``），打补丁那侧没有，
    这里两侧都预热——是本脚本与 verify 唯一的一处刻意差异，方向是让收益不被
    冷启动吃掉。
    """
    out: list[float] = []
    for index in range(max(1, runs) + 1):
        tu = driver.run(path, options, cwd=None, timeout=timeout)
        if not tu.ok:
            return None, tu.error or "compile failed"
        if index == 0:
            continue
        out.append(round(tu_compile_seconds(tu) * 1000, 1))
    return out, None


def _round(samples_b: list[float], samples_p: list[float]) -> dict:
    """一侧一轮的结果：min-of-N 判收益，同时保留离散度。"""
    base = min(samples_b)
    patched = min(samples_p)
    saved = base - patched
    return {
        "baseline_samples_ms": samples_b,
        "patched_samples_ms": samples_p,
        "baseline_min_ms": base,
        "patched_min_ms": patched,
        "saved_ms": round(saved, 1),
        "percent_saved": round(saved / base * 100.0, 2) if base else 0.0,
        "baseline_spread_ms": round(max(samples_b) - min(samples_b), 1),
        "patched_spread_ms": round(max(samples_p) - min(samples_p), 1),
    }


def run(args) -> dict:
    precision = json.loads(args.precision_json.read_text(encoding="utf-8"))
    scan = scan_project(
        ScanOptions(
            root=args.project,
            compiler=args.compiler,
            compile_commands=args.db,
            max_tus=args.tus or 500,
            probe_compile=False,
            probe_jobs=1,
        )
    )
    selected = select_pool(scan, precision["rows_by_rank"], args.noise_fraction)
    pool = selected["pool"]
    if not pool:
        return {"pool": 0, "skipped": selected["skipped"]}

    driver = detect_driver(args.compiler)
    fallback = DriverOptions(standard="c++17")
    effective_includes = list(fallback.include_dirs) or default_include_dirs(scan.root)

    # 基线按单元缓存：多个候选共用同一个 TU 时只编一次（与 verify 同款）。
    baseline_cache: dict[Path, list[float]] = {}
    rows: list[dict] = []
    for candidate in pool:
        source = Path(candidate.tu)
        unit_options = _unit_options(scan, candidate, fallback, effective_includes)
        if source not in baseline_cache:
            samples, error = _samples(
                driver, source, unit_options, args.runs, args.timeout
            )
            if samples is None:
                rows.append(
                    {
                        "tu": source.name,
                        "header": Path(candidate.header).name,
                        "line": candidate.line,
                        "status": "error",
                        "message": f"基线编译失败：{error}",
                    }
                )
                continue
            baseline_cache[source] = samples
        text = source.read_text(encoding="utf-8", errors="replace")
        with tempfile.TemporaryDirectory(prefix="heavyheader-repeat-") as tmp:
            patched_path = Path(tmp) / source.name
            patched_path.write_text(
                _comment_out(text, candidate.line), encoding="utf-8"
            )
            patched_options = _patched_options(
                unit_options, effective_includes, source
            )
            p_samples, error = _samples(
                driver, patched_path, patched_options, args.runs, args.timeout
            )
        if p_samples is None:
            rows.append(
                {
                    "tu": source.name,
                    "header": Path(candidate.header).name,
                    "line": candidate.line,
                    "status": "compile-error",
                    "message": error or "",
                }
            )
            continue
        first = _round(baseline_cache[source], p_samples)
        row = {
            "tu": source.name,
            "header": Path(candidate.header).name,
            "line": candidate.line,
            "usage": candidate.usage_verdict,
            "estimate_s": round(candidate.estimated_seconds_saved or 0.0, 5),
            "status": "no-change",
            **first,
        }
        if first["percent_saved"] >= IMPROVED_FLOOR * 100:
            row["status"] = "improved"
            # 复测：独立再测一遍（两侧都重新采样），不复用首轮读数。
            p2, error2 = None, None
            with tempfile.TemporaryDirectory(prefix="heavyheader-repeat-") as tmp:
                patched_path = Path(tmp) / source.name
                patched_path.write_text(
                    _comment_out(text, candidate.line), encoding="utf-8"
                )
                p2, error2 = _samples(
                    driver, patched_path, patched_options, args.runs, args.timeout
                )
            if p2 is None:
                row["retest"] = {"error": error2 or ""}
                row["status"] = "no-change"
            else:
                second = _round(baseline_cache[source], p2)
                row["retest"] = second
                if second["percent_saved"] < IMPROVED_FLOOR * 100:
                    row["status"] = "no-change"
                    row["retest"]["demoted"] = True
        rows.append(row)
        pct = f"{row.get('percent_saved', 0):+.2f}%"
        print(f"  {row['tu']:22} -> {row['header']:14} {row['status']:13} {pct}")

    confirmed = [r for r in rows if r.get("status") == "improved"]
    first_pass = [
        r for r in rows if (r.get("percent_saved") or 0) >= IMPROVED_FLOOR * 100
    ]
    measured = [r for r in rows if "percent_saved" in r]
    spreads = [max(r["baseline_spread_ms"], r["patched_spread_ms"]) for r in measured]
    excluded = selected["excluded"]
    return {
        "project": str(args.project),
        "runs_per_side": args.runs,
        "improved_floor_percent": IMPROVED_FLOOR * 100,
        "pool": len(pool),
        "first_pass_improved": len(first_pass),
        "confirmed_improved": len(confirmed),
        "adopt_rate_on_pool": round(len(confirmed) / len(pool), 3),
        "retest_survival": (
            round(len(confirmed) / len(first_pass), 3) if first_pass else 0.0
        ),
        "spread_ms": {
            "median": round(statistics.median(spreads), 1),
            "max": round(max(spreads), 1),
            "min": round(min(spreads), 1),
        },
        "excluded_sub_noise": len(excluded),
        "excluded_detail": excluded,
        "details": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precision-json", type=Path, required=True)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--tus", type=int, default=0)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--noise-fraction", type=float, default=0.01)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if report.get("pool"):
        print(
            f"\n送测 {report['pool']} 条 → 首轮达线 {report['first_pass_improved']} 条 → "
            f"复测后保留 {report['confirmed_improved']} 条"
        )
        print(f"池内采纳率 {report['adopt_rate_on_pool']:.0%}；"
              f"复测存活率 {report['retest_survival']:.0%}")
        print(f"单侧重复测量离散度（max-min）中位数 "
              f"{report['spread_ms']['median']} ms，最大 {report['spread_ms']['max']} ms")
        print(f"未送测（已证实但预估低于噪声线）{report['excluded_sub_noise']} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
