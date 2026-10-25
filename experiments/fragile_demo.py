#!/usr/bin/env python
"""生成 fragile-demo：一个「部分覆盖」的合成语料，用来暴露 PCH 选择的缺陷。

形状（对着 ``experiments/results/pch-greedy-selection-20261024.md`` 校准）：

* 10 个编译单元，**全部**包含 ``include/common.h``（1,503 行）；
* 其中 5 个额外包含 ``include/special.h``（1,503 行，与 common.h 等大）。

因此 ``common.h`` 触达 10 个单元、``special.h`` 只触达 5 个。按
``(reach, 闭包行数)`` 取前 ``top_k=2`` 会把 special.h 也放进 PCH，闭包翻倍、
eligible 从 10 腰斩到 5，净节省从 13,527 行降到 12,024 行；按净收益贪心则
只选 common.h。这个语料就是用来把那 1,503 行的差量测出来的。

用法::

    python experiments/fragile_demo.py [目标目录]

默认写到 ``experiments/_work/fragile-demo``（该目录在 .gitignore 里）。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 两个头文件的行数。刻意取等大：否则「谁更大」会成为混淆变量，
#: 而这里要隔离出来的变量只有「被多少个单元包含」。
HEADER_LINES = 1503
#: 总编译单元数。
UNIT_COUNT = 10
#: 其中多少个额外包含 special.h。
SPECIAL_UNITS = 5


def generate(root: Path) -> dict:
    """在 ``root`` 下生成语料，返回元数据（供调用方记录）。"""
    include = root / "include"
    units = root / "units"
    include.mkdir(parents=True, exist_ok=True)
    units.mkdir(parents=True, exist_ok=True)

    def write_header(name: str) -> Path:
        path = include / name
        stem = name.split(".")[0]
        body = ["#pragma once", "namespace demo {"]
        body += [
            f"inline int f_{stem}_{i}(int x) {{ return x + {i}; }}" for i in range(HEADER_LINES)
        ]
        body.append("}")
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        return path

    write_header("common.h")
    write_header("special.h")

    for i in range(UNIT_COUNT):
        lines = ['#include "common.h"']
        if i < SPECIAL_UNITS:
            lines.append('#include "special.h"')
        lines.append("#include <cstdio>")
        lines.append("int main() {")
        lines.append("  long long acc = 0;")
        # 每个单元自己也做一点工作，让逐单元墙钟不为零（维护税要用）。
        for k in range(200):
            lines.append(f"  acc += demo::f_common_{(i * 7 + k) % HEADER_LINES}({k});")
        if i < SPECIAL_UNITS:
            for k in range(200):
                lines.append(f"  acc += demo::f_special_{(i * 5 + k) % HEADER_LINES}({k});")
        lines += ['  printf("%lld\\n", acc);', "  return 0;", "}"]
        (units / f"u{i:02d}.cpp").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {
        "root": str(root),
        "units": UNIT_COUNT,
        "headers": {
            "common.h": {"lines": HEADER_LINES, "reach": UNIT_COUNT},
            "special.h": {"lines": HEADER_LINES, "reach": SPECIAL_UNITS},
        },
    }


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("experiments/_work/fragile-demo")
    meta = generate(target.resolve())
    print(f"written: {meta['root']}")
    print(f"  {meta['units']} 个编译单元；common.h 触达 {UNIT_COUNT}，"
          f"special.h 触达 {SPECIAL_UNITS}")
    print("\n扫描命令：")
    print(f"  heavyheader scan {meta['root']} --pch --pch-top 5 --jobs 8 "
          "--output fragile.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
