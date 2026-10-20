"""测量「补丁侧系统性变慢」到底是补丁还是测量协议造成的。

三个配置，交替跑，各取最小值：
  A 原文件（基线路径，verify 的 baselines 走这条）
  B 临时目录副本、**不改任何一行**（把「换了路径」这一个变量单独拎出来）
  C 临时目录副本、注释掉目标 include（补丁路径）

判定：
  * A≈B，C 明显慢  → 补丁本身让编译变慢（真实效应，得找出原因）
  * B≈C，A 明显快  → 慢的是「临时副本」这个测量装置，不是补丁
  * 三者一致        → 之前的 +40ms 是机器状态（并行争抢 / 缓存），复现不出来

加 --time-report 会把每次编译的 clang -ftime-report 落到同目录 jsonl，
用来分辨慢在哪个阶段（Front end / Backend / 总执行）。

用法::

    python experiments/probe_bias.py --project <dir> --tu llex.c --header lparser.h
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

_CLANG_FRONT_END = re.compile(
    r"^\s*([\d.]+)\s*\([\s\d.]+%\)\s+Front end\s*$", re.MULTILINE
)
_CLANG_TOTAL = re.compile(
    r"Total Execution Time:\s*([\d.]+)\s*seconds", re.MULTILINE
)


def comment_out(text: str, needle: str) -> str:
    out = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#include") and needle in line:
            indent = line[: len(line) - len(stripped)]
            out.append(f"{indent}// {stripped}")
        else:
            out.append(line)
    return "".join(out)


def phases(tu) -> tuple[float | None, float | None]:
    """从 TU 的 phase 行取 (Front end 秒, 总执行秒)。"""
    front = None
    total = None
    for row in tu.timings:
        name = row.name.strip().lower()
        if name == "front end":
            front = row.wall_seconds
        elif row.is_total:
            total = row.wall_seconds
    return front, total


def measure(driver, source: Path, options: DriverOptions, args):
    """跑 runs 次，返回 (最小墙钟 ms, 对应的前端秒, 对应的总执行秒)。"""
    wall: list[float] = []
    front_s: list[float] = []
    total_s: list[float] = []
    for _ in range(args.runs):
        tu = driver.run(source, options, cwd=None, timeout=300)
        if not tu.ok:
            raise SystemExit(f"编译失败：{source}\n{tu.error}")
        wall.append(tu_seconds(tu))
        front, total = phases(tu)
        if front is not None:
            front_s.append(front)
        if total is not None:
            total_s.append(total)
    best = min(range(len(wall)), key=lambda i: wall[i])
    return (
        wall[best] * 1000,
        front_s[best] if best < len(front_s) else None,
        total_s[best] if best < len(total_s) else None,
    )


def tu_seconds(tu) -> float:
    from heavyheader.models import tu_compile_seconds

    return tu_compile_seconds(tu)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--compiler", default="clang")
    ap.add_argument("--tu", required=True)
    ap.add_argument("--header", required=True)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    project = Path(args.project)
    db = Path(args.db) if args.db else project / "compile_commands.json"
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=args.compiler,
            compile_commands=db,
            max_tus=500,
            probe_compile=False,
            probe_jobs=1,
        )
    )
    driver = detect_driver(args.compiler)

    source = next(t.source for t in scan.ok_tus if t.source.name == args.tu)
    options: DriverOptions = source and next(  # type: ignore[assignment]
        t.compile_options for t in scan.ok_tus if t.source == source
    )
    print(f"单元 {source}")
    print(f"选项 {options}")
    text = source.read_text(encoding="utf-8", errors="replace")
    if args.header not in text:
        raise SystemExit(f"{args.tu} 里没有 {args.header}")

    with tempfile.TemporaryDirectory(prefix="heavyheader-probe-") as tmp:
        # 两个副本必须落不同目录：同目录同名会互相覆盖（第一次写这块时踩过）。
        (Path(tmp) / "same").mkdir()
        (Path(tmp) / "cut").mkdir()
        identical = Path(tmp) / "same" / source.name
        identical.write_text(text, encoding="utf-8")
        patched = Path(tmp) / "cut" / source.name
        patched.write_text(comment_out(text, args.header), encoding="utf-8")

        mirrored = DriverOptions(
            standard=options.standard,
            include_dirs=[*options.include_dirs, source.parent],
            defines=list(options.defines),
            extra_flags=list(options.extra_flags),
            full_compile=options.full_compile,
        )

        rows = {"A_original": [], "B_copy_identical": [], "C_copy_patched": []}
        for _ in range(args.runs):
            rows["A_original"].append(measure(driver, source, options, args))
            rows["B_copy_identical"].append(measure(driver, identical, mirrored, args))
            rows["C_copy_patched"].append(measure(driver, patched, mirrored, args))

        print(f"\n{'配置':<18}{'墙钟 ms':>10}{'FrontEnd s':>12}{'总执行 s':>11}")
        for name, samples in rows.items():
            best = min(samples, key=lambda s: s[0])
            print(f"{name:<18}{best[0]:>10.1f}{best[1] or 0:>12.4f}{best[2] or 0:>11.4f}")
        a = min(s[0] for s in rows["A_original"])
        b = min(s[0] for s in rows["B_copy_identical"])
        c = min(s[0] for s in rows["C_copy_patched"])
        print(f"\nB-A = {b - a:+.1f} ms（换路径的代价）")
        print(f"C-B = {c - b:+.1f} ms（补丁本身的代价）")
        print(f"C-A = {c - a:+.1f} ms（verify 实际看到的差）")
        print(f"A 中位数 {statistics.median(s[0] for s in rows['A_original']):.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
