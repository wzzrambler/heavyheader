"""边级边际成本：一条 include 边删掉后，哪些文件不再被解析、省多少毫秒。

竞品的归因停在**节点**（这个头文件总共花了多少），而开发者能删的是**边**
（源文件里的那一行 ``#include``）。两者不是同一个量：一个头文件被五个 TU
共享时，删掉其中一条 include 只省下这条边独占的那部分。

所以这里算的是：对每条边 e，取本编译单元内「可达集合减去拿掉 e 后的可达
集合」，这个差集就是 e 的独占闭包；把闭包内各文件的自耗时（来自
``-ftime-trace`` 的嵌套区间还原）与行数分别求和，就是这条边的边际成本。

本模块只做纯计算：输入已解析好的边、自耗时、行数，输出成本表。采集
（真编译、trace、``-H``）在调用方，便于单测与复用。

**边界**：这是预测值，用于排序候选。一条边值不值得删，仍只能由 verify 的
A/B 实测说了算——预测与结论不是一回事，本模块不越权。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[<\"]([^>\"]+)[>\"]")


#: 一条边的边际成本。``marginal_seconds`` 是跨 TU 求和，因此是「全项目删掉
#: 这条边能省的编译时间」的量级，而不是单次编译的墙钟。
@dataclass(slots=True)
class EdgeCost:
    parent: str
    child: str
    marginal_seconds: float = 0.0
    marginal_lines: int = 0
    #: 有多少个编译单元里这条边存在且独占闭包非空
    tus: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.parent, self.child)


def _reachable(
    graph: Mapping[str, set[str]], root: str, skip: tuple[str, str] | None
) -> set[str]:
    """从 ``root`` 出发可达的节点集合，可选跳过一条边。"""
    seen = {root}
    stack = [root]
    while stack:
        node = stack.pop()
        for nxt in graph.get(node, ()):
            if skip is not None and node == skip[0] and nxt == skip[1]:
                continue
            if nxt in seen:
                continue
            seen.add(nxt)
            stack.append(nxt)
    return seen


def build_potential_graph(
    files: Mapping[str, Path], effective: Mapping[str, set[str]]
) -> dict[str, set[str]]:
    """在「本次已解析的文件」范围内补全**潜在供给边**。

    为什么必须补：``-H``（以及 ``-ftime-trace`` 的 Source 区间）只记录每个头
    **首次生效**的包含点——它被谁先拉进来，就只显示那一条路径。于是「删掉
    prelude.h 里的 ``#include <cmath>``」在有效树上看起来只通向 cmath，
    实际上 cmath 早被别的头拉进来了，删了根本不省。实测：删掉该行后 TU 的
    解析文件数一个不少（377 → 377）。

    补法是把这个 TU 解析到的每个文件的 ``#include`` 行**文本扫描**一遍，按
    文件名把边加回图中（``files`` 的键可以是路径、也可以是任何节点标识，
    匹配按 ``Path.name`` 进行）。条件编译看不到（文本层没有宏求值），因此
    可能多算一条永不生效的供给边——方向是**保守**：闭包偏小、预测偏低，
    不会虚报收益。
    """
    graph: dict[str, set[str]] = {key: set(children) for key, children in effective.items()}
    by_name: dict[str, str] = {}
    for key, path in files.items():
        by_name.setdefault(Path(path).name, key)
    for key, path in files.items():
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = _INCLUDE_RE.match(line)
            if not match:
                continue
            target = by_name.get(Path(match.group(1)).name)
            if target is not None:
                graph.setdefault(key, set()).add(target)
                graph.setdefault(target, set())
    return graph


def unit_edge_costs(
    root: str,
    edges: Iterable[tuple[str, str]],
    self_seconds: Mapping[str, float],
    line_counts: Mapping[str, int],
    files: Mapping[str, Path] | None = None,
) -> list[tuple[str, str, float, int]]:
    """单个编译单元里每条边的 ``(parent, child, 秒, 行)``。

    独占闭包为空的边**不返回**：那条 include 删掉后没有任何文件不再被解析，
    即「删了不省」。把它们放进报告只会制造噪声。

    ``files``（文件名 → 路径）给出本次解析到的文件时，会补全潜在供给边——
    **这是得到正确闭包的必要条件**，只给 ``edges`` 得到的是有效树的乐观估计。
    """
    graph: dict[str, set[str]] = {root: set()}
    for parent, child in edges:
        graph.setdefault(parent, set()).add(child)
        graph.setdefault(child, set())
    if files:
        graph = build_potential_graph(files, graph)

    reachable = _reachable(graph, root, None)
    out: list[tuple[str, str, float, int]] = []
    for parent, children in graph.items():
        for child in children:
            without = _reachable(graph, root, (parent, child))
            excluded = reachable - without
            if not excluded:
                continue
            seconds = sum(self_seconds.get(node, 0.0) for node in excluded)
            lines = sum(line_counts.get(node, 0) for node in excluded)
            out.append((parent, child, seconds, lines))
    return out


def aggregate_edge_costs(
    per_unit: Iterable[Sequence[tuple[str, str, float, int]]],
) -> list[EdgeCost]:
    """把逐单元的边成本聚合成项目级表，按边际秒数降序。

    同一条边出现在多个 TU 里时逐项求和：全项目删掉这条 include 的收益，正是
    各处之和。
    """
    costs: dict[tuple[str, str], EdgeCost] = {}
    for unit in per_unit:
        for parent, child, seconds, lines in unit:
            cost = costs.setdefault((parent, child), EdgeCost(parent=parent, child=child))
            cost.marginal_seconds += seconds
            cost.marginal_lines += lines
            cost.tus += 1
    return sorted(costs.values(), key=lambda c: c.marginal_seconds, reverse=True)
