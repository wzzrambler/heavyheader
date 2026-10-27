"""clang-include-cleaner 对位的复现脚本。

做的事与 ``experiments/results/include-cleaner-20261028.md`` 一一对应：

1. 定位本机 clang-include-cleaner 二进制（找不到就明确说找不到，不降级）。
2. 扫 ``benchmarks/cases/demo``（带 ``--probe``，让我们这一侧也有实测结论）。
3. 为该语料生成一份 ``compile_commands.json``（两个编译单元）。
4. 逐个单元调用 clang-include-cleaner，收齐记录。
5. 与本工具的候选对位，打印总览与逐条结果。

用法::

    python experiments/incclean_probe.py

产物落在 ``experiments/_work/include-cleaner/``。
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORK = HERE / "_work"
OUT = WORK / "include-cleaner"

DEMO = REPO / "benchmarks" / "cases" / "demo"


def _scan_demo():
    """扫 demo 语料；``--probe`` 会真的重编，所以这一侧是实测口径。

    用 **g++** 而不是 clang++：本机的 clang 装在 ``C:\\Program Files\\LLVM\\bin``
    且不在 PATH 上，``scan_project`` 遇到找不到编译器只给一条 warning、
    ``candidates`` 归零——扫描看起来正常，其实是空的。所以这里顺手把
    warning 打出来：静默的 0 候选比报错更难发现。
    """
    from heavyheader.drivers.base import detect_driver
    from heavyheader.scanner import ScanOptions, scan_project

    options = ScanOptions(
        root=DEMO,
        compiler="g++",
        standard="c++17",
        include_dirs=[DEMO / "include"],
        probe=True,
        jobs=2,
    )
    scan = scan_project(options, driver=detect_driver("g++"))
    for warning in scan.warnings or []:
        print(f"  扫描告警：{warning}")
    return scan


def _write_database(path: Path) -> int:
    """为 demo 的两个编译单元写一份 compile_commands.json。"""
    entries = []
    for source in sorted((DEMO / "app").glob("*.cpp")):
        entries.append(
            {
                "directory": str(DEMO),
                "file": str(source),
                "command": (
                    f"clang++ -std=c++17 -I{DEMO / 'include'} "
                    f"-c {source} -o {source}.o"
                ),
            }
        )
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return len(entries)


def main() -> int:
    from heavyheader.attrib.incclean import (
        analyse_database,
        compare_with_scan,
        find_binary,
    )

    OUT.mkdir(parents=True, exist_ok=True)

    binary = find_binary()
    if binary is None:
        print("找不到 clang-include-cleaner：本脚本必须端到端跑，不降级到样本。")
        return 2
    print(f"二进制：{binary}")

    print("扫描 demo 语料（--probe）…")
    scan = _scan_demo()
    print(f"候选：{len(scan.candidates)}")

    database = OUT / "compile_commands.json"
    units = _write_database(database)
    print(f"编译单元：{units}")

    print("逐单元调用 clang-include-cleaner…")
    records = analyse_database(database, binary=binary, jobs=1)
    for record in records:
        state = "分析成功" if record.analysed else f"未参与分析（{record.skip_reason or record.error}）"
        print(f"  {Path(record.source).name}: {state}")
        for edit in record.removes:
            print(f"    删 {edit.spelling} @Line:{edit.line}")
        for edit in record.inserts:
            print(f"    插 {edit.spelling}")

    comparison = compare_with_scan(records, scan)
    print()
    print("总览：")
    print(f"  参与分析的文件        {comparison.analysed_files}")
    print(f"  未参与分析的文件      {comparison.skipped_files}")
    print(f"  删除建议              {comparison.removals}")
    print(f"  两边一致              {comparison.agreement}")
    print(f"  分歧（我们判 KEEP）   {comparison.conflicts}")
    print(f"  我们独有              {comparison.our_unmatched}")

    (OUT / "comparison.json").write_text(
        json.dumps(comparison.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n详细结果：{OUT / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
