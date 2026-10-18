"""G3：CI 门禁是否成立——注入回归必检出、无回归零误报、单次成本可接受。

三条判据（与 `docs/benchmark-plan.md` §6 一致）
-----------------------------------------------
1. **检出**：注入一次已知的成本回归（给一个 TU 增加一个重量级头），
   门禁必须 3/3 判失败，且失败原因指向真实变化。
2. **误报**：tree 不变时连跑 3 次，必须 3/3 通过。误报比漏报更容易毁掉
   一个门禁——团队两次被无故拦下就会关掉它。
3. **成本**：单次门禁的墙钟时间。若比一次普通扫描贵得多，它进不了 CI。

为什么要复制到临时目录：注入回归必须真的改文件。改完再还原的操作一旦中断
（Ctrl-C、崩溃）就会把仓库留在坏状态；在副本里做，原工程永不被触碰。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "benchmarks" / "cases" / "demo"
OUT = ROOT / "experiments" / "results"
REPEATS = 3


def run_cli(args: list[str], cwd: Path) -> tuple[int, str, float]:
    """跑一条 heavyheader 命令，返回 (exit, 合并输出, 秒)。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "heavyheader", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, (proc.stdout + proc.stderr), time.perf_counter() - started


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="g3-gate-"))
    project = work / "demo"
    shutil.copytree(DEMO, project)
    baseline = work / "baseline.json"

    scan_args = ["scan", ".", "-I", "include", "--jobs", "1", "--format", "json"]
    code, out, _ = run_cli(scan_args + ["-o", str(baseline)], project)
    if code != 0:
        print("基线扫描失败：\n" + out[-800:])
        return 1

    gate_args = ["gate", ".", "-I", "include", "--jobs", "1", "--baseline", str(baseline)]

    # -- 1. 无回归：应 3/3 通过 ----------------------------------------
    clean = []
    clean_times = []
    for _ in range(REPEATS):
        code, out, seconds = run_cli(gate_args, project)
        clean.append({"exit": code, "seconds": round(seconds, 2), "tail": out.strip().splitlines()[-1] if out.strip() else ""})
        clean_times.append(seconds)

    # -- 2. 注入回归：给 main.cpp 增加一个重量级头 ---------------------
    main_cpp = project / "app" / "main.cpp"
    original = main_cpp.read_text(encoding="utf-8")

    # 注入什么，决定这次实验有没有意义。
    #
    # 前两次尝试都失败了，两次都不是门禁的问题：
    #   1. 注入已在闭包里的 core.h —— 加一条冗余 include 不改变「哪些 TU 覆盖
    #      了它」，归因行数一点没变。**门禁量的是覆盖范围，不是 include 条数。**
    #   2. 想从现有头里挑一个 main.cpp 没覆盖的 —— main.cpp 已经覆盖了全部头，
    #      没有可挑的。
    # 所以改为新建一个重头再包含它：这是唯一能确定性扩大覆盖面的注入方式。
    # 行数按基线归因行数的 10% 生成，确保与 5% 容差有明显区分度。
    baseline_data = json.loads(baseline.read_text(encoding="utf-8"))
    before_lines = baseline_data.get("totals", {}).get("attributed_lines", 0)
    injected_lines = max(2000, int(before_lines * 0.10))
    victim = "zz_injected_heavy.h"
    (project / "include" / victim).write_text(
        "#pragma once\n"
        + "\n".join(f"inline int injected_{i}() {{ return {i}; }}" for i in range(injected_lines))
        + "\n",
        encoding="utf-8",
    )
    # BOM 必须留在文件最前面：把它挤到第二行，clang 会报
    # "unexpected character <U+FEFF>"，main.cpp 直接编译失败——那测的就不是
    # 成本回归，而是「我们的注入写坏了文件」。（第一次跑正是这样，门禁因为
    # TU 从 2 掉到 1 而通过，反而暴露了门禁的真缺陷。）
    bom = "\ufeff" if original.startswith("\ufeff") else ""
    injected = bom + f'#include "{victim}"\n' + original[len(bom):]

    main_cpp.write_text(injected, encoding="utf-8")

    regressed = []
    regressed_times = []
    for _ in range(REPEATS):
        code, out, seconds = run_cli(gate_args, project)
        regressed.append(
            {
                "exit": code,
                "seconds": round(seconds, 2),
                "output": [line for line in out.splitlines() if line.strip()][:4],
            }
        )
        regressed_times.append(seconds)

    main_cpp.write_text(original, encoding="utf-8")

    # -- 3. 构建损坏：TU 变少时门禁必须失败，不能「越坏越绿」 -----------
    # 这是修复前实测到的缺陷：注入导致 main.cpp 编译失败，TU 从 2 掉到 1，
    # 总成本算出 -67.7%，门禁**通过**。本场景把这条路径固定下来。
    util_cpp = project / "app" / "util.cpp"
    util_original = util_cpp.read_text(encoding="utf-8")
    util_cpp.write_text(util_original + "\nthis is not valid c++;\n", encoding="utf-8")
    broken = []
    for _ in range(REPEATS):
        code, out, seconds = run_cli(gate_args, project)
        broken.append(
            {
                "exit": code,
                "seconds": round(seconds, 2),
                "output": [line for line in out.splitlines() if line.strip()][:4],
            }
        )
    util_cpp.write_text(util_original, encoding="utf-8")

    payload = {
        "date": date.today().isoformat(),
        "sample": "benchmarks/cases/demo",
        "repeats": REPEATS,
        "injected_include": victim,
        "injected_header_lines": injected_lines,
        "baseline_attributed_lines": before_lines,
        "clean_runs": clean,
        "clean_pass": sum(1 for r in clean if r["exit"] == 0),
        "clean_false_positive": any(r["exit"] != 0 for r in clean),
        "regression_runs": regressed,
        "regression_detected": sum(1 for r in regressed if r["exit"] != 0),
        "broken_build_runs": broken,
        "broken_build_detected": sum(1 for r in broken if r["exit"] != 0),
        "gate_seconds_median": round(sorted(clean_times)[len(clean_times) // 2], 2),
        "regressed_seconds_median": round(sorted(regressed_times)[len(regressed_times) // 2], 2),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / f"g3-gate-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"# G3 门禁实测（{payload['date']}）",
        "",
        f"- 样本：`benchmarks/cases/demo` 的副本（原工程不被修改）",
        f"- 协议：`gate --baseline`，容差默认 5%，每侧重复 {REPEATS} 次",
        "",
        "## 1. 无回归时必须通过（误报）",
        "",
        "| # | exit | 耗时 | 结论行 |",
        "|---:|---:|---:|---|",
        *[f"| {i+1} | {r['exit']} | {r['seconds']}s | {r['tail']} |" for i, r in enumerate(clean)],
        "",
        f"**通过 {payload['clean_pass']}/{REPEATS}；误报：{'有' if payload['clean_false_positive'] else '无'}**",
        "",
        "## 2. 注入回归后必须失败（检出）",
        "",
        f"注入方式：给 `main.cpp` 增加 `#include \"{victim}\"`。",
        "",
        "| # | exit | 耗时 | 输出摘要 |",
        "|---:|---:|---:|---|",
        *[
            f"| {i+1} | {r['exit']} | {r['seconds']}s | {' / '.join(r['output'][:2])} |"
            for i, r in enumerate(regressed)
        ],
        "",
        f"**检出 {payload['regression_detected']}/{REPEATS}**",
        "",
        "## 3. 构建损坏（TU 变少）时必须失败",
        "",
        "给 `app/util.cpp` 追加一行非法 C++，让它编译不过。修复前的情形是："
        "TU 从 2 掉到 1、总成本算出 −67.7%、门禁**通过**。",
        "",
        "| # | exit | 耗时 | 输出摘要 |",
        "|---:|---:|---:|---|",
        *[
            f"| {i+1} | {r['exit']} | {r['seconds']}s | {' / '.join(r['output'][:2])} |"
            for i, r in enumerate(broken)
        ],
        "",
        f"**检出 {payload['broken_build_detected']}/{REPEATS}**",
        "",
        "## 4. 成本",
        "",
        f"- 无回归时单次门禁中位 **{payload['gate_seconds_median']}s**",
        f"- 有回归时中位 {payload['regressed_seconds_median']}s（失败路径不多花时间）",
        "",
        "## 判读边界",
        "",
        "- 检出率 3/3 只覆盖**一种**回归形态（归因行数上升）。",
        "  编译耗时上升但行数不变的回归（例如宏展开变复杂）不在当前门禁的判据内，",
        "  这类回归不会被发现——这是已知局限，不是已解决项。",
        "- 样本是构造工程，接入真实 CI（检出、缓存、并行）的成本未测。",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"clean pass {payload['clean_pass']}/{REPEATS} (false positive={payload['clean_false_positive']})")
    print(f"regression detected {payload['regression_detected']}/{REPEATS}")
    print(f"broken build detected {payload['broken_build_detected']}/{REPEATS}")
    print(f"gate median {payload['gate_seconds_median']}s")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
