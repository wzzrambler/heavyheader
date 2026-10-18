"""G2 第三问：工程里**确实存在**可删的 include 时，工具找得到吗？

前两问的答案（``g2_sites.py``）是：

* 可行性闸门有用：闸门开 2/3 编译通过，闸门关 0/5；
* 但 doctest 里**天然**可删的那几条只省 0.31%–0.32%，低于聚合 A/A 的
  0.644%——不是找不到，是找到了也测不出来。

于是「G2 不成立」这句话仍然有两种读法，必须分开：

1. 「这个工程没有值得删的东西」 —— 那是工程的属性，不是工具的缺陷；
2. 「有值得删的东西时工具也找不出来」 —— 那才是工具不成立。

区分二者只能靠**注入**：往真实工程里放几条确实多余、且确实昂贵的
``#include``，然后看工具能不能把它们找出来、排到前面、并实测证实。
注入的是真实编译、真实测量，唯一「人造」的是那条 include 本身。

本脚本注入两条：

* 头文件级：往 ``prelude.h`` 加一条 ``#include <regex>``（被 24 个 TU 解析）；
* TU 级：往某个 part 的 .cpp 加一条 ``#include <regex>``。

报告三件事：召回（在不在候选里、排第几）、编译通过、实测收益。
注入的文件一律在 ``finally`` 里还原。

用法::

    python experiments/g2_recall.py --project experiments/_work/doctest \
        --db experiments/_work/doctest/compile_commands.json --tus 24
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.advice import AGGREGATE_NOISE_FLOOR, TIME_NOISE_FLOOR  # noqa: E402
from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers
from heavyheader.attrib.sites import find_header_candidates
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import candidate_rank_key  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]
INJECTED = "<regex>"


def inject(path: Path) -> str:
    """把注入行插到最后一个**顶层** ``#include`` 之后。

    「顶层」不是讲究，是必须：``prelude.h`` 末尾的 include 在
    ``#ifdef DOCTEST_PLATFORM_MAC`` 里，第一次注入就插进了那个块，在 Windows
    上根本没被解析——实验跑完显示「头文件级召回 = None」，查下来是注入
    位置错了，不是工具没找到。条件编译的嵌套深度要自己数：``#if*`` 进一层，
    ``#endif`` 出一层。
    """
    original = path.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines()
    depth = 0
    last_top_level = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#if"):
            depth += 1
        elif stripped.startswith("#endif"):
            depth = max(0, depth - 1)
        elif stripped.startswith("#include") and depth <= 1:
            # 文件级 include guard 也算一层，所以「顶层」是 depth ≤ 1：
            # 包在 #ifdef DOCTEST_PLATFORM_MAC 里的 include 是 depth 2。
            last_top_level = index
    if last_top_level is None:
        raise RuntimeError(f"{path} 里找不到顶层 #include")
    patched = (
        lines[: last_top_level + 1]
        + [f"#include {INJECTED}"]
        + lines[last_top_level + 1 :]
    )
    path.write_text("\n".join(patched) + "\n", encoding="utf-8")
    return original


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--level",
        choices=["tu", "header"],
        required=True,
        help="只注入一处：tu = 注入某个 .cpp；header = 注入 prelude.h",
    )
    args = parser.parse_args()

    project = Path(args.project).resolve()
    db = Path(args.db).resolve()
    header = project / "doctest" / "parts" / "private" / "prelude.h"
    unit = project / "doctest" / "parts" / "private" / "assert" / "data.cpp"
    for path in (header, unit):
        if not path.exists():
            print(f"找不到注入目标：{path}")
            return 1

    # 一次只能注入一处。两处同时注入会互相抵消：prelude.h 里的 regex 会让
    # data.cpp 里那条变成「删了也还在」（prelude.h 仍然把它拉进来），于是
    # 独占闭包为空、候选根本不生成——那是工具判对了，实验却读成了「没找到」。
    target = header if args.level == "header" else unit
    original = target.read_text(encoding="utf-8", errors="replace")
    try:
        inject(target)
        return _run(project, db, args, header, unit)
    finally:
        target.write_text(original, encoding="utf-8")
        print(f"注入已还原：{target.name}")


def _scan(project: Path, db: Path, tus: int):
    driver = detect_driver(CLANG)
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=CLANG,
            compile_commands=db,
            max_tus=tus,
            jobs=1,
            extra_flags=list(TARGET_FLAGS),
        ),
        driver=driver,
    )
    scan.candidates = []
    return driver, scan


def _run(project: Path, db: Path, args, header: Path, unit: Path) -> int:
    driver, scan = _scan(project, db, args.tus)
    if not scan.ok_tus:
        print("扫描没有成功单元")
        return 1

    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(project, {h for tu in scan.ok_tus for h in tu.dependencies})

    # ---- 召回：注入的那条在不在候选里、排第几 ------------------------
    from heavyheader.attrib.graph import find_removal_candidates

    tu_candidates = find_removal_candidates(scan, sizes, kinds)
    header_candidates = find_header_candidates(scan, sizes, kinds)

    def rank_of(candidates, predicate) -> int | None:
        ordered = sorted(candidates, key=candidate_rank_key)
        for index, candidate in enumerate(ordered, start=1):
            if predicate(candidate):
                return index
        return None

    tu_rank = rank_of(
        tu_candidates, lambda c: c.header.name == "regex" and Path(c.tu) == unit
    )
    header_rank = rank_of(
        header_candidates,
        lambda c: c.header.name == "regex" and Path(c.site) == header,
    )
    print(f"TU 级候选 {len(tu_candidates)} 条；注入项排名 = {tu_rank}")
    print(f"头文件级候选 {len(header_candidates)} 条；注入项排名 = {header_rank}")

    # 排序本身也要留证据：召回只说「在第几位」，看不出「为什么是这一位」。
    print("\n-- TU 级排序前 8（est% 是行数模型给的预估，不是实测）--")
    for candidate in sorted(tu_candidates, key=candidate_rank_key)[:8]:
        print(
            f"  {Path(candidate.tu).name[:26]:26s} -> {candidate.header.name[:18]:18s} "
            f"{candidate.usage_verdict[:16]:16s} est%={candidate.estimated_percent:6.2f} "
            f"lines={candidate.estimated_lines_saved}"
        )

    targets = []
    for candidate in sorted(tu_candidates, key=candidate_rank_key):
        if candidate.header.name == "regex" and Path(candidate.tu) == unit:
            targets.append(("tu", candidate))
            break
    for candidate in sorted(header_candidates, key=candidate_rank_key):
        if candidate.header.name == "regex" and Path(candidate.site) == header:
            targets.append(("header", candidate))
            break

    # ---- 验证 ---------------------------------------------------------
    fallback = DriverOptions(
        standard="c++17", include_dirs=[project], extra_flags=list(TARGET_FLAGS)
    )
    rows = []
    for kind, candidate in targets:
        scan.candidates = [candidate]
        results = verify_candidates(
            scan,
            driver,
            fallback,
            verify=VerifyOptions(top=1, runs=args.runs, jobs=1, confirm_passes=2),
        )
        for result in results:
            rows.append(
                {
                    "level": kind,
                    "site": Path(candidate.site).name,
                    "header": Path(candidate.header).name,
                    "reach": candidate.reach,
                    "status": result.status,
                    "baseline_ms": None
                    if result.baseline_seconds is None
                    else round(result.baseline_seconds * 1000, 1),
                    "patched_ms": None
                    if result.patched_seconds is None
                    else round(result.patched_seconds * 1000, 1),
                    "percent_saved": None
                    if result.percent_saved is None
                    else round(result.percent_saved, 3),
                    "message": result.message[:200],
                }
            )
            print(
                f"{kind:6s} {Path(candidate.site).name[:24]:24s} -> {result.status:14s} "
                f"{rows[-1]['percent_saved']}%  {result.message[:80]}"
            )

    payload = {
        "date": date.today().isoformat(),
        "level": args.level,
        "project": project.name,
        "tus": len(scan.ok_tus),
        "injected": INJECTED,
        "runs_per_side": args.runs,
        "recall": {
            "tu_level_rank": tu_rank,
            "tu_level_pool": len(tu_candidates),
            "header_level_rank": header_rank,
            "header_level_pool": len(header_candidates),
        },
        "noise_floors_percent": {
            "single_unit": TIME_NOISE_FLOOR * 100,
            "aggregate": AGGREGATE_NOISE_FLOOR * 100,
        },
        "verification": rows,
    }
    out = ROOT / "experiments" / "results" / f"g2-recall-{project.name}-{args.level}-{date.today():%Y%m%d}"
    out.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        f"# G2 注入-召回（{payload['date']}，{project.name}）",
        "",
        f"- 注入：{INJECTED}，一处进头文件 ``prelude.h``（24 TU），一处进 TU 源 ``data.cpp``。",
        f"- 召回：TU 级第 {tu_rank} / {len(tu_candidates)} 名；"
        f"头文件级第 {header_rank} / {len(header_candidates)} 名。",
        f"- 噪声线：单单元 {TIME_NOISE_FLOOR * 100:.0f}%，聚合 {AGGREGATE_NOISE_FLOOR * 100:.0f}%。",
        "",
        "| 层级 | 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |",
        "|---|---|---|---:|---|---:|---:|---:|",
        *[
            f"| {r['level']} | `{r['site']}` | `{r['header']}` | {r['reach']} | {r['status']} | "
            f"{r['baseline_ms']} | {r['patched_ms']} | {r['percent_saved']}% |"
            for r in rows
        ],
        "",
        "> 注入的 include 是人造的，编译与测量不是。因此这一组数字只能用来回答",
        "> 「存在真实收益时工具找不找得到」，不能用来回答「这个工程有多少优化空间」。",
    ]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
