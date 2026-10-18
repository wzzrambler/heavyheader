"""G2 补充：闸门交白卷时，工具到底说了什么？

背景
----
G2 在三个工程上的结果：doctest 1/5，nlohmann/json 0/5，zlib 0/5。
后两个工程的产品表现是**一张空的候选表**，而空表会被读成「构建没问题」——
真实情况是每条都被判了「删了编不过」。

本脚本把「空表」这一结果本身测出来：对每个工程记录

* 成本集中在哪些头文件、这些头文件是不是项目自己的；
* 收益最高的那几条被拦下的原因（具体到标识符）；
* 拦下原因的分布；
* 报告给出的下一步建议。

这不是优化闭环的证据，是**负面结果可解释**的证据：0 条候选时工具仍然
给出了可核对的结论，而不是沉默。

用法::

    python experiments/g2_diagnosis.py --project experiments/_work/json \
        --db experiments/_work/compile_commands.json --tus 25
    python experiments/g2_diagnosis.py --project experiments/_work/zlib \
        --db experiments/_work/zlib/compile_commands.json --tus 15 \
        --compiler "C:/Program Files/LLVM/bin/clang.exe"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.diagnosis import BLOCK_REASONS  # noqa: E402
from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.report.markdown import render_markdown  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def _short(path: Path, root: Path, width: int = 52) -> str:
    """控制台打印用：系统头的绝对路径很长，截断会把不同文件显示成同一行。"""
    rel = _rel(path, root)
    if len(rel) <= width:
        return rel
    return "…" + rel[-(width - 1) :]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=25)
    parser.add_argument("--compiler", default=CLANG)
    parser.add_argument("--top", type=int, default=10)
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
            header_sites=True,
        ),
        driver=driver,
    )
    if not scan.ok_tus:
        print("扫描没有成功单元")
        return 1

    diagnosis = scan.site_diagnosis
    if diagnosis is None:  # pragma: no cover - 上一行开了 header_sites
        print("没有生成诊断")
        return 1

    header_site_candidates = [c for c in scan.candidates if c.is_header_site]
    print(f"工程：{project.name}  TU：{len(scan.ok_tus)}")
    print(
        f"头文件级候选：{len(header_site_candidates)} 条；"
        f"诊断记录：放行 {diagnosis.candidate_count} 条 / 拦下 "
        f"{sum(diagnosis.counts.values())} 条"
    )
    print()
    print("成本集中 Top %d：" % len(diagnosis.concentration))
    for row in diagnosis.concentration:
        print(
            f"  {_short(row.path, project):52s} {row.kind.value:8s} "
            f"{row.attributed_lines:10,d} 行  {row.share * 100:5.1f}%"
        )
    print(
        f"  → 前 {len(diagnosis.concentration)} 个占 {diagnosis.top_share_percent}%，"
        f"项目自有头占 {diagnosis.project_share_percent}%"
    )
    print()
    print("收益最高但被拦下的：")
    for item in diagnosis.blocked_top:
        where = _rel(item.site_file, project)
        if item.line:
            where = f"{where}:{item.line}"
        print(
            f"  {_short(item.site_file, project, 44):44s} -> "
            f"{_short(item.header, project, 22):22s} "
            f"amplified={item.amplified_lines:9,d} reach={item.reach:3d} "
            f"{item.reason}"
        )
        if item.detail:
            print(f"      证据：{item.detail}")
    print()
    print(
        "拦下原因分布："
        + ", ".join(
            f"{BLOCK_REASONS[reason]} {count} 条"
            for reason, count in sorted(diagnosis.counts.items())
        )
    )
    print()
    print("下一步建议：")
    for item in diagnosis.advice:
        print(f"  - {item}")

    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "compiler": args.compiler,
        "tus": len(scan.ok_tus),
        "header_site_candidates": len(header_site_candidates),
        "candidate_count": diagnosis.candidate_count,
        "blocked_count": sum(diagnosis.counts.values()),
        "total_attributed_lines": diagnosis.total_attributed_lines,
        "project_share_percent": diagnosis.project_share_percent,
        "top_share_percent": diagnosis.top_share_percent,
        "concentration": [
            {
                "path": _rel(row.path, project),
                "kind": row.kind.value,
                "attributed_lines": row.attributed_lines,
                "share_percent": round(row.share * 100, 2),
            }
            for row in diagnosis.concentration
        ],
        "blocked": [
            {
                "site": _rel(item.site_file, project),
                "line": item.line,
                "header": _rel(item.header, project),
                "amplified_lines": item.amplified_lines,
                "reach": item.reach,
                "reason": item.reason,
                "detail": item.detail,
            }
            for item in diagnosis.blocked_top
        ],
        "reason_counts": dict(diagnosis.counts),
        "advice": list(diagnosis.advice),
    }
    results = ROOT / "experiments" / "results"
    results.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat().replace("-", "")
    out_json = results / f"g2-diagnosis-{project.name}-{stamp}.json"
    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# G2 补充：闸门交白卷时给了什么结论（{date.today().isoformat()}，{project.name}）",
        "",
        f"- 工程：{project.name}，{len(scan.ok_tus)} 个 TU，编译器 `{args.compiler}`。",
        f"- 头文件级候选：**{len(header_site_candidates)} 条**"
        f"（放行 {diagnosis.candidate_count} / 拦下 {sum(diagnosis.counts.values())}）。",
        f"- 全项目解析量：{diagnosis.total_attributed_lines:,} 行。",
        "",
        "## 成本集中在哪",
        "",
        "| 头文件 | 归属 | 归因行数 | 占比 |",
        "|---|---|---:|---:|",
    ]
    for row in diagnosis.concentration:
        lines.append(
            f"| `{_rel(row.path, project)}` | {row.kind.value} "
            f"| {row.attributed_lines:,} | {row.share * 100:.1f}% |"
        )
    lines += [
        "",
        f"前 {len(diagnosis.concentration)} 个头文件占 **{diagnosis.top_share_percent}%**；"
        f"项目自有头文件占 **{diagnosis.project_share_percent}%**。",
        "",
        "## 收益最高但被拦下的",
        "",
        "| 位置 | include | 放大后行数 | 影响 TU | 原因 | 证据 |",
        "|---|---|---:|---:|---|---|",
    ]
    for item in diagnosis.blocked_top:
        where = _rel(item.site_file, project)
        if item.line:
            where = f"{where}:{item.line}"
        lines.append(
            f"| `{where}` | `{_rel(item.header, project)}` "
            f"| {item.amplified_lines:,} | {item.reach} "
            f"| {item.reason} | {item.detail or '—'} |"
        )
    lines += ["", "## 下一步建议", ""]
    lines += [f"- {item}" for item in diagnosis.advice]
    out_md = results / f"g2-diagnosis-{project.name}-{stamp}.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 报告里那一节是否真的渲染出来——脚本顺手验一下，免得实验数据和产品
    # 输出是两套东西。
    rendered = render_markdown(scan)
    has_section = ("没有可删的了" in rendered) or ("成本去向" in rendered)
    print()
    print(f"报告渲染该小节：{'是' if has_section else '否'}")
    print(f"结果写入：{out_json.name} / {out_md.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
