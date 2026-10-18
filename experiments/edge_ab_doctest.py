#!/usr/bin/env python3
"""边级成本模型的 A/B 精度实验（doctest prelude.h）。

目的：回答「预测的边际毫秒，和真的删掉这条 include 省下的墙钟，对不对得上」，
以及「行数模型与毫秒模型谁把可删的边排得更靠前」。

设计（沿用 CBE 协议）：
1. 候选 = prelude.h 里真实生效的每一条 ``#include``；
2. 可删性判定 = 删掉后 **全部** 24 个编译单元仍能编译（否则如实记为「不可删」）；
3. 计时 = 成对交替（基线、改动、基线、改动）取中位数，抵消机器漂移；
4. 预测值来自 ``edge-spike-doctest-20261019.json``（同一份 trace 数据），
   按被测 TU 数等比缩放，保证两边口径一致。

用法：
    python experiments/edge_ab_doctest.py \\
        --db <build>/compile_commands.json --tus 24 --sample 6
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heavyheader.scanner import _split_command, _strip_output_flag  # noqa: E402

PRELUDE = Path("doctest/parts/private/prelude.h")
INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]")


def load_units(db_path: Path, limit: int) -> list[dict]:
    entries = json.loads(db_path.read_text(encoding="utf-8"))
    return entries[:limit]


def compile_once(entry: dict, workdir: Path, slot: str) -> tuple[bool, float, str]:
    argv = _strip_output_flag(_split_command(entry["command"]))
    obj = workdir / f"{slot}.o"
    argv += ["-o", str(obj)]
    start = time.perf_counter()
    proc = subprocess.run(
        argv,
        cwd=entry["directory"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    wall = time.perf_counter() - start
    return proc.returncode == 0, wall, (proc.stderr or "")[-400:]


def candidates(prelude: Path) -> list[tuple[int, str]]:
    """(行号, 头文件名) —— 只取文件里真实写着的 include 行。"""
    out: list[tuple[int, str]] = []
    for idx, line in enumerate(prelude.read_text(encoding="utf-8").splitlines(), 1):
        m = INCLUDE_RE.match(line)
        if m:
            out.append((idx, m.group(1)))
    return out


def remove_line(prelude: Path, lineno: int) -> str:
    lines = prelude.read_text(encoding="utf-8").splitlines(keepends=True)
    original = lines[lineno - 1]
    lines[lineno - 1] = "// " + original
    prelude.write_text("".join(lines), encoding="utf-8")
    return original


def restore_line(prelude: Path, lineno: int, original: str) -> None:
    lines = prelude.read_text(encoding="utf-8").splitlines(keepends=True)
    lines[lineno - 1] = original
    prelude.write_text("".join(lines), encoding="utf-8")


def paired_timing(
    units: list[dict], workdir: Path, rounds: int, tag: str
) -> tuple[list[float], list[float]]:
    """成对交替：基线/改动 各 rounds 次，返回每单位的中位墙钟。

    顺序在轮次间翻转（B,M / M,B / ...）：先跑的那一臂总是吃到冷缓存，
    固定顺序会把这份系统偏差整块算成「改动带来的收益」。翻转后偏差相消。
    """
    base: list[list[float]] = [[] for _ in units]
    mod: list[list[float]] = [[] for _ in units]
    for r in range(rounds):
        base_first = r % 2 == 0
        for i, entry in enumerate(units):
            arms = ("b", "m") if base_first else ("m", "b")
            for arm in arms:
                ok, wall, _ = compile_once(entry, workdir, f"{arm}_{tag}_{i}_{r}")
                if not ok:
                    continue
                (base if arm == "b" else mod)[i].append(wall)
    return (
        [statistics.median(v) if v else 0.0 for v in base],
        [statistics.median(v) if v else 0.0 for v in mod],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--tus", type=int, default=24)
    ap.add_argument("--sample", type=int, default=6, help="计时用的 TU 采样数")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--spike", type=Path, required=True, help="边级预测 JSON")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个候选（冒烟用）")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    project = args.project.resolve()
    prelude = project / PRELUDE
    units = load_units(args.db, args.tus)
    # 计时样本：取前 N 个单位（同一批，跨候选可比）
    sample = units[: args.sample]

    spike = json.loads(args.spike.read_text(encoding="utf-8"))
    predicted = {
        (Path(e["parent"]).name, Path(e["child"]).name): e for e in spike["edges"]
    }

    cands = candidates(prelude)
    if args.limit:
        cands = cands[: args.limit]
    print(f"[setup] TUs={len(units)} sample={len(sample)} candidates={len(cands)}")

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        # 基线：全部 TU 编一遍，记录成功与耗时（可删性判定的参照）
        base_ok: list[bool] = []
        base_wall: list[float] = []
        for i, entry in enumerate(units):
            ok, wall, err = compile_once(entry, work, f"base_{i}")
            base_ok.append(ok)
            base_wall.append(wall)
        n_ok = sum(base_ok)
        print(f"[baseline] {n_ok}/{len(units)} compiled, mean {statistics.mean(base_wall):.3f}s")
        if n_ok != len(units):
            print("[baseline] 基线本身有编译失败，实验无效", file=sys.stderr)
            return 2

        for k, (lineno, header) in enumerate(cands, 1):
            original = remove_line(prelude, lineno)
            try:
                # 1) 可删性：全部 TU 都要过
                fails = []
                for i, entry in enumerate(units):
                    ok, _, err = compile_once(entry, work, f"chk_{k}_{i}")
                    if not ok:
                        fails.append(Path(entry["file"]).name)
                removable = not fails
                # 2) 计时只在可删时做（不可删没有「省多少」可言）
                base_s, mod_s = ([0.0] * len(sample), [0.0] * len(sample))
                if removable:
                    base_s, mod_s = paired_timing(
                        sample, work, args.rounds, f"c{k}"
                    )
                deltas = [b - m for b, m in zip(base_s, mod_s, strict=True)]
                measured = sum(deltas)
                key = ("prelude.h", header)
                pred = predicted.get(key)
                rows.append(
                    {
                        "header": header,
                        "line": lineno,
                        "removable": removable,
                        "failed_units": fails[:4],
                        "failed_count": len(fails),
                        "measured_saved_seconds_sample": round(measured, 4),
                        "measured_per_tu": round(measured / max(len(sample), 1), 4),
                        "predicted_marginal_seconds_total": None
                        if pred is None
                        else round(pred["marginal_seconds"], 4),
                        "predicted_scaled_to_sample": None
                        if pred is None
                        else round(pred["marginal_seconds"] * len(sample) / len(units), 4),
                        "predicted_marginal_lines": None
                        if pred is None
                        else pred["marginal_lines"],
                        "tus_predicted": None if pred is None else pred["tus"],
                        "base_per_tu": [round(x, 4) for x in base_s],
                        "mod_per_tu": [round(x, 4) for x in mod_s],
                        "stderr": "" if removable else "",
                    }
                )
                flag = "OK " if removable else "NO "
                pred_scaled = (
                    0.0
                    if pred is None
                    else pred["marginal_seconds"] * len(sample) / len(units)
                )
                print(
                    f"[{k:2d}/{len(cands)}] {flag}{header:20s} "
                    f"fails={len(fails):2d} measured={measured * 1000:8.1f}ms "
                    f"pred={pred_scaled * 1000:8.1f}ms"
                )
            finally:
                restore_line(prelude, lineno, original)

    payload = {
        "project": str(project),
        "tus_total": len(units),
        "tus_sample": len(sample),
        "rounds": args.rounds,
        "baseline_mean_seconds": round(statistics.mean(base_wall), 4),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
