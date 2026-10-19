"""语料选型筛选：同一扫描配置下比较各工程的候选池与预估收益。

只做扫描（每个 TU 编译一次），不做计时闭环——先便宜地把有希望跑出计时
收益的工程挑出来。输出四张表：TU、头文件级 site 候选（闸门开/关）、
预估聚合收益最高的前 8 条 site、TU 层疑似未使用条数。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmarks"))

from run_all import CASES, clone  # noqa: E402

from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers  # noqa: E402
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

CLANGXX = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def screen(case) -> None:
    project = clone(case)
    driver = detect_driver(CLANGXX)
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=CLANGXX,
            include_dirs=[project / inc for inc in case.includes],
            exclude_dirs=set(case.excludes),
            defines=list(case.defines),
            max_tus=case.max_tus,
            extra_flags=[f"-std={case.std}", *TARGET_FLAGS],
            jobs=1,
        ),
        driver=driver,
    )
    n_fail = len(scan.tus) - len(scan.ok_tus)
    print(f"\n===== {case.name}: {len(scan.ok_tus)} TU ok / {n_fail} fail")
    if not scan.ok_tus:
        return
    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(project, {h for tu in scan.ok_tus for h in tu.dependencies})
    gated = find_header_candidates(scan, sizes, kinds)
    ungated = find_header_candidates(scan, sizes, kinds, require_plausibly_unused=False)
    tu_pu = [c for c in scan.candidates if c.usage_verdict == "plausibly_unused"]
    print(f"  site 候选：闸门开 {len(gated)} / 闸门关 {len(ungated)}；TU 层疑似未使用 {len(tu_pu)}")
    pool = sorted(gated, key=lambda c: -c.estimated_percent) or sorted(
        ungated, key=lambda c: -c.estimated_percent
    )
    for c in pool[:8]:
        site = Path(c.site).name
        if str(c.site).startswith(str(project)):
            site = str(Path(c.site).relative_to(project))[:40]
        print(
            f"    {site:42s} -> {c.header.name[:22]:22s} est%={c.estimated_percent:6.3f} "
            f"reach={c.reach:3d} verdict={c.usage_verdict[:12]}"
        )


def main() -> int:
    names = sys.argv[1:] or [c.name for c in CASES]
    for case in CASES:
        if case.name in names:
            try:
                screen(case)
            except Exception as exc:  # noqa: BLE001 - 筛选脚本要扛住单个工程失败
                print(f"\n===== {case.name}: FAILED {exc!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
