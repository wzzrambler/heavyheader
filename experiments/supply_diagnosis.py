"""供给侧漏报诊断：真能删的候选，是被**哪一条判据**挡住的？

为什么要先做诊断：§9.19.7 发现「8 条落地收益里 7 条不在推荐集」，于是有了
「放宽供给侧」的念头。但「放宽」是个危险的词——推荐层现在三语料零假阳性，
放宽判据最容易的成果是把它一起放掉。所以在动判据之前，先量清楚：

    verdict 不是 plausibly_unused、而真值确实是「删了编得过且少解析了文件」
    的那些候选，各自的 ``usage_reason`` 是什么？

答案决定了哪一条判据值得改、以及改了之后要拿什么证据兜底（副作用靠
``--check-codegen`` 的汇编闸门，缺声明靠编译本身）。

用法::

    python experiments/supply_diagnosis.py \\
        --precision-json experiments/results/candidate-precision-lua-20261020k.json \\
        --project %TEMP%/upstream/lua-5.4.7 \\
        --db %TEMP%/upstream/lua-5.4.7/compile_commands.json \\
        --compiler clang --tus 34 \\
        --out experiments/results/supply-diagnosis-lua-20261020.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

#: ``usage_reason`` 的前缀就是判据的名字，取冒号/逗号之前那一段做归类。
_PREFIX = re.compile(r"^\s*([^:,，、]{2,12})")


def bucket(reason: str) -> str:
    """把一句话理由归到判据类别上（归类只为统计，原文仍然落盘）。"""
    if not reason:
        return "(无理由)"
    match = _PREFIX.match(reason)
    head = match.group(1).strip() if match else reason[:12]
    for key in (
        "副作用",
        "疑似副作用",
        "仅 #ifdef",
        "条件编译",
        "宏体",
        "拼接",
        "实现片段",
        "转发",
        "环境噪音",
        "另有供给",
        "自供给",
    ):
        if key in head or key in reason[:20]:
            return key
    return head


def run(args) -> dict:
    precision = json.loads(args.precision_json.read_text(encoding="utf-8"))
    truth = {
        (row["tu"].lower(), Path(row["header"]).name, row["line"]): row
        for row in precision["rows_by_rank"]
    }
    scan = scan_project(
        ScanOptions(
            root=args.project,
            compiler=args.compiler,
            compile_commands=args.db,
            max_tus=args.tus or 500,
            probe_compile=True,
            probe_top=0,
            probe_jobs=args.jobs,
        )
    )

    rows: list[dict] = []
    for candidate in scan.candidates:
        key = (
            str(candidate.tu).lower(),
            Path(candidate.header).name,
            candidate.line,
        )
        row = truth.get(key)
        if row is None:
            continue
        rows.append(
            {
                "tu": Path(candidate.tu).name,
                "header": Path(candidate.header).name,
                "line": candidate.line,
                "verdict": candidate.usage_verdict,
                "reason": candidate.usage_reason,
                "bucket": bucket(candidate.usage_reason),
                "truth_useful": bool(row.get("useful")),
                "truth_compiles": bool(row.get("truth_compiles")),
                "parse_shrink": bool(row.get("parse_shrink")),
                "silent_change": bool(row.get("silent_change")),
                "lines_saved": row.get("lines_saved"),
                "estimate_s": (
                    None
                    if candidate.estimated_seconds_saved is None
                    else round(candidate.estimated_seconds_saved, 5)
                ),
            }
        )

    leaked = [r for r in rows if r["truth_useful"] and r["verdict"] != "plausibly_unused"]
    by_bucket = Counter(r["bucket"] for r in leaked)
    by_verdict = Counter(r["verdict"] for r in leaked)
    recommended = [
        r for r in rows if r["verdict"] == "plausibly_unused"
    ]
    return {
        "project": str(args.project),
        "candidates_matched": len(rows),
        "truth_useful": sum(1 for r in rows if r["truth_useful"]),
        "recommended": len(recommended),
        "recommended_useful": sum(1 for r in recommended if r["truth_useful"]),
        "leaked": len(leaked),
        "leaked_by_verdict": dict(by_verdict),
        "leaked_by_bucket": dict(by_bucket.most_common()),
        "leaked_rows": sorted(
            leaked,
            key=lambda r: -(r["estimate_s"] or 0.0),
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precision-json", type=Path, required=True)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--tus", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"匹配候选 {report['candidates_matched']} 条；真值可删 "
          f"{report['truth_useful']} 条；推荐 {report['recommended']} 条"
          f"（其中真可删 {report['recommended_useful']}）")
    print(f"**供给侧漏报 {report['leaked']} 条** → 判定分布 "
          f"{report['leaked_by_verdict']}")
    print("阻塞原因分布（前 12）:")
    for name, count in list(report["leaked_by_bucket"].items())[:12]:
        print(f"  {count:4d}  {name}")
    print("\n漏报里预估收益最大的 12 条:")
    for row in report["leaked_rows"][:12]:
        est = row["estimate_s"]
        text = "—" if est is None else f"{est * 1000:.1f} ms"
        print(f"  {row['tu']:14}-> {row['header']:14} {row['verdict']:16} "
              f"{text:>9}  {row['reason'][:46]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
