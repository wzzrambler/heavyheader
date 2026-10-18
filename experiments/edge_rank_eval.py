#!/usr/bin/env python3
"""边级排序质量评估：行数模型 vs 边际毫秒模型，用真 A/B 结局做 ground truth。

输入：
  --spike  experiments/results/edge-spike-doctest-*.json   （两模型对全部边的预测）
  --ab     experiments/results/edge-ab-doctest-*.json      （prelude.h 各边的真结局）

输出指标（只在有 ground truth 的边上算，口径一致）：
  1. 可采纳数：删掉后全部 TU 仍编译 + 实测省时超过噪声线；
  2. 首条可采纳排名：两个模型各自第一名的位置（越小越好）；
  3. precision@3 / @5：Top-k 里可采纳的比例；
  4. 预测-实测秩相关：毫秒预测 vs 实测节省、行数预测 vs 实测节省。
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

#: 单次编译的实际噪声水平（由成对交替观测到的同臂波动给出，单位秒）。
#: 低于它的「收益」不当作可利用结论——与产品里 verify 的噪声线同一口径。
NOISE_SECONDS = 0.05


def spearman(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spike", type=Path, required=True)
    ap.add_argument("--ab", type=Path, required=True)
    ap.add_argument(
        "--noise",
        type=float,
        default=NOISE_SECONDS,
        help="收益噪声线（秒）。默认 0.05；本机在 24 TU 尺度上的实测残差约 0.25",
    )
    ap.add_argument("--md", type=Path, help="同时写一份 Markdown 结论")
    args = ap.parse_args()

    noise = args.noise
    spike = json.loads(args.spike.read_text(encoding="utf-8"))
    ab = json.loads(args.ab.read_text(encoding="utf-8"))

    total_tus = ab["tus_total"]
    sample_tus = ab["tus_sample"]
    truth = {r["header"]: r for r in ab["rows"]}

    # 只保留 prelude.h -> X 的边，并且必须在 A/B 里被真的试过
    rows = []
    for e in spike["edges"]:
        if Path(e["parent"]).name != "prelude.h":
            continue
        child = Path(e["child"]).name
        t = truth.get(child)
        if t is None:
            continue
        measured_per_tu = t["measured_saved_seconds_sample"] / max(sample_tus, 1)
        measured_total = measured_per_tu * total_tus
        rows.append(
            {
                "header": child,
                "pred_seconds": e["marginal_seconds"],
                "pred_lines": e["marginal_lines"],
                "removable": t["removable"],
                "measured_total": measured_total,
                "beneficial": t["removable"] and measured_total > noise,
            }
        )

    n = len(rows)
    n_removable = sum(1 for r in rows if r["removable"])
    n_beneficial = sum(1 for r in rows if r["beneficial"])
    print(f"# 样本：{n} 条 prelude.h 出边（{total_tus} 个 TU，计时采样 {sample_tus} 个）")
    print(
        f"# 可删：{n_removable} 条；"
        f"可删且实测省时 > {noise * 1000:.0f}ms：{n_beneficial} 条"
    )

    def top_k(seq: list[dict], k: int) -> list[dict]:
        return seq[:k]

    by_sec = sorted(rows, key=lambda r: r["pred_seconds"], reverse=True)
    by_line = sorted(rows, key=lambda r: r["pred_lines"], reverse=True)

    print("\n## 逐条结局（按毫秒模型排序）")
    print(f"{'#':>3} {'header':22s} {'pred_ms':>9} {'meas_ms':>9} {'可删':>4} {'受益':>4}")
    for i, r in enumerate(by_sec, 1):
        print(
            f"{i:3d} {r['header']:22s} {r['pred_seconds'] * 1000:9.1f} "
            f"{r['measured_total'] * 1000:9.1f} {'Y' if r['removable'] else 'N':>4} "
            f"{'Y' if r['beneficial'] else 'N':>4}"
        )

    print("\n## 两个模型的头部质量")
    for name, seq in (("毫秒模型", by_sec), ("行数模型", by_line)):
        first = next((i for i, r in enumerate(seq, 1) if r["beneficial"]), None)
        p3 = sum(1 for r in top_k(seq, 3) if r["beneficial"])
        p5 = sum(1 for r in top_k(seq, 5) if r["beneficial"])
        first_removable = next((i for i, r in enumerate(seq, 1) if r["removable"]), None)
        print(
            f"{name}: 首条可删 #{first_removable}，首条受益 #{first}，"
            f"precision@3={p3}/3，precision@5={p5}/5"
        )
        print("  top5:", ", ".join(r["header"] for r in top_k(seq, 5)))

    print(f"\n## 预测 vs 实测（秩相关，n={n}）")
    rho_sec = spearman(
        [r["pred_seconds"] for r in rows], [r["measured_total"] for r in rows]
    )
    rho_line = spearman(
        [r["pred_lines"] for r in rows], [r["measured_total"] for r in rows]
    )
    print(f"毫秒预测 vs 实测节省: rho = {rho_sec:+.3f}")
    print(f"行数预测 vs 实测节省: rho = {rho_line:+.3f}")

    # 只在可删边上再算一次：不可删的边节省恒为 0，会把相关性整体压平
    rem = [r for r in rows if r["removable"]]
    if len(rem) >= 3:
        print(f"\n## 只在可删的 {len(rem)} 条边上重算")
        s = spearman([r["pred_seconds"] for r in rem], [r["measured_total"] for r in rem])
        line = spearman([r["pred_lines"] for r in rem], [r["measured_total"] for r in rem])
        print(f"毫秒预测 vs 实测: rho = {s:+.3f}")
        print(f"行数预测 vs 实测: rho = {line:+.3f}")

    if args.md:
        args.md.write_text("(见终端输出，脚本自动生成见 --md 支持)\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
