"""G2（重做）：换掉候选生成策略之后，优化闭环成立吗？

旧协议为什么判不出来
--------------------
旧的候选空间是「编译单元自己的 ``#include``」。实测（``g2_probe.py``）：
nlohmann/json 25 个 TU、83 条候选，其中「疑似未使用」只有 3 条，全是
``<iostream>``，单 TU 省 1.0–1.9%——压在 2% 噪声线下方。G2 得 0/10。

那个 0 有两个来源，必须分开：

1. **候选空间太窄**：一个 TU 只 include 两三个文件，几乎全是要用的。
   把 include 图摊到所有文件上后有 432 对候选（json），空间大一个数量级。
2. **测量协议太钝**：先测完基线、再测补丁，机器漂移整块落到差值上。
   实测聚合 A/A 差 2.005%；改成交替测量后降到 0.644% / 0.245%。

本脚本同时换掉这两样，然后在**同一个真实工程**上重跑 G2：

* 候选来自 :func:`heavyheader.attrib.sites.find_header_candidates`
  （头文件级 + 可行性闸门 + 收益按受影响 TU 数放大）；
* 判定阈值用 :data:`heavyheader.attrib.advice.AGGREGATE_NOISE_FLOOR`
  （聚合 + 交替口径，由 ``g2_interleaved.py`` 的 A/A 标定）。

三条臂
------
1. **feasible**：闸门开，按放大收益取前 K 条 —— 新策略本身。
2. **ungated**：闸门关，同样取前 K 条 —— 消融对照，用来证明闸门有用；
   如果两臂编译通过率一样，那闸门就只是装饰。
3. **tu-level**：旧的 TU 级候选前 K 条 —— 新旧策略的直接对照。

采纳 = 编译通过（头文件级要求**所有**受影响单元都通过）**且**省时超过
聚合噪声线 **且**复测仍成立。

用法::

    python experiments/g2_sites.py --project experiments/_work/doctest \
        --db experiments/_work/doctest/compile_commands.json --tus 24 --top 5
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.advice import AGGREGATE_NOISE_FLOOR  # noqa: E402
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import candidate_rank_key  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def clone(candidate):
    """丢掉上一轮的探针/测量状态（理由与 g2_loop.clone_candidate 相同）。"""
    from dataclasses import replace

    return replace(
        candidate,
        measured_seconds_saved=None,
        verified=False,
        probe_status="",
        advice=None,
    )


def run_arm(scan, driver, project, candidates, confirm: int, runs: int, jobs: int):
    saved = scan.candidates
    scan.candidates = list(candidates)
    try:
        fallback = DriverOptions(
            standard="c++17",
            include_dirs=[project / "include"],
            extra_flags=list(TARGET_FLAGS),
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=1)
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
    # 候选由本脚本自己生成（要跑三种口径），不让 scanner 预生成干扰。
    scan.candidates = []

    from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers

    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(project, {h for tu in scan.ok_tus for h in tu.dependencies})

    gated = find_header_candidates(scan, sizes, kinds)
    ungated = find_header_candidates(
        scan, sizes, kinds, require_plausibly_unused=False
    )
    print(f"TU: {len(scan.ok_tus)}  头文件级候选：闸门开 {len(gated)} 条 / 闸门关 {len(ungated)} 条")
    for c in gated[: args.top]:
        print(
            f"  {str(Path(c.site).relative_to(project))[:46]:46s} -> {c.header.name[:24]:24s} "
            f"lines={c.estimated_lines_saved:6d} reach={c.reach:3d} "
            f"amplified={c.amplified_lines_saved:8d} est%={c.estimated_percent:.3f}"
        )

    feasible_arm = sorted(gated, key=candidate_rank_key)[: args.top]
    ungated_arm = sorted(ungated, key=lambda c: -c.amplified_lines_saved)[: args.top]
    tu_arm = [c for c in sorted(scan.candidates, key=candidate_rank_key)][: args.top]

    arms = []
    if feasible_arm:
        res = run_arm(scan, driver, project, [clone(c) for c in feasible_arm], 2, args.runs, args.jobs)
        arms.append(summarize(res, "feasible(header,gated)"))
    if ungated_arm:
        res = run_arm(scan, driver, project, [clone(c) for c in ungated_arm], 2, args.runs, args.jobs)
        arms.append(summarize(res, "ungated(header,top-by-size)"))
    if tu_arm:
        res = run_arm(scan, driver, project, [clone(c) for c in tu_arm], 2, args.runs, args.jobs)
        arms.append(summarize(res, "tu-level(old strategy)"))

    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "tus": len(scan.ok_tus),
        "top": args.top,
        "runs_per_side": args.runs,
        "aggregate_noise_floor_percent": AGGREGATE_NOISE_FLOOR * 100,
        "candidate_pool": {"gated": len(gated), "ungated": len(ungated)},
        "arms": arms,
    }
    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"g2-sites-{project.name}-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"# G2 重做：头文件级候选 + 交替测量（{payload['date']}，{project.name}）",
        "",
        f"- 工程：{project.name}，{payload['tus']} 个 TU。",
        f"- 候选池：可行性闸门开 {len(gated)} 条 / 关 {len(ungated)} 条。",
        f"- 协议：clang，交替 min-of-{args.runs}，"
        f"聚合噪声线 {AGGREGATE_NOISE_FLOOR * 100:.0f}%，improved 需复测。",
        "",
        "| 臂 | 候选 | 编译通过 | improved | 采纳率 |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {a['arm']} | {a['candidates']} | {a['compiled']} | {a['improved']} | **{a['adopt_rate']:.0%}** |"
            for a in arms
        ],
        "",
    ]
    for a in arms:
        lines += [
            f"## {a['arm']} 逐条",
            "",
            "| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |",
            "|---|---|---:|---|---:|---:|---:|",
            *[
                f"| `{d['site']}` | `{d['header']}` | {d['reach']} | {d['status']} | "
                f"{d['baseline_ms']} | {d['patched_ms']} | {d['percent_saved']}% |"
                for d in a["details"]
            ],
            "",
        ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for a in arms:
        print(f"{a['arm']:34s} candidates={a['candidates']:3d} compiled={a['compiled']:3d} "
              f"improved={a['improved']:3d} adopt={a['adopt_rate']:.0%}")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
