"""同一份代码、不同的构建配置：候选表该不该跟着变？

这是 §9.15 结论的直接检验。判定层从 `compile_commands.json` 逐单元取 `-D`
并按它求值 `#ifdef`，那么「同一行 include 在配置 A 里可删、在配置 B 里删了就编
不过」就该在候选表上显形。本脚本把同一棵源码树按多套配置各扫一遍，按
`(单元, 头文件, 行号)` 对齐，输出**判定矩阵**。

用法::

    python experiments/config_matrix.py --corpus <dir> --compiler <clang> \\
        --configs "single:" "mt:-DZSTD_MULTITHREAD" \
                  "mt_legacy:-DZSTD_MULTITHREAD -DZSTD_LEGACY_SUPPORT=1"

冒号前是配置名，冒号后是追加的编译参数（空格分隔）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

INCLUDES = [
    "lib",
    "lib/common",
    "lib/compress",
    "lib/decompress",
    "lib/dictBuilder",
    "lib/deprecated",
]


def write_db(root: Path, extra: str, out: Path) -> int:
    flags = " ".join(f"-I{root / p}" for p in INCLUDES)
    entries = []
    for src in sorted(root.glob("lib/*/*.c")):
        entries.append(
            {
                "directory": str(root),
                "command": (
                    f'"{args_compiler}" {flags} {extra} -O1 -c {src}'
                ).strip(),
                "file": str(src),
            }
        )
    out.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return len(entries)


def main() -> int:
    global args_compiler
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--configs", nargs="+", required=True, help='"名字:额外参数"')
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    args_compiler = args.compiler

    root = Path(args.corpus)
    tables: dict[str, dict[tuple[str, str, int | None], dict]] = {}
    summaries: dict[str, dict] = {}

    for spec in args.configs:
        name, _, extra = spec.partition(":")
        db = root / f"compile_commands.{name}.json"
        count = write_db(root, extra, db)
        scan = scan_project(
            ScanOptions(
                root=root,
                compiler=args.compiler,
                compile_commands=db,
                max_tus=500,
                probe_compile=False,
                probe_jobs=2,
            )
        )
        table = {}
        for c in scan.candidates:
            key = (str(c.tu).lower(), c.header.name, c.line)
            table[key] = {
                "tu": c.tu.name,
                "header": c.header.name,
                "verdict": c.usage_verdict,
                "seconds": c.estimated_seconds_saved,
                "reason": c.usage_reason,
            }
        tables[name] = table
        verdicts: dict[str, int] = {}
        for row in table.values():
            verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
        summaries[name] = {
            "command_count": count,
            "tu": len(scan.ok_tus),
            "failed": len(scan.failed_tus),
            "baseline_seconds": scan.baseline_seconds,
            "candidates": len(table),
            "verdicts": verdicts,
        }
        print(
            f"[{name:<10}] TU {len(scan.ok_tus):>3} 失败 {len(scan.failed_tus):>2} "
            f"候选 {len(table):>3} 基线 {scan.baseline_seconds:6.1f}s  {verdicts}"
        )

    names = list(tables)
    if len(names) >= 2:
        base = names[0]
        print(f"\n以 [{base}] 为基准的判定变化：")
        for other in names[1:]:
            changed = []
            for key, row in tables[base].items():
                peer = tables[other].get(key)
                if peer is None:
                    changed.append((row, None))
                elif peer["verdict"] != row["verdict"]:
                    changed.append((row, peer))
            print(f"  {base} → {other}：{len(changed)} 条判定不同")
            for row, peer in changed:
                was = row["verdict"]
                now = peer["verdict"] if peer else "（不再出现）"
                print(
                    f"    {row['tu']:<26}{row['header']:<18}{was:<18}→ {now}"
                )

    payload = {"summaries": summaries, "tables": {
        name: list(table.values()) for name, table in tables.items()
    }}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
