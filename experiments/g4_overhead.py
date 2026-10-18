"""G4：工程优势——同一个任务下，我们的采集开销、内存与确定性是否可接受。

G4 与 G1/G2/G3 的分工
--------------------
前三道门槛问「有没有用」，G4 问「付得起吗」。一个把构建时间翻倍的分析器，
即使结论正确也会被团队关掉。三条指标：

1. **时间开销**：一次扫描的墙钟 ÷ 同一批单元**纯编译**的墙钟。分母来自扫描
   自己记录的每单元墙钟（`baseline_seconds`），不是估算。
2. **峰值内存**：扫描进程的峰值工作集。要在 CI 常驻，这个数必须小。
3. **确定性**：同输入三次，归因行数与 Top-10 必须完全一致。不确定的排行榜
   不能用来对比基线，也没法和竞品比排序。

两种编译器各跑一轮（GCC / Clang）。MSVC 因 `vcvars` 依赖被策略拦截的 `reg.exe`
无法运行，如实记为未测——不是「应该也差不多」。

说明：本脚本用 `psutil` 采样峰值内存，它是**实验工具**，不是产品依赖；
HeavyHeader 本身仍然零第三方依赖。
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "experiments" / "_work" / "json"
DB = ROOT / "experiments" / "_work" / "compile_commands.json"
OUT = ROOT / "experiments" / "results"
TUS = 25
REPEATS = 3

LANES = [
    {
        "name": "gcc",
        "compiler": "g++",
        "flags": [],
    },
    {
        "name": "clang",
        "compiler": "C:/Program Files/LLVM/bin/clang++.exe",
        "flags": ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"],
    },
]


def scan_once(lane: dict, out_path: Path) -> dict:
    """跑一次 scan，返回墙钟、峰值 RSS 与结果 JSON。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    argv = [
        sys.executable, "-m", "heavyheader", "scan", str(PROJECT),
        "--compile-commands", str(DB), "--max-tus", str(TUS),
        "--jobs", "1", "--compiler", lane["compiler"],
        *[f"--flag={flag}" for flag in lane["flags"]],
        "--format", "json", "-o", str(out_path),
    ]
    started = time.perf_counter()
    proc = subprocess.Popen(
        argv, cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    # 轮询峰值工作集：Windows 上 rss 就是工作集，采样间隔 50 ms 足够抓住
    # 峰值——扫描的峰值出现在并发读头文件的阶段，不会是一瞬间。
    peak = 0
    handle = psutil.Process(proc.pid)
    while proc.poll() is None:
        try:
            peak = max(peak, handle.memory_info().rss)
        except psutil.Error:
            break
        time.sleep(0.05)
    stderr = proc.stderr.read() if proc.stderr else ""
    wall = time.perf_counter() - started
    if proc.returncode != 0:
        return {"error": stderr[-400:], "wall": wall, "peak_rss": peak}
    data = json.loads(out_path.read_text(encoding="utf-8"))
    return {
        "wall": wall,
        "peak_rss": peak,
        "data": data,
    }


def top10(data: dict) -> list[str]:
    rows = [h for h in data.get("headers", []) if h.get("kind") == "project"]
    rows.sort(key=lambda h: h["attributed_lines"], reverse=True)
    return [Path(h["path"]).name for h in rows[:10]]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    lanes = []
    for lane in LANES:
        runs = []
        for i in range(REPEATS):
            out_path = OUT / f"_g4-{lane['name']}-{i}.json"
            runs.append(scan_once(lane, out_path))
            out_path.unlink(missing_ok=True)
        ok = [r for r in runs if "data" in r]
        if not ok:
            lanes.append({"lane": lane["name"], "error": runs[0].get("error", "unknown")})
            continue

        walls = [r["wall"] for r in ok]
        peaks = [r["peak_rss"] for r in ok]
        compiles = [r["data"]["baseline_seconds"] for r in ok]
        lines = {r["data"]["totals"]["attributed_lines"] for r in ok}
        tops = {tuple(top10(r["data"])) for r in ok}
        failures = sum(r["data"]["totals"].get("failed_translation_units", 0) for r in ok)

        lanes.append(
            {
                "lane": lane["name"],
                "compiler": lane["compiler"],
                "runs": len(ok),
                "failed_units": failures,
                "wall_median_s": round(statistics.median(walls), 2),
                "compile_median_s": round(statistics.median(compiles), 2),
                # 开销倍数：扫描总时间 / 纯编译时间。1.0 表示分析几乎免费，
                # 2.0 表示用户为分析多付了一倍等待。
                "overhead_ratio": round(
                    statistics.median(walls) / statistics.median(compiles), 2
                )
                if statistics.median(compiles)
                else None,
                "peak_rss_mb": round(max(peaks) / (1024 * 1024), 1),
                # 分析本身的固定成本 ≈ 总时间 − 编译时间。
                "analysis_only_s": round(
                    statistics.median(walls) - statistics.median(compiles), 2
                ),
                "attributed_lines_stable": len(lines) == 1,
                "top10_stable": len(tops) == 1,
                "attributed_lines": sorted(lines)[0] if len(lines) == 1 else sorted(lines),
                "top10": list(next(iter(tops))),
            }
        )

    payload = {
        "date": date.today().isoformat(),
        "project": "nlohmann/json (first 25 TUs)",
        "tus": TUS,
        "repeats": REPEATS,
        "jobs": 1,
        "lanes": lanes,
        "msvc": "未测：vcvars64 定位 Windows SDK 依赖 reg.exe，被本机安全策略拦截",
    }
    stem = OUT / f"g4-overhead-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    usable = [lane for lane in lanes if "error" not in lane]
    lines_md = [
        f"# G4 采集开销、内存与确定性（{payload['date']}）",
        "",
        f"- 工程：{payload['project']}，`--jobs 1`，重复 {REPEATS} 次取中位/最大值。",
        "- 「纯编译时间」来自扫描自己记录的每单元墙钟（`baseline_seconds`），"
        "不是估算；开销倍数 = 扫描墙钟 ÷ 纯编译墙钟。",
        "",
        "| 编译器 | 扫描墙钟 | 纯编译 | 开销倍数 | 分析独占 | 峰值内存 | 失败单元 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {lane['lane']} | {lane['wall_median_s']}s | {lane['compile_median_s']}s | "
            f"**{lane['overhead_ratio']}×** | {lane['analysis_only_s']}s | "
            f"{lane['peak_rss_mb']} MB | {lane['failed_units']} |"
            for lane in usable
        ],
        "",
        "## 确定性（同输入三次）",
        "",
        "| 编译器 | 归因行数一致 | Top-10 一致 | 归因行数 |",
        "|---|---|---|---:|",
        *[
            f"| {lane['lane']} | {'是' if lane['attributed_lines_stable'] else '**否**'} | "
            f"{'是' if lane['top10_stable'] else '**否**'} | {lane['attributed_lines']} |"
            for lane in usable
        ],
        "",
        "## 未测",
        "",
        f"- MSVC：{payload['msvc']}",
        "",
        "## 判读边界",
        "",
        "- 开销倍数含进程启动与 Python 解释器启动（每次约 0.1–0.2 s），"
        "在几十个单元的小工程上这部分占比不可忽略。",
        "- 峰值内存是整个扫描进程的工作集，包含编译器子进程吗？**不包含**——"
        "采样的是主进程；编译器子进程的内存不在这个数里。",
        "- 结果只对这台机器、这个工程、25 个单元成立，不能外推成「CI 上也是这个数」。",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines_md) + "\n", encoding="utf-8")

    for lane in usable:
        print(
            f"{lane['lane']:6s} wall={lane['wall_median_s']}s compile={lane['compile_median_s']}s "
            f"overhead={lane['overhead_ratio']}x analysis={lane['analysis_only_s']}s "
            f"rss={lane['peak_rss_mb']}MB stable={lane['attributed_lines_stable']}/{lane['top10_stable']}"
        )
    for lane in lanes:
        if "error" in lane:
            print(f"{lane['lane']}: ERROR {lane['error'][:200]}")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
