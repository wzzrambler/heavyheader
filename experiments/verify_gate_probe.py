"""逐条实测指定候选，并验证「疑似副作用强制过闸门」这条纪律。

为什么单独一个脚本：CLI 的 ``verify`` 按排名取前 N 条，验证一组**特定**候选
（失败分类里被标成保守漏报的那几条）时排不进来。这个脚本按判定类型从一次
新扫描里取人，再走同一条 ``verify_candidates`` 路径。

关键开关：默认 **关掉** ``--check-codegen``。疑似副作用的候选如果还能被测出
「有收益」，那一定是闸门被强制打开了——这正是要证明的事。

用法::

    python experiments/verify_gate_probe.py --corpus <dir> \\
        --compiler <clang> --only suspected
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import candidate_rank_key  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--only", default="suspected", choices=["suspected", "unknown", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--confirm", type=int, default=2)
    ap.add_argument("--gate", action="store_true", help="打开全局汇编闸门（默认关：验证强制逻辑）")
    ap.add_argument("--jobs", type=int, default=1)
    args = ap.parse_args()

    root = Path(args.corpus)
    db = Path(args.db) if args.db else root / "compile_commands.json"
    scan = scan_project(
        ScanOptions(
            root=root,
            compiler=args.compiler,
            compile_commands=db,
            max_tus=500,
            probe_compile=False,
            probe_jobs=args.jobs,
        )
    )
    pool = list(scan.candidates)
    if args.only == "suspected":
        pool = [c for c in pool if c.side_effect_suspected]
    elif args.only == "unknown":
        pool = [c for c in pool if c.usage_verdict == "unknown"]
    pool.sort(key=candidate_rank_key)
    if args.limit:
        pool = pool[: args.limit]
    if not pool:
        print("没有符合条件的候选")
        return 1
    # ``verify_candidates`` 自己按排名从 ``scan.candidates`` 取人，所以「只测这
    # 一组」的唯一办法是把扫描里的候选表换成这一组。
    scan.candidates = pool

    driver = detect_driver(args.compiler)
    # 走 scan 里记录过的选项；共享 fallback 只用于没有记录的情况。
    sample = next((t.compile_options for t in scan.ok_tus if t.compile_options), None)
    options = sample or DriverOptions()
    verify = VerifyOptions(
        top=len(pool),
        runs=args.runs,
        confirm_passes=args.confirm,
        codegen_check=args.gate,
        jobs=args.jobs,
    )
    print(f"候选 {len(pool)} 条；全局闸门 {'开' if args.gate else '关'}；jobs={args.jobs}")
    results = verify_candidates(scan, driver, options, verify=verify)
    print(f"\n{'单元':<14}{'头文件':<13}{'结论':<16}说明")
    for result in sorted(results, key=lambda r: (r.candidate.tu.name, r.candidate.header.name)):
        c = result.candidate
        print(
            f"{c.tu.name:<14}{c.header.name:<13}{result.status:<16}{result.message[:90]}"
        )
    improved = [r for r in results if r.status == "improved"]
    print(f"\nimproved {len(improved)}/{len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
