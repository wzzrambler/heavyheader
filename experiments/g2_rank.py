"""排序模型对照：归因行数 vs -ftime-trace 实测成本。

为什么单独做一个脚本
--------------------
G2 的注入-召回实验测到一件事：一条**真实收益 6.8%** 的改动，在行数模型下
被排到 **25/61**。候选列表里最值钱的那条排在第 25 位，这个列表对用户就没
用了——不是精度不够，是排错了量。

本脚本把两种排序放在同一批候选上直接比：

* ``lines``：独占行数 × 受影响单元数（现有口径）；
* ``trace``：``-ftime-trace`` 实测的每头文件自身耗时 × 受影响单元数。

主指标是**注入项的排名**（越小越好），以及 Top-K 集合的重合度。同时记录
采集 trace 的额外开销——它是一次真编译，不是免费的，必须量化后才能决定
默认开还是默认关。

用法::

    python experiments/g2_rank.py --project experiments/_work/doctest \\
        --db experiments/_work/doctest/compile_commands.json --tus 24
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.cost import collect_trace_costs  # noqa: E402
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
#: 与 g2_sites.py 完全相同的编译参数。必须一致，否则系统头是两套，
#: 候选集都不同，排名无从比较——第一次跑就是栽在这上面（9 条 vs 5 条）。
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]

#: 注入一条确实多余、确实昂贵的 include。它必须是**顶层**的（不在任何
#: #ifdef 里），否则对多数单元根本不生效，排名变化就说不清是模型的功劳。
INJECTED = "<regex>"


def inject(path: Path) -> str:
    """插到最后一个顶层 ``#include`` 之后，返回原文本以便还原。"""
    original = path.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines()
    depth = 0
    last = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#if"):
            depth += 1
        elif stripped.startswith("#endif"):
            depth = max(depth - 1, 0)
        elif stripped.startswith("#include") and depth == 0:
            last = index
    target = (last + 1) if last is not None else 0
    patched = lines[:target] + [f"#include {INJECTED}"] + lines[target:]
    path.write_text("\n".join(patched) + "\n", encoding="utf-8")
    return original


def build(project: Path, db: Path, tus: int, cost_model: str):
    """扫一遍；``cost_model="trace"`` 时额外真编译一遍全项目采 trace。

    注意开销是**整次扫描**的，不是候选生成那一步的——trace 采集发生在扫描
    内部，用户感受到的就是这个差值。
    """
    driver = detect_driver(CLANG)
    started = time.perf_counter()
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=CLANG,
            compile_commands=db,
            max_tus=tus,
            header_sites=True,
            cost_model=cost_model,
            jobs=1,
            extra_flags=list(TARGET_FLAGS),
        ),
        driver=driver,
    )
    elapsed = time.perf_counter() - started
    return scan, driver, elapsed


def candidates_for(scan, project: Path, *, gated: bool, costs=None):
    """在**同一批候选**上生成列表，只换排序依据。

    默认关闸门：排序对照要在同一集合上比顺序，而闸门会把注入项挡在外面
    （实测注入 ``<regex>`` 后，它拉进来的 ``<string>`` 让拥有这一行的文件
    判成「在用」——闸门做得对，但它会让「排名」这个指标无从测起）。
    """
    from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers

    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(
        scan.root, {h for tu in scan.ok_tus for h in tu.dependencies}
    )
    return find_header_candidates(
        scan,
        sizes,
        kinds,
        require_plausibly_unused=gated,
        costs=costs,
    )


def rank_of(candidates, site_name: str, header_name: str) -> int | None:
    for index, candidate in enumerate(candidates, start=1):
        if (
            Path(candidate.site).name == site_name
            and Path(candidate.header).name == header_name
        ):
            return index
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--header", default=None, help="注入目标头文件（相对项目根）")
    parser.add_argument("--unit", default=None, help="注入目标编译单元（相对项目根）")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--gate",
        action="store_true",
        help="开可行性闸门（默认关：排序对照要在同一批候选上比顺序）",
    )
    args = parser.parse_args()

    project = Path(args.project).resolve()
    db = Path(args.db).resolve()
    header = project / args.header if args.header else None
    unit = project / args.unit if args.unit else None

    originals: dict[Path, str] = {}
    for path in (header, unit):
        if path is not None and path.exists():
            originals[path] = inject(path)
            print(f"已注入 {path.name}")

    try:
        scan_lines, driver, lines_seconds = build(project, db, args.tus, "lines")
        scan_trace, _driver, trace_seconds = build(project, db, args.tus, "trace")
    finally:
        for path, text in originals.items():
            path.write_text(text, encoding="utf-8")
        if originals:
            print("注入已还原")

    gated = args.gate
    costs = scan_trace.cost_profile
    lines_candidates = candidates_for(scan_lines, project, gated=gated, costs=None)
    trace_candidates = candidates_for(scan_trace, project, gated=gated, costs=costs)
    print(
        f"\n头文件级候选（闸门{'开' if gated else '关'}）："
        f"lines {len(lines_candidates)} 条 / trace {len(trace_candidates)} 条"
    )
    if costs is not None:
        print(f"trace 采集：{costs.tu_count} 个单元，{len(costs.self_us)} 个头拿到耗时")
    print(f"扫描耗时：lines {lines_seconds:.1f}s / trace {trace_seconds:.1f}s"
          f"（差 {trace_seconds - lines_seconds:+.1f}s，{(trace_seconds / lines_seconds if lines_seconds else 0):.2f}×）")

    rows = []
    for label, candidates in (("lines", lines_candidates), ("trace", trace_candidates)):
        rank = (
            rank_of(candidates, Path(args.header).name, "regex")
            if args.header
            else None
        )
        rows.append(
            {
                "model": label,
                "candidates": len(candidates),
                "injected_rank": rank,
                "top": [
                    {
                        "site": Path(c.site).name,
                        "header": Path(c.header).name,
                        "lines": c.amplified_lines_saved,
                        "seconds": c.estimated_seconds_saved,
                    }
                    for c in candidates[: args.top]
                ],
            }
        )
        print(f"\n--- {label} --- 注入项排名 = {rank}")
        for index, c in enumerate(candidates[: args.top], start=1):
            print(
                f"  {index:2d}. {Path(c.site).name} → {Path(c.header).name}"
                f"  行数 {c.amplified_lines_saved:>7}  "
                f"秒 {c.estimated_seconds_saved}"
            )

    out = ROOT / "experiments" / "results" / f"g2-rank-{project.name}-{date.today():%Y%m%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "tus": args.tus,
        "injected": INJECTED,
        "scan_seconds": {"lines": round(lines_seconds, 2), "trace": round(trace_seconds, 2)},
        "models": rows,
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
