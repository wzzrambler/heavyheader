"""G2 前置探测（二）：候选空间到底有多大，可参数化跑多个工程。

探测一（``g2_probe.py``）在 nlohmann/json 上的结论很硬：候选池 83 条里只有
3 条「疑似未使用」，全是 ``<iostream>``，单 TU 只省 1.0–1.9%，压在噪声线下。
也就是说 **G2 的 0 不是排序错，是候选定义太窄**——一个 TU 只 include 两三个
文件，几乎全是要用的，删无可删。

真正有余地的地方在**头文件**：一个头文件被几十个 TU 反复解析，它自己 include
的东西只要有一条多余，收益就乘以 TU 数。deheader / IWYU 都在这一层工作。

这个脚本把 include 图摊到**所有文件**上（不只是 TU 源），统计：

* 每个文件 F 的每条 direct include H，F 自身正文用不用 H 独占闭包里的标识符；
* ``reach`` = 有多少个 TU 会解析 F —— 收益的放大器；
* 预估收益 = 独占行数 × reach。

它只报告，不改动任何东西。输出用来回答两个决策问题：

1. 有没有**天然可删**的候选（有的话 G2 可以直接在真实工程上重跑）；
2. 没有的话，G2 必须改成「注入-召回」协议，否则 0 只说明工程干净。

用法::

    python experiments/g2_probe2.py --project experiments/_work/json \
        --db experiments/_work/compile_commands.json --tus 25
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from heavyheader.attrib.usage import IdentifierIndex, UsageVerdict, analyse_usage  # noqa: E402
from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def build_graph(scan):
    """全图：文件 -> 直接包含；以及 TU -> 它解析到的全部文件。"""
    children: dict[Path, list[Path]] = {}
    for tu in scan.ok_tus:
        for ev in tu.include_events:
            bucket = children.setdefault(ev.includer, [])
            if ev.included not in bucket:
                bucket.append(ev.included)

    memo: dict[Path, frozenset[Path]] = {}

    def closure(node: Path) -> frozenset[Path]:
        if node in memo:
            return memo[node]
        memo[node] = frozenset()  # 防环占位
        acc: set[Path] = set()
        for child in children.get(node, ()):
            acc.add(child)
            acc |= closure(child)
        result = frozenset(acc)
        memo[node] = result
        return result

    tu_closure = {tu.source: closure(tu.source) | {tu.source} for tu in scan.ok_tus}
    return children, closure, tu_closure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=25)
    parser.add_argument("--top", type=int, default=25)
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

    children, closure, tu_closure = build_graph(scan)
    index = IdentifierIndex()
    sizes = scan.header_stats

    def lines_of(paths) -> int:
        return sum(getattr(sizes.get(p), "line_count", 0) or 0 for p in paths)

    tu_sources = {tu.source for tu in scan.ok_tus}
    rows = []
    for includer, directs in children.items():
        reach = sum(1 for files in tu_closure.values() if includer in files)
        if reach <= 0:
            continue
        for header in directs:
            shared: set[Path] = set()
            for other in directs:
                if other == header:
                    continue
                shared |= closure(other)
            shared.discard(includer)
            excl = set(closure(header))
            if header not in shared:
                excl.add(header)
            excl -= shared
            if not excl:
                continue
            saved = lines_of(excl)
            signal = analyse_usage(includer, header, closure=excl, index=index)
            rows.append(
                {
                    "includer": includer,
                    "header": header,
                    "verdict": signal.verdict.value,
                    "saved": saved,
                    "reach": reach,
                    "amplified": saved * reach,
                    "is_tu": includer in tu_sources,
                }
            )

    def short(path: Path) -> str:
        try:
            return str(Path(path).relative_to(project))[:52]
        except ValueError:
            return Path(path).name[:52]

    print(f"工程: {project.name}  TU: {len(scan.ok_tus)}  有出边的文件: {len(children)}  "
          f"候选(文件,include)对: {len(rows)}")
    usable = [r for r in rows if r["verdict"] == UsageVerdict.PLAUSIBLY_UNUSED.value]
    print(f"疑似未使用: {len(usable)}  "
          f"（其中 includer 是 TU 源的: {sum(1 for r in usable if r['is_tu'])}）")

    print(f"\n-- 疑似未使用，按 独占行数×reach 排序（前 {args.top}）--")
    print(f"{'includer':52s} {'header':30s} {'lines':>7s} {'reach':>5s} {'×':>9s}")
    for r in sorted(usable, key=lambda r: -r["amplified"])[: args.top]:
        print(
            f"{short(r['includer']):52s} {Path(r['header']).name[:30]:30s} "
            f"{r['saved']:7d} {r['reach']:5d} {r['amplified']:9d}"
        )

    print(f"\n-- 对照：in_use 里放大收益最大的 8 条（空间在哪）--")
    for r in sorted(
        [r for r in rows if r["verdict"] == UsageVerdict.IN_USE.value],
        key=lambda r: -r["amplified"],
    )[:8]:
        print(
            f"{short(r['includer']):52s} {Path(r['header']).name[:30]:30s} "
            f"{r['saved']:7d} {r['reach']:5d} {r['amplified']:9d}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
