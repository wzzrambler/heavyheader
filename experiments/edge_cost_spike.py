"""Edge-level marginal cost attribution — feasibility spike.

Question this script answers (falsifiably): **can we rank include EDGES by their
marginal parse cost in milliseconds, using one real compile per TU?**

Method per translation unit:

1. one real compile with ``-ftime-trace`` and ``-H``;
2. the trace's nested ``Source`` spans give per-file *self* time (own tokens,
   nested children subtracted);
3. the ``-H`` tree gives the include edges (parent -> child);
4. for an edge ``p -> c`` the exclusive closure is the set of files that become
   unreachable when that edge is removed *in this TU*;
   marginal time = sum of their self times.

Aggregated across TUs, each edge gets a millisecond estimate.  The spike also
computes the line-count marginal (exclusive lines x TUs) so the two rankings
can be compared on the same data, and reports the top edges together with the
number of TUs they affect.

This is a probe, not a product: edge identity ignores the include *line* (two
identical includes in one file collapse), and the aggregation is per
(parent,child) pair.  Both limitations are stated in the output.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from heavyheader.attrib.cost import _key, parse_trace
from heavyheader.parse.includetree import parse_include_tree
from heavyheader.scanner import _split_command, _strip_output_flag


@dataclass(slots=True)
class EdgeCost:
    parent: str
    child: str
    marginal_seconds: float = 0.0
    marginal_lines: int = 0
    tus: int = 0
    project: bool = False


@dataclass(slots=True)
class UnitResult:
    source: Path
    ok: bool
    total_seconds: float = 0.0
    self_seconds: float = 0.0
    #: (parent, child, marginal_seconds, marginal_lines) per edge in this TU
    edge_costs: list[tuple[str, str, float, int]] = field(default_factory=list)
    note: str = ""


def _line_counts(paths: set[Path]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        try:
            counts[_key(path)] = len(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        except OSError:
            counts[_key(path)] = 0
    return counts


def _reachable(graph: dict[str, set[str]], root: str, skip: tuple[str, str] | None) -> set[str]:
    seen: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for child in graph.get(node, ()):
            if skip is not None and node == skip[0] and child == skip[1]:
                continue
            stack.append(child)
    return seen


def run_unit(
    entry: dict, project_root: Path, workdir: Path, compiler: str, extra_flags: list[str]
) -> UnitResult:
    source = Path(entry["file"])
    directory = Path(entry.get("directory", project_root))
    command = entry.get("command") or " ".join(entry.get("arguments", []))
    argv = _split_command(command) if command else []
    if not argv:
        return UnitResult(source=source, ok=False, note="空命令")
    argv = _strip_output_flag(argv)
    # 去掉调用方自带的 -H / -ftime-trace，避免重复；再追加我们的采集参数。
    argv = [a for a in argv if a not in ("-H", "-ftime-trace")]
    object_path = workdir / (source.stem + ".o")
    argv += [*extra_flags, "-ftime-trace", "-H", "-o", str(object_path)]

    trace = object_path.with_suffix(".json")
    if trace.exists():
        trace.unlink()
    started = time.perf_counter()
    proc = subprocess.run(
        argv, cwd=str(directory), capture_output=True, text=True, timeout=600
    )
    wall = time.perf_counter() - started
    if proc.returncode != 0 or not trace.exists():
        return UnitResult(
            source=source, ok=False, total_seconds=wall,
            note=(proc.stderr or "")[-200:] or "无 trace 输出",
        )

    self_times = {key: us / 1e6 for key, (us, _n) in parse_trace(trace).items()}
    events = parse_include_tree(proc.stderr, source)

    graph: dict[str, set[str]] = {}
    paths: dict[str, Path] = {_key(source): source}
    for event in events:
        parent = _key(event.includer)
        child = _key(event.included)
        paths[parent] = event.includer
        paths[child] = event.included
        graph.setdefault(parent, set()).add(child)
        graph.setdefault(child, set())

    root = _key(source)
    lines = _line_counts(set(paths.values()))

    # 每条边的独占闭包：本 TU 内删掉它后不再可达的集合。
    reachable = _reachable(graph, root, None)
    local: list[tuple[str, str, float, int]] = []
    for parent, children in graph.items():
        for child in children:
            without = _reachable(graph, root, (parent, child))
            excluded = reachable - without
            if not excluded:
                continue
            seconds = sum(self_times.get(k, 0.0) for k in excluded)
            line_sum = sum(lines.get(k, 0) for k in excluded)
            local.append((parent, child, seconds, line_sum))

    return UnitResult(
        source=source,
        ok=True,
        total_seconds=wall,
        self_seconds=sum(self_times.values()),
        edge_costs=local,
    )


def build_report(
    entries: list[dict],
    project_root: Path,
    workdir: Path,
    compiler: str,
    extra_flags: list[str],
    limit: int,
) -> dict:
    costs: dict[tuple[str, str], EdgeCost] = {}
    units: list[UnitResult] = []
    for entry in entries[:limit]:
        result = run_unit(entry, project_root, workdir, compiler, extra_flags)
        units.append(result)
        if not result.ok:
            continue
        for parent, child, seconds, line_sum in result.edge_costs:
            cost = costs.setdefault((parent, child), EdgeCost(parent=parent, child=child))
            cost.marginal_seconds += seconds
            cost.marginal_lines += line_sum
            cost.tus += 1
    ranked = sorted(costs.values(), key=lambda c: c.marginal_seconds, reverse=True)
    return {
        "units": [
            {
                "source": str(u.source),
                "ok": u.ok,
                "wall_seconds": round(u.total_seconds, 3),
                "self_seconds": round(u.self_seconds, 3),
                "edges": len(u.edge_costs),
                "note": "" if u.ok else u.note,
            }
            for u in units
        ],
        "edges": [
            {
                "parent": c.parent,
                "child": c.child,
                "marginal_seconds": round(c.marginal_seconds, 4),
                "marginal_lines": c.marginal_lines,
                "tus": c.tus,
            }
            for c in ranked
        ],
    }


def render_markdown(report: dict) -> str:
    ok = [u for u in report["units"] if u["ok"]]
    lines = [
        "# 边级边际成本归因 · 可行性 spike",
        "",
        f"- 编译单元：{len(report['units'])}（成功 {len(ok)}）",
        f"- 边际秒数合计（Top 60 口径外的全部边也参与计算）："
        f"{sum(u['self_seconds'] for u in ok):.2f}s 自耗时",
        "",
        "## 按边际秒数排序的 include 边（Top 30）",
        "",
        "| # | 边 | 边际 ms | 边际行数 | 影响 TU 数 |",
        "|---:|---|---:|---:|---:|",
    ]
    for i, edge in enumerate(report["edges"][:30], 1):
        lines.append(
            f"| {i} | `{Path(edge['parent']).name}` → `{Path(edge['child']).name}` | "
            f"{edge['marginal_seconds'] * 1000:.1f} | {edge['marginal_lines']:,} | {edge['tus']} |"
        )
    lines += [
        "",
        "口径：边际秒数 = 该 TU 内删掉这条边后不再被解析的文件的自耗时之和；",
        "边际行数 = 同一集合的行数之和。两者对同一批边各自排序，可直接对比。",
        "限制：边身份按 (parent, child) 归一，同文件内两条相同 include 合并计一条。",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path, help="compile_commands.json")
    parser.add_argument("--compiler", default="clang++")
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument(
        "--flag",
        dest="flags",
        action="append",
        default=[],
        help="额外编译参数（如 --target=x86_64-w64-windows-gnu），可重复",
    )
    parser.add_argument("--out", type=Path, default=Path("experiments/results/edge-spike.json"))
    args = parser.parse_args(argv)

    entries = json.loads(args.db.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        report = build_report(
            entries, args.project, Path(tmp), args.compiler, args.flags, args.tus
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md = args.out.with_suffix(".md")
    md.write_text(render_markdown(report), encoding="utf-8")
    print(f"written: {args.out} / {md}")
    ok = [u for u in report["units"] if u["ok"]]
    print(f"units ok: {len(ok)}/{len(report['units'])}  edges ranked: {len(report['edges'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
