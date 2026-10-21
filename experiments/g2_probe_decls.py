"""声明级口径 + 下游证据之后，doctest 的闸门候选实测还能编过几条？

背景
----
改口径前的实测（``experiments/results/g2-sites-doctest-all16-20261018.json``）：
16 条闸门候选里 8 条编译失败，报错符号全部写在下游文件里。改口径之后：

1. 「这个头声明了什么」按声明作用域取（``attrib/decls.py``）；
2. 每条候选带下游引用证据（``downstream_files``），置信度据此压到 low。

本脚本对改口径后的**全部**闸门候选跑编译探测（只回答「删了还能编吗」，
不测时间），输出逐条状态，用于更新 §9.3b 的通过率数字。

用法::

    python experiments/g2_probe_decls.py --project experiments/_work/doctest \
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

from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers  # noqa: E402
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
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
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--tag", default="")
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
    gated = find_header_candidates(scan, sizes, kinds)
    print(f"闸门候选 {len(gated)} 条")

    fallback = DriverOptions(
        standard="c++17",
        include_dirs=[project / "include"],
        extra_flags=list(TARGET_FLAGS),
    )
    saved = scan.candidates
    scan.candidates = list(gated)
    try:
        results = verify_candidates(
            scan,
            driver,
            fallback,
            verify=VerifyOptions(top=len(gated), runs=1, jobs=args.jobs, probe_only=True),
        )
    finally:
        scan.candidates = saved

    rows = []
    for r in results:
        c = r.candidate
        rows.append(
            {
                "site": str(c.site),
                "header": str(c.header),
                "reach": c.reach,
                "downstream": len(c.downstream_files),
                "downstream_names": list(c.downstream_names[:4]),
                "status": r.status,
                "message": r.message[:160],
            }
        )
    ok = sum(1 for r in rows if r["status"] == "compiles")
    bad = sum(1 for r in rows if r["status"] == "compile-error")
    print(f"编译通过 {ok} / 失败 {bad} / 其它 {len(rows) - ok - bad}")
    for r in rows:
        print(f"  {Path(r['site']).name:16s} -> {Path(r['header']).name:20s} "
              f"下游{r['downstream']:3d}  {r['status']}")

    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "tus": len(scan.ok_tus),
        "gated": len(gated),
        "compiled": ok,
        "compile_error": bad,
        "rows": rows,
    }
    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{args.tag}" if args.tag else ""
    stem = out_dir / f"g2-probe-decls-{project.name}{suffix}-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    lines = [
        f"# 声明级口径后的编译探测（{payload['date']}，{payload['project']}）",
        "",
        f"- 闸门候选 {len(gated)} 条：编译通过 {ok}，失败 {bad}。",
        "",
        "| 改动位置 | include | 下游引用 | 探测结果 |",
        "|---|---|---:|---|",
        *[
            f"| `{Path(r['site']).name}` | `{Path(r['header']).name}` | {r['downstream']} | {r['status']} |"
            for r in rows
        ],
        "",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
