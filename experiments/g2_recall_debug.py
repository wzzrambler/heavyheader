"""诊断：注入到头文件里的那条 include，为什么没变成候选？

``g2_recall.py --level header`` 跑出「排名 = None」，而同样的注入放到 .cpp
里能被找到（排名 25、实测省 6.6%）。差一步查清楚就会把「实验没做好」
误写成「工具找不到」，所以单独开一个小脚本把中间量打出来：

* 注入行到底在不在编译器的 include 图里；
* 它在的话，独占闭包有多大、用法判定是什么、为什么被闸门挡掉或没被挡掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from g2_recall import CLANG, TARGET_FLAGS, inject  # noqa: E402

from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers  # noqa: E402
from heavyheader.attrib.sites import build_include_graph, find_header_candidates  # noqa: E402
from heavyheader.attrib.usage import IdentifierIndex, analyse_usage  # noqa: E402
from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

PROJECT = (ROOT / "experiments" / "_work" / "doctest").resolve()
DB = PROJECT / "compile_commands.json"
HEADER = PROJECT / "doctest" / "parts" / "private" / "prelude.h"


def main() -> int:
    original = HEADER.read_text(encoding="utf-8", errors="replace")
    inject(HEADER)
    try:
        driver = detect_driver(CLANG)
        scan = scan_project(
            ScanOptions(
                root=PROJECT,
                compiler=CLANG,
                compile_commands=DB,
                max_tus=24,
                jobs=1,
                extra_flags=list(TARGET_FLAGS),
            ),
            driver=driver,
        )
        scan.candidates = []
        sizes = HeaderSizes()
        build_header_stats(scan, sizes)
        kinds = classify_headers(PROJECT, {h for tu in scan.ok_tus for h in tu.dependencies})
        children = build_include_graph(scan)
        print(f"prelude.h kind = {kinds.get(HEADER)}")
        print("prelude.h 的直接包含：")
        for child in children.get(HEADER, []):
            print(f"  {child}")
        gated = find_header_candidates(scan, sizes, kinds)
        ungated = find_header_candidates(scan, sizes, kinds, require_plausibly_unused=False)
        print(f"\n闸门开 {len(gated)} 条 / 关 {len(ungated)} 条")
        print("\n闸门开的逐条：")
        for c in gated:
            print(f"  {Path(c.site).name} -> {c.header.name} lines={c.estimated_lines_saved} reach={c.reach}")
        print("\n闸门关的前 12 条（看 regex 在不在、判什么）：")
        for c in sorted(ungated, key=lambda c: -c.amplified_lines_saved)[:12]:
            print(f"  {Path(c.site).name} -> {c.header.name:24s} lines={c.estimated_lines_saved:7d} "
                  f"reach={c.reach} verdict={c.usage_verdict}")
        # 直接问一句：prelude.h 对 regex 的用法判定是什么
        index = IdentifierIndex()
        regex = next((p for p in children.get(HEADER, []) if p.name == "regex"), None)
        if regex is not None:
            signal = analyse_usage(HEADER, regex, index=index)
            print(f"\nprelude.h 对 <regex> 的用法判定：{signal.verdict.value}  命中标识符："
                  f"{list(signal.shared_identifiers)[:8]}")
        else:
            print("\ninclude 图里没有 <regex>：注入没生效或没被解析")
        return 0
    finally:
        HEADER.write_text(original, encoding="utf-8")
        print("已还原")


if __name__ == "__main__":
    raise SystemExit(main())
