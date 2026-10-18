"""对指定候选按正式协议复测（给初筛里「差点过线」的那几条一个结论）

为什么需要它
------------
`g2_recall_off.py` 用阈值 2% 决定谁进入复测。阈值之下还有几条在 1.2%–1.6%
（json 的 `serializer.hpp -> iomanip` 1.246%、zlib 的 `zutil.h -> stdlib.h`
1.552%）：它们没过 2%，但也不是噪声量级。留一句「没测」在报告里，等于把
一个可测的问题留给读者。

本脚本按正式协议（``runs=3`` + ``confirm_passes=2``）测这几条，给确定结论。

用法::

    python experiments/g2_confirm_picks.py --project experiments/_work/zlib \
        --db experiments/_work/zlib/compile_commands.json --tus 15 \
        --compiler "C:/Program Files/LLVM/bin/clang.exe" \
        --pick zutil.h:stdlib.h
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
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402

TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=15)
    parser.add_argument("--compiler", default="C:/Program Files/LLVM/bin/clang++.exe")
    parser.add_argument(
        "--pick",
        action="append",
        required=True,
        help="要复测的候选，格式 site文件名:header文件名，可重复",
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--confirm", type=int, default=2)
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
    pool = find_header_candidates(scan, sizes, kinds, require_plausibly_unused=False)

    wanted = {tuple(item.split(":", 1)) for item in args.pick}
    picked = [
        c
        for c in pool
        if (Path(c.site).name, Path(c.header).name) in wanted
    ]
    missing = wanted - {(Path(c.site).name, Path(c.header).name) for c in picked}
    if missing:
        print(f"没在候选池里找到：{sorted(missing)}")
    if not picked:
        return 1

    saved = scan.candidates
    scan.candidates = picked
    started = time.perf_counter()
    try:
        results = verify_candidates(
            scan,
            driver,
            DriverOptions(extra_flags=list(TARGET_FLAGS)),
            verify=VerifyOptions(
                top=0, runs=args.runs, confirm_passes=args.confirm, jobs=4
            ),
        )
    finally:
        scan.candidates = saved
    elapsed = time.perf_counter() - started

    rows = []
    for result in results:
        rows.append(
            {
                "site": Path(result.candidate.site).name,
                "header": Path(result.candidate.header).name,
                "reach": len(result.candidate.affected_tus) or 1,
                "status": result.status,
                "percent_saved": None
                if result.percent_saved is None
                else round(result.percent_saved, 3),
                "baseline_ms": None
                if result.baseline_seconds is None
                else round(result.baseline_seconds * 1000, 1),
                "patched_ms": None
                if result.patched_seconds is None
                else round(result.patched_seconds * 1000, 1),
                "message": result.message[:200],
            }
        )
        print(f"{rows[-1]['site']} -> {rows[-1]['header']}: {rows[-1]['status']} "
              f"{rows[-1]['percent_saved']}%", flush=True)

    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "protocol": {"runs": args.runs, "confirm_passes": args.confirm},
        "seconds": round(elapsed, 1),
        "rows": rows,
    }
    out = ROOT / "experiments" / "results" / f"g2-confirm-picks-{project.name}-{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
