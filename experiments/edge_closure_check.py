#!/usr/bin/env python3
"""边级删除收益的**结构性**验证：预测的闭包 vs 真的消失的文件。

为什么不用墙钟：单条标准头的解析时间（十几毫秒）远小于测量噪声（24 TU 尺度
上 ±250ms），计时法无法判定。但「删掉这条 include 后哪些文件不再被解析」是
确定性的集合，用真编译的 ``-H`` 前后对比能精确得到——不受噪声影响。

被测的两个预测器：

* **A 有效树闭包**：只用 ``-H`` 给的有效包含树做可达性。``-H`` 只记录每个头
  **首次生效**的包含点，因此看不到「删了这条，别处还会把它拉进来」。
* **B 潜在供给闭包**：把树上所有文件的 ``#include`` 行文本扫描一遍，按文件名
  补全潜在供给边，再做可达性——即考虑被 include guard 抑制掉的替代包含点。

指标：预测闭包与真值（真编译后消失的文件集合）是否完全一致；以及「预测说会
省、实际没省」的假阳性文件数——这是最危险的错误方向。

用法：
    python experiments/edge_closure_check.py \\
        --db build/compile_commands.json --tus 24 --sample 4
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heavyheader.parse.includetree import parse_include_tree  # noqa: E402
from heavyheader.scanner import _split_command, _strip_output_flag  # noqa: E402

PRELUDE_REL = Path("doctest/parts/private/prelude.h")
INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]")


def compile_tree(
    entry: dict, workdir: Path, tag: str
) -> tuple[bool, dict[str, Path], dict[str, set[str]]]:
    """编译一次，返回 (成功, 文件名→路径, 有效包含树)。"""
    argv = _strip_output_flag(_split_command(entry["command"]))
    obj = workdir / f"{tag}.o"
    argv += ["-H", "-o", str(obj)]
    proc = subprocess.run(
        argv,
        cwd=entry["directory"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return False, {}, {}
    events = parse_include_tree(proc.stderr, Path(entry["file"]))
    files: dict[str, Path] = {Path(entry["file"]).name: Path(entry["file"])}
    graph: dict[str, set[str]] = {}
    for ev in events:
        files[ev.includer.name] = ev.includer
        files[ev.included.name] = ev.included
        graph.setdefault(ev.includer.name, set()).add(ev.included.name)
        graph.setdefault(ev.included.name, set())
    return True, files, graph


def reachable(
    graph: dict[str, set[str]], root: str, skip: tuple[str, str] | None
) -> set[str]:
    seen = {root}
    stack = [root]
    while stack:
        node = stack.pop()
        for nxt in graph.get(node, ()):
            if skip is not None and (node, nxt) == skip:
                continue
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def potential_graph(
    files: dict[str, Path], effective: dict[str, set[str]]
) -> dict[str, set[str]]:
    """在「已解析文件」范围内，按文本 include 行补全潜在供给边。

    只认 basename 匹配：宁可靠**多算**供给边让闭包偏小（预测保守），也不能凭空
    造边让预测显得省得多。文本里看不到的关系（如 ``#include_next``）同样看不见，
    这是本方法已知的下界。
    """
    graph: dict[str, set[str]] = {name: set(v) for name, v in effective.items()}
    for name, path in files.items():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = INCLUDE_RE.match(line)
            if not m:
                continue
            target = Path(m.group(1)).name
            if target in files:
                graph.setdefault(name, set()).add(target)
                graph.setdefault(target, set())
    return graph


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--tus", type=int, default=24)
    ap.add_argument("--sample", type=int, default=4, help="用几个 TU 做真值（取并集）")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    project = args.project.resolve()
    prelude = project / PRELUDE_REL
    entries = json.loads(args.db.read_text(encoding="utf-8"))[: args.tus]
    sample = entries[: args.sample]

    original_text = prelude.read_text(encoding="utf-8")
    headers = [
        m.group(1) for line in original_text.splitlines() if (m := INCLUDE_RE.match(line))
    ]
    print(f"[setup] TUs={len(entries)} truth-sample={len(sample)} candidates={len(headers)}")

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)

        # 基线：每个被测 TU 的解析文件集、有效树、潜在树（后两者与删哪条无关，只算一次）
        base: list[dict] = []
        for i, entry in enumerate(sample):
            ok, files, graph = compile_tree(entry, work, f"base_{i}")
            if not ok:
                print(f"[fatal] 基线编译失败: {entry['file']}", file=sys.stderr)
                return 2
            root = Path(entry["file"]).name
            pot = potential_graph(files, graph)
            base.append(
                {
                    "entry": entry,
                    "files": files,
                    "graph": graph,
                    "pot": pot,
                    "root": root,
                    "reach_eff": reachable(graph, root, None),
                    "reach_pot": reachable(pot, root, None),
                }
            )
        mean_files = sum(len(b["files"]) for b in base) / len(base)
        print(f"[baseline] {len(base)} TUs，平均解析文件数 {mean_files:.0f}")

        for k, header in enumerate(headers, 1):
            lines = original_text.splitlines(keepends=True)
            idx = next(
                i
                for i, line in enumerate(lines)
                if (m := INCLUDE_RE.match(line)) and m.group(1) == header
            )
            lines[idx] = "// " + lines[idx]
            prelude.write_text("".join(lines), encoding="utf-8")
            try:
                actual: set[str] = set()
                compiled = True
                for i, b in enumerate(base):
                    ok, files, _ = compile_tree(b["entry"], work, f"c{k}_{i}")
                    if not ok:
                        compiled = False
                        break
                    actual |= set(b["files"]) - set(files)
            finally:
                prelude.write_text(original_text, encoding="utf-8")

            if not compiled:
                rows.append({"header": header, "removable": False})
                print(f"[{k:2d}/{len(headers)}] NO  {header:22s} 编译失败")
                continue

            child = Path(header).name
            pred_a: set[str] = set()
            pred_b: set[str] = set()
            for b in base:
                pred_a |= b["reach_eff"] - reachable(b["graph"], b["root"], ("prelude.h", child))
                pred_b |= b["reach_pot"] - reachable(b["pot"], b["root"], ("prelude.h", child))

            rows.append(
                {
                    "header": header,
                    "removable": True,
                    "actual": sorted(actual),
                    "pred_a": sorted(pred_a),
                    "pred_b": sorted(pred_b),
                    "ok_a": pred_a == actual,
                    "ok_b": pred_b == actual,
                    "fp_a": len(pred_a - actual),
                    "fp_b": len(pred_b - actual),
                }
            )
            print(
                f"[{k:2d}/{len(headers)}] OK  {header:22s} actual={len(actual):3d} "
                f"A={len(pred_a):3d}(fp {len(pred_a - actual):3d}) "
                f"B={len(pred_b):3d}(fp {len(pred_b - actual):3d})"
            )

    rem = [r for r in rows if r.get("removable")]
    exact_a = sum(1 for r in rem if r["ok_a"])
    exact_b = sum(1 for r in rem if r["ok_b"])
    fp_a = sum(r["fp_a"] for r in rem)
    fp_b = sum(r["fp_b"] for r in rem)
    hits = sum(1 for r in rem if r["actual"])
    hit_a = sum(1 for r in rem if r["actual"] and r["ok_a"])
    hit_b = sum(1 for r in rem if r["actual"] and r["ok_b"])
    print(f"\n## 可删的 {len(rem)} 条边（其中 {hits} 条真的让文件消失）")
    print(
        f"A 有效树闭包：完全一致 {exact_a}/{len(rem)}，假阳性文件 {fp_a}，"
        f"真消失且预测对 {hit_a}/{hits}"
    )
    print(
        f"B 潜在供给闭包：完全一致 {exact_b}/{len(rem)}，假阳性文件 {fp_b}，"
        f"真消失且预测对 {hit_b}/{hits}"
    )

    payload = {
        "sample_tus": len(sample),
        "rows": rows,
        "summary": {
            "removable": len(rem),
            "with_actual_disappearance": hits,
            "exact_a": exact_a,
            "exact_b": exact_b,
            "false_positive_files_a": fp_a,
            "false_positive_files_b": fp_b,
            "true_positive_a": hit_a,
            "true_positive_b": hit_b,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
