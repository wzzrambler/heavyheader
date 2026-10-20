"""实现片段站点化之后，四个语料的回归对照。

为什么单独跑一遍：「同一头被包含多次」这条分支一改，影响的不只是 brotli。
以前这类候选被判成「行号歧义」（一条、估算按整个闭包、从未实测），现在分成
两种：无 guard 的展开成 N 条**实例化站点**，带 guard 的明确标成「不是机会、
收益按 0 计」。这条改动必须证明它没有把别的语料的推荐层搞乱。

用法::

    python experiments/instantiation_regression.py --corpus lua \\
        --out experiments/results/instantiation-regression-lua-20261020.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

CORPORA = {
    "lua": (
        "%TEMP%/upstream/lua-5.4.7",
        "%TEMP%/upstream/lua-5.4.7/compile_commands.json",
        "C:/Program Files/LLVM/bin/clang.exe",
    ),
    "doctest": (
        "%TEMP%/upstream/doctest-pristine",
        "%TEMP%/upstream/doctest-pristine/build/compile_commands.json",
        "C:/Program Files/LLVM/bin/clang++.exe",
    ),
    "zlib": (
        "%TEMP%/upstream/zlib-develop",
        "%TEMP%/upstream/zlib-develop/build/compile_commands.json",
        "C:/Program Files/LLVM/bin/clang.exe",
    ),
    "zstd": (
        "%TEMP%/zstd-1.5.6",
        "%TEMP%/zstd-1.5.6/compile_commands.json",
        "C:/Program Files/LLVM/bin/clang.exe",
    ),
    "brotli": (
        "%TEMP%/brotli-1.1.0",
        "%TEMP%/brotli-1.1.0/compile_commands.json",
        "C:/Program Files/LLVM/bin/clang.exe",
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, choices=sorted(CORPORA))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root_spec, db_spec, compiler = CORPORA[args.corpus]
    root = Path(__import__("os").path.expandvars(root_spec))
    db = Path(__import__("os").path.expandvars(db_spec))
    scan = scan_project(
        ScanOptions(
            root=root,
            compiler=compiler,
            compile_commands=db,
            max_tus=500,
            probe_compile=False,
        )
    )
    candidates = list(scan.candidates)
    recommended = [c for c in candidates if c.usage_verdict == "plausibly_unused"]
    unknown = [c for c in candidates if c.usage_verdict == "unknown"]
    in_use = [c for c in candidates if c.usage_verdict == "in_use"]
    instantiation = [c for c in candidates if c.instantiation]
    zeroed = [
        c
        for c in candidates
        if c.estimated_lines_saved == 0 and "不是优化机会" in " ".join(c.notes)
    ]
    payload = {
        "corpus": args.corpus,
        "root": str(root),
        "compiler": compiler,
        "translation_units": len(scan.ok_tus),
        "failed_translation_units": len(scan.failed_tus),
        "baseline_seconds": scan.baseline_seconds,
        "counts": {
            "candidates": len(candidates),
            "plausibly_unused": len(recommended),
            "unknown": len(unknown),
            "in_use": len(in_use),
            "instantiation_sites": len(instantiation),
            "guarded_repeat_zeroed": len(zeroed),
        },
        "recommended": [
            {
                "tu": Path(c.tu).name,
                "header": Path(c.header).name,
                "line": c.line,
                "estimated_lines_saved": c.estimated_lines_saved,
            }
            for c in recommended
        ],
        "instantiation_sites": [
            {"tu": Path(c.tu).name, "header": Path(c.header).name, "line": c.line}
            for c in instantiation
        ],
        "guarded_repeat": [
            {"tu": Path(c.tu).name, "header": Path(c.header).name}
            for c in zeroed
        ],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"已写出 {out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
