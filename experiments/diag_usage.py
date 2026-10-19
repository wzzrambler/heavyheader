"""诊断：把每条候选的 usage 判定证据逐条打印出来。

精度基准只告诉我们「判错了没有」，不告诉我们「为什么判错」。这个脚本把
``analyse_usage`` 的内部证据（具体命中 / 过于普遍的命中 / 另有供给的命中）
抓出来，用来定位保守误判（判 in_use、实际可删）的根因。

用法::

    python experiments/diag_usage.py --project <dir> --db <compile_commands.json> \
        --compiler clang++ --tus 35
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heavyheader.attrib import graph as _graph  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

_CAPTURED: dict[tuple[str, str], object] = {}
_ORIGINAL = _graph.analyse_usage


def _wrapped(*args, **kwargs):
    signal = _ORIGINAL(*args, **kwargs)
    source = str(args[0]) if args else str(kwargs.get("source"))
    header = str(args[1]) if len(args) > 1 else str(kwargs.get("header"))
    _CAPTURED[(source.lower(), header.lower())] = signal
    return signal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", default=None)
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--tus", type=int, default=0)
    parser.add_argument("--only", default=None, help="只打印源码文件名包含该串的候选")
    args = parser.parse_args(argv)

    _graph.analyse_usage = _wrapped  # type: ignore[assignment]
    try:
        scan = scan_project(
            ScanOptions(
                root=Path(args.project),
                compiler=args.compiler,
                compile_commands=Path(args.db) if args.db else None,
                max_tus=args.tus or 500,
            )
        )
    finally:
        _graph.analyse_usage = _ORIGINAL  # type: ignore[assignment]

    for cand in scan.candidates:
        if args.only and args.only.lower() not in str(cand.tu).lower():
            continue
        signal = _CAPTURED.get((str(cand.tu).lower(), str(cand.header).lower()))
        if signal is None:
            continue
        print(
            f"{Path(cand.tu).name} -> {Path(cand.header).name}  "
            f"[{signal.verdict.value}]  lines={cand.estimated_lines_saved}"
        )
        if signal.shared_identifiers:
            print(f"    具体命中: {', '.join(signal.shared_identifiers)}"
                  f"   (由 {Path(signal.provided_by).name if signal.provided_by else '?'})")
        if signal.ambient_identifiers:
            print(f"    过于普遍: {', '.join(signal.ambient_identifiers)}")
        if signal.cross_provided:
            print(f"    另有供给: {', '.join(signal.cross_provided)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
