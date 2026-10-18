"""G2 前置探测：现有候选池里，到底有没有「看起来能删」的条目？

G2 判定为不成立时留下一个未回答的问题：tool 臂 10 条里 7 条是 ``json.hpp``
（每个测试 TU 都要它）、3 条是 ``<iostream>``（省 1.0–1.9%，压在噪声线下）。
这到底是**排序错了**，还是**这个工程的候选池里本来就没有可采纳的改动**？

两种可能对应完全不同的修法：排序错 → 改排序即可；池里没有 → 换工程或换
候选定义。在没有数据之前争论该修哪个，只是换一种方式浪费编译预算。

所以这个脚本不做任何改动，只把候选池摊开来看：

* 每个 direct include 的用法判定分布；
* 判定为「疑似未使用」的条目按预估收益排序，看有几条；
* 判定为 unknown 的条目里，最大的那些是谁，为什么 unknown。

输出直接打印，不写文件：这是探索，不是证据。
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from g2_loop import build_scan  # noqa: E402

from heavyheader.models import candidate_rank_key  # noqa: E402


def main() -> int:
    scan = build_scan()
    print(f"TU: {len(scan.ok_tus)}  候选池: {len(scan.candidates)}")

    verdicts = collections.Counter(c.usage_verdict or "?" for c in scan.candidates)
    print("用法判定分布:", dict(verdicts))

    print("\n-- 按当前排序的前 15 条 --")
    print(f"{'TU':34s} {'header':28s} {'verdict':16s} {'est%':>6s} {'est_s':>8s}")
    for c in sorted(scan.candidates, key=candidate_rank_key)[:15]:
        print(
            f"{Path(c.tu).name[:34]:34s} {Path(c.header).name[:28]:28s} "
            f"{(c.usage_verdict or '?')[:16]:16s} {c.estimated_percent:6.2f} "
            f"{(c.estimated_seconds_saved if c.estimated_seconds_saved is not None else -1):8.4f}"
        )

    plausibly = [c for c in scan.candidates if c.usage_verdict == "plausibly_unused"]
    print(f"\n-- plausibly_unused 共 {len(plausibly)} 条，按预估收益排序 --")
    for c in sorted(plausibly, key=lambda c: -c.estimated_percent):
        print(
            f"{Path(c.tu).name[:34]:34s} {Path(c.header).name[:28]:28s} "
            f"{c.estimated_percent:6.2f}%  lines={c.estimated_lines_saved}"
        )

    unknown = [c for c in scan.candidates if c.usage_verdict == "unknown"]
    print(f"\n-- unknown 共 {len(unknown)} 条，按预估收益前 10 --")
    for c in sorted(unknown, key=lambda c: -c.estimated_percent)[:10]:
        print(
            f"{Path(c.tu).name[:34]:34s} {Path(c.header).name[:28]:28s} "
            f"{c.estimated_percent:6.2f}%  lines={c.estimated_lines_saved} "
            f"exclusive={len(c.exclusive_headers)}"
        )

    # 每个 TU 的 direct include 全量：看看「删掉还能编译」的余地有多大
    print("\n-- 每个 TU 的 direct include 数 / 疑似未使用数 --")
    per_tu = collections.Counter(Path(c.tu).name for c in scan.candidates)
    unused_tu = collections.Counter(
        Path(c.tu).name for c in scan.candidates if c.usage_verdict == "plausibly_unused"
    )
    for tu in sorted(per_tu):
        print(f"{tu[:44]:44s} {per_tu[tu]:3d} / {unused_tu.get(tu, 0)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
