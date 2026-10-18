"""闸门该开多紧？——三种策略的编译通过率与成本

背景
----
doctest 上闸门放行 16 条，实测 **8 条编译失败**（`g2-sites-doctest-all16`）。
这说明文本闸门的假阳性很高，但它同时也有假阴性（zlib 的 `zconf.h -> <limits.h>`
被判「在用」，实测却编得过）。于是有个真问题：

    放行更多候选（用编译去换召回）值得吗？

本脚本用 ``probe_only``（每条候选删一次、编一次，不测时间）回答它——成本只有
完整测量的 ~1/6，所以可以把**全部候选**都过一遍，而不是只看前 5 条。

三条臂
------
1. ``strict``：默认闸门（只放「疑似未使用」）——当前产品行为；
2. ``+unknown``：再把「文本判定给不出结论」的放进来（``--allow-unknown-sites``）；
3. ``off``：完全不筛（消融对照）。

判据是**编译通过率**，不是「找到多少优化」——后者要完整测量，成本高一个量级，
且已有 `g2-sites-doctest-all16` 覆盖 strict 臂的 16 条。本脚本回答的是「放宽
闸门能多拿到多少条能编的候选，代价是多少次编译」。

用法::

    python experiments/g2_gate_variants.py --project experiments/_work/doctest \
        --db experiments/_work/doctest/compile_commands.json --tus 24
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.graph import (  # noqa: E402
    HeaderSizes,
    build_header_stats,
    classify_headers,
)
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--compiler", default=CLANG)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--arms",
        default="strict,unknown,off",
        help="要跑哪几条臂（逗号分隔）：strict / unknown / off",
    )
    args = parser.parse_args()

    project = Path(args.project).resolve()
    db = Path(args.db).resolve()
    driver = detect_driver(args.compiler)
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=args.compiler,
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
    scan.candidates = []

    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(project, {h for tu in scan.ok_tus for h in tu.dependencies})

    pools = {
        "strict": find_header_candidates(scan, sizes, kinds),
        "unknown": find_header_candidates(
            scan, sizes, kinds, include_unknown=True
        ),
        "off": find_header_candidates(
            scan, sizes, kinds, require_plausibly_unused=False
        ),
    }
    wanted = [item.strip() for item in args.arms.split(",") if item.strip()]

    # verify_candidates 需要一个「与扫描一致」的 DriverOptions；扫描用的是
    # compile_commands 里自带的那套，所以这里传空 options + 目标三元组兜底。
    from heavyheader.drivers.base import DriverOptions

    global_driver_options = DriverOptions(extra_flags=list(TARGET_FLAGS))

    arms: list[dict] = []
    for name in wanted:
        pool = pools.get(name)
        if pool is None:
            print(f"未知臂：{name}")
            continue
        print(f"[{name}] 候选 {len(pool)} 条，开始只验编译……")
        saved = scan.candidates
        scan.candidates = list(pool)
        started = time.perf_counter()
        try:
            results = verify_candidates(
                scan,
                driver,
                global_driver_options,
                verify=VerifyOptions(top=0, probe_only=True, jobs=args.jobs),
            )
        finally:
            scan.candidates = saved
            elapsed = time.perf_counter() - started
        counts: dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        arms.append(
            {
                "arm": name,
                "candidates": len(pool),
                "compiles": counts.get("compiles", 0),
                "compile_error": counts.get("compile-error", 0),
                "other": sum(
                    v
                    for k, v in counts.items()
                    if k not in ("compiles", "compile-error")
                ),
                "pass_rate": round(
                    counts.get("compiles", 0) / len(pool), 3
                )
                if pool
                else 0.0,
                "seconds": round(elapsed, 1),
                "seconds_per_candidate": round(elapsed / len(pool), 2) if pool else 0.0,
                "statuses": counts,
                "pass_list": [
                    {
                        "site": Path(r.candidate.site).name,
                        "header": Path(r.candidate.header).name,
                        "reach": len(r.candidate.affected_tus) or 1,
                    }
                    for r in results
                    if r.status == "compiles"
                ],
            }
        )
        print(
            f"  编译通过 {arms[-1]['compiles']} / {len(pool)}"
            f"（{arms[-1]['pass_rate']:.0%}），耗时 {elapsed:.1f}s"
        )

    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "compiler": args.compiler,
        "tus": len(scan.ok_tus),
        "probe_only": True,
        "arms": arms,
    }
    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"g2-gate-variants-{project.name}-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# 闸门三档对照：编译通过率与成本（{payload['date']}，{project.name}）",
        "",
        f"- 工程：{project.name}，{len(scan.ok_tus)} 个 TU，编译器 `{args.compiler}`。",
        "- 协议：只做编译探测（每条候选删一次、编一次），**不测时间**。",
        "",
        "| 闸门策略 | 候选 | 编译通过 | 编译失败 | 通过率 | 耗时 | 每条耗时 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in arms:
        lines.append(
            f"| {arm['arm']} | {arm['candidates']} | {arm['compiles']} "
            f"| {arm['compile_error']} | **{arm['pass_rate']:.0%}** "
            f"| {arm['seconds']:.0f}s | {arm['seconds_per_candidate']:.1f}s |"
        )
    lines += [
        "",
        "> 「编译通过」只证明删掉这行还能编，**不代表省时**。省时要完整测量，",
        "> 成本约为本表的一个数量级，见 `g2-sites-doctest-all16`。",
        "",
    ]
    for arm in arms:
        lines += [f"## {arm['arm']} 通过编译的候选", ""]
        if not arm["pass_list"]:
            lines += ["（无）", ""]
            continue
        lines += ["| 改动位置 | include | 受影响 TU |", "|---|---|---:|"]
        lines += [
            f"| `{p['site']}` | `{p['header']}` | {p['reach']} |"
            for p in arm["pass_list"]
        ]
        lines.append("")
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
