"""G3 补测：门禁在**真实工程**上是否成立（此前只有构造样本）。

为什么必须补
------------
G3 的结论目前只建立在 `benchmarks/cases/demo` 上——2 个编译单元、7 个头文件。
评审完全可以说「你们只在一个玩具工程上验过门禁」。而门禁是我们唯一站得住的主卖点，
只靠构造样本支撑太薄。

本脚本在 nlohmann/json 的前 25 个编译单元上重复三件事：
1. **无回归**：树不变，门禁必须通过（误报 0）；
2. **注入回归**：新建一个头并让一个真实 TU 包含它，门禁必须失败；
3. **构建损坏**：让一个 TU 编译失败，门禁必须失败（而不是因为总量下降而放行）。

所有改动都在 `try/finally` 里还原：json 是 `experiments/_work/` 下的工作副本，
即使中途异常也要恢复，否则后续实验会踩到被污染的状态。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "experiments" / "_work" / "json"
DB = PROJECT / "compile_commands.json"
CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]
TUS = 25
REPEATS = 3
OUT = ROOT / "experiments" / "results"

#: 注入用 TU **不能写死**：必须从基线扫描的实际结果里取。
#:
#: 第一次写成 `tests/src/unit-algorithms.cpp`，结果三次注入全部 +0.0%——那个文件
#: 不在 `--max-tus 25` 扫到的集合里，改了等于没改。这种错误在本机看起来像「门禁
#: 漏报」，实际是实验装置指错了文件。所以下面从 baseline 的 translation_units 里取。


def cli(args: list[str], timeout: float = 900.0) -> tuple[int, str, float]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "heavyheader", *args],
        cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr), time.perf_counter() - started


def scan_args(out: Path) -> list[str]:
    return [
        "scan", str(PROJECT), "--compile-commands", str(DB),
        "--max-tus", str(TUS), "--jobs", "1", "--compiler", CLANG,
        *[f"--flag={flag}" for flag in TARGET],
        "--format", "json", "-o", str(out),
    ]


def gate_args(baseline: Path) -> list[str]:
    return [
        "gate", str(PROJECT), "--compile-commands", str(DB),
        "--max-tus", str(TUS), "--jobs", "1", "--compiler", CLANG,
        *[f"--flag={flag}" for flag in TARGET],
        "--baseline", str(baseline),
    ]


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="g3real-"))
    baseline = work / "baseline.json"

    code, out, scan_seconds = cli(scan_args(baseline))
    if code != 0:
        print("基线扫描失败：\n" + out[-1200:])
        return 1
    data = json.loads(baseline.read_text(encoding="utf-8"))
    before_lines = data["totals"]["attributed_lines"]
    tus = data["totals"]["translation_units"]
    print(f"基线：{tus} TU / {before_lines:,} 行 / 扫描 {scan_seconds:.1f}s")

    # -- 1. 无回归 ------------------------------------------------------
    clean = []
    for _ in range(REPEATS):
        code, out, seconds = cli(gate_args(baseline))
        clean.append(
            {
                "exit": code,
                "seconds": round(seconds, 2),
                "tail": [ln for ln in out.splitlines() if ln.strip()][-1:],
            }
        )

    # -- 2. 注入回归 ----------------------------------------------------
    scanned = [Path(tu["source"]) for tu in data.get("translation_units", []) if tu.get("error") is None]
    if not scanned:
        print("基线没有任何成功单元，无法注入")
        return 1
    victim_tu = scanned[0]
    print(f"注入目标：{victim_tu.relative_to(PROJECT)}")

    injected_header = PROJECT / "include" / "zz_gate_probe.h"
    original_src = victim_tu.read_text(encoding="utf-8")
    injected_lines = max(2000, int(before_lines * 0.10))
    regressed = []
    broken = []
    try:
        injected_header.write_text(
            "#pragma once\n"
            + "\n".join(
                f"inline int gate_probe_{i}() {{ return {i}; }}"
                for i in range(injected_lines)
            )
            + "\n",
            encoding="utf-8",
        )
        # BOM 必须留在首行之前，否则 clang 会报 unexpected character。
        bom = "\ufeff" if original_src.startswith("\ufeff") else ""
        victim_tu.write_text(
            bom + '#include "zz_gate_probe.h"\n' + original_src[len(bom):],
            encoding="utf-8",
        )
        for _ in range(REPEATS):
            code, out, seconds = cli(gate_args(baseline))
            regressed.append(
                {
                    "exit": code,
                    "seconds": round(seconds, 2),
                    "head": [ln for ln in out.splitlines() if ln.strip()][:2],
                }
            )

        # -- 3. 构建损坏 -------------------------------------------------
        victim_tu.write_text(original_src + "\nthis is not valid c++;\n", encoding="utf-8")
        for _ in range(REPEATS):
            code, out, seconds = cli(gate_args(baseline))
            broken.append(
                {
                    "exit": code,
                    "seconds": round(seconds, 2),
                    "head": [ln for ln in out.splitlines() if ln.strip()][:2],
                }
            )
    finally:
        # 无论上面发生什么，工作副本必须回到干净状态。
        victim_tu.write_text(original_src, encoding="utf-8")
        injected_header.unlink(missing_ok=True)

    payload = {
        "date": date.today().isoformat(),
        "project": "nlohmann/json (first 25 TUs)",
        "repeats": REPEATS,
        "baseline_attributed_lines": before_lines,
        "baseline_tus": tus,
        "scan_seconds": round(scan_seconds, 1),
        "injected_header_lines": injected_lines,
        "clean": clean,
        "clean_pass": sum(1 for r in clean if r["exit"] == 0),
        "regressed": regressed,
        "regression_detected": sum(1 for r in regressed if r["exit"] != 0),
        "broken": broken,
        "broken_detected": sum(1 for r in broken if r["exit"] != 0),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / f"g3-real-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"# G3 真实工程补测（{payload['date']}）",
        "",
        f"- 工程：{payload['project']}，基线 **{tus} 个 TU / {before_lines:,} 行**，"
        f"一次扫描 {payload['scan_seconds']}s",
        f"- 注入：新建 {injected_lines:,} 行的头并让一个真实测试 TU 包含它（约 +10%）",
        "",
        "| 场景 | 期望 | 结果 | 单次耗时 |",
        "|---|---|---|---:|",
        f"| 无回归（{REPEATS} 次） | 通过 | **{payload['clean_pass']}/{REPEATS}** | "
        f"{clean[0]['seconds']}s |",
        f"| 注入回归（{REPEATS} 次） | 失败 | **{payload['regression_detected']}/{REPEATS}** | "
        f"{regressed[0]['seconds'] if regressed else '—'}s |",
        f"| 构建损坏（{REPEATS} 次） | 失败 | **{payload['broken_detected']}/{REPEATS}** | "
        f"{broken[0]['seconds'] if broken else '—'}s |",
        "",
        "## 各次输出（前两行）",
        "",
        "### 注入回归",
        *[f"- exit={r['exit']}：{' / '.join(r['head'])}" for r in regressed],
        "",
        "### 构建损坏",
        *[f"- exit={r['exit']}：{' / '.join(r['head'])}" for r in broken],
        "",
        "## 判读边界",
        "",
        "- 注入的是「覆盖面膨胀」型回归；行数不变但编译变慢的回归仍测不出（已知盲区）。",
        "- 单次门禁耗时包含一次完整扫描，因此与同一工程的一次 `scan` 同量级。",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"clean {payload['clean_pass']}/{REPEATS} | "
          f"regression {payload['regression_detected']}/{REPEATS} | "
          f"broken {payload['broken_detected']}/{REPEATS}")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
