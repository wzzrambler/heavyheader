"""CBA 榜单自身的可复现性：同一输入跑两遍，看 Top-10 是否一致。

为什么必须测这一项
------------------
G1 要比较「两个工具的 Top-10 是否指向同一批头文件」。但如果**其中一方自己的
榜单在两次运行间就会变**，那么任何重合度/顺序一致性数字都掺进了对方的抖动，
把它当成「两个工具的差异」是错的。

HeavyHeader 侧的确定性已由 A/A 对照单独验证（归因行数与 Top-10 三次完全一致，
见 `experiments/aa_noise.py`）。本脚本只回答另一半：CBA 侧稳不稳。

结论只有两种用法：
- 若 CBA 的集合稳定、只有并列项顺序漂移 → 集合重合度可信，顺序类指标
  （Kendall τ）在接近并列的位置不可信，报告里必须这么写。
- 若集合本身也变 → 连重合度也要打折扣。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from cba_compare import (
    CBA,
    DB,
    PROJECT,
    ROOT,
    cba_args,
    parse_cba_headers,
    trace_self_times,
    under_project,
)

N = 25
RUNS = 2


def one_run(tag: str) -> dict:
    """跑一次独立采集，返回发布口径与自身耗时口径的 Top-10。"""
    work = ROOT / "experiments" / "_work" / f"cba-stability-{tag}"
    obj = work / "obj"
    obj.mkdir(parents=True, exist_ok=True)
    for stale in obj.iterdir():
        if stale.is_file():
            stale.unlink()

    import subprocess

    subprocess.run([str(CBA), "--start", str(obj)], check=True, capture_output=True)
    for i, entry in enumerate(json.loads(DB.read_text(encoding="utf-8"))[:N]):
        subprocess.run(
            cba_args(entry["command"], obj / f"u{i}.o"),
            cwd=entry["directory"],
            capture_output=True,
            text=True,
        )
    capture = work / "capture.bin"
    subprocess.run(
        [str(CBA), "--stop", str(obj), str(capture)], check=True, capture_output=True
    )
    (work / "ClangBuildAnalyzer.ini").write_text(
        "[counts]\nheader=500\n\n[misc]\nonlyRootHeaders=false\n", encoding="utf-8"
    )
    analysis = subprocess.run(
        [str(CBA), "--analyze", str(capture)],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(work),
    ).stdout
    (work / "analysis.txt").write_text(analysis, encoding="utf-8")

    published = [
        Path(h["path"]).name
        for h in parse_cba_headers(analysis)
        if under_project(h["path"], PROJECT)
    ][:10]
    self_rows = [
        {"name": n, "ms": round(v["self_us"] / 1000, 1)}
        for n, v in trace_self_times(obj).items()
        if under_project(v["path"], PROJECT) and v["self_us"] > 0
    ]
    self_rows.sort(key=lambda r: (-r["ms"], r["name"]))
    return {
        "published": published,
        "self": [r["name"] for r in self_rows[:10]],
        "self_ms": {r["name"]: r["ms"] for r in self_rows[:20]},
    }


def main() -> int:
    runs = [one_run(f"r{i + 1}") for i in range(RUNS)]

    published_sets = {tuple(r["published"]) for r in runs}
    self_sets = {tuple(r["self"]) for r in runs}
    same_published_set = len({frozenset(r["published"]) for r in runs}) == 1
    same_self_set = len({frozenset(r["self"]) for r in runs}) == 1

    # 同一头文件在两次运行间的耗时差异：时间型指标的物理抖动有多大。
    deltas = []
    for name, ms in runs[0]["self_ms"].items():
        other = runs[1]["self_ms"].get(name)
        if other:
            deltas.append((name, ms, other, abs(other - ms) / ms * 100 if ms else 0.0))
    deltas.sort(key=lambda d: -d[3])

    payload = {
        "date": date.today().isoformat(),
        "tus": N,
        "runs": RUNS,
        "published_top10": [r["published"] for r in runs],
        "self_top10": [r["self"] for r in runs],
        "published_set_stable": same_published_set,
        "published_order_stable": len(published_sets) == 1,
        "self_set_stable": same_self_set,
        "self_order_stable": len(self_sets) == 1,
        "per_header_delta_percent": [
            {"name": n, "run1_ms": a, "run2_ms": b, "delta_percent": round(d, 1)}
            for n, a, b, d in deltas[:10]
        ],
    }
    out = ROOT / "experiments" / "results" / f"cba-stability-{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"published 集合稳定: {same_published_set} | 顺序稳定: {len(published_sets) == 1}")
    print(f"self-time 集合稳定: {same_self_set} | 顺序稳定: {len(self_sets) == 1}")
    print("run1 published:", runs[0]["published"])
    print("run2 published:", runs[1]["published"])
    print("最大单头文件抖动:", f"{deltas[0][3]:.1f}% ({deltas[0][0]})" if deltas else "无")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
