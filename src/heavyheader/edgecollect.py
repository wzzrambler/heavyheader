"""边级成本的采集层：真编译一次，同时拿 ``-ftime-trace`` 与 ``-H``。

分工：本模块只负责「跑编译器、把两份输出变成边成本」，
数学在 :mod:`heavyheader.attrib.edgecost`，渲染在
:mod:`heavyheader.report.edges`。这样纯计算可以单测，采集可以端到端验。

按 ``-ftime-trace`` 的可用性，当前只支持 Clang 系（GCC 的 ``-ftime-report``
只有阶段总量，MSVC 需要另一套 ``/timetrace``）：不支持的工具链**明确报错**，
不做「悄悄退化成估算」——那会让报告里的毫秒数字来路不明。
"""

from __future__ import annotations

import dataclasses
import subprocess
import tempfile
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .attrib.cost import _key, parse_trace
from .attrib.edgecost import unit_edge_costs
from .drivers.base import CompilerDriver
from .models import CompilerFamily
from .parse.includetree import parse_include_tree


class EdgeCostError(RuntimeError):
    """边级成本无法采集（工具链不支持、没有编译单元等）。"""


@dataclass(slots=True)
class EdgeUnit:
    """一个编译单元的采集结果。"""

    source: Path
    ok: bool
    wall_seconds: float = 0.0
    self_seconds: float = 0.0
    edges: list[tuple[str, str, float, int]] = field(default_factory=list)
    note: str = ""

    @property
    def edge_count(self) -> int:
        return len(self.edges)


def _line_counts(paths: set[Path]) -> dict[str, int]:
    """每个文件的行数（按节点键）。读不到就记 0，不因为一个文件崩掉。"""
    counts: dict[str, int] = {}
    for path in paths:
        try:
            counts[_key(path)] = sum(1 for _ in path.open("rb"))
        except OSError:
            counts[_key(path)] = 0
    return counts


def collect_unit(
    source: Path,
    driver: CompilerDriver,
    options,
    workdir: Path,
    slot: str,
    *,
    cwd: Path | None = None,
    timeout: float = 300.0,
    effective_only: bool = False,
) -> EdgeUnit:
    """编译一个 TU 并算出它的边成本。"""
    object_path = workdir / f"{slot}.o"
    opts = dataclasses.replace(options, output_path=object_path)
    argv = driver.build_command(source, opts)
    # 编译成 .o 而不是链接：compile_commands 里的命令自带 -c，但走「目录里
    # 有 .cpp 但没 compile_commands」的兜底路径时没有，clang 会一路链接下去
    # （实测在 Windows 上报 lld-link: undefined symbol -- 看起来像代码有错，
    # 其实是我们在让它链接）。
    if "-c" not in argv:
        argv.insert(1, "-c")
    if "-ftime-trace" not in argv:
        argv.append("-ftime-trace")
    trace_path = object_path.with_suffix(".json")
    if trace_path.exists():
        trace_path.unlink()

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return EdgeUnit(source=source, ok=False, note=f"启动编译器失败：{exc}")
    wall = time.perf_counter() - started

    if completed.returncode != 0 or not trace_path.exists():
        tail = (completed.stderr or "").strip().splitlines()[-3:]
        return EdgeUnit(
            source=source,
            ok=False,
            wall_seconds=wall,
            note="编译失败：" + " / ".join(tail) if tail else "编译失败（无 trace 输出）",
        )

    self_times = {key: micros / 1e6 for key, (micros, _n) in parse_trace(trace_path).items()}
    events = parse_include_tree(completed.stderr, source)

    graph: dict[str, set[str]] = {}
    paths: dict[str, Path] = {_key(source): source}
    for event in events:
        parent = _key(event.includer)
        child = _key(event.included)
        paths[parent] = event.includer
        paths[child] = event.included
        graph.setdefault(parent, set()).add(child)
        graph.setdefault(child, set())

    edges = [
        (parent, child)
        for parent, children in graph.items()
        for child in children
    ]
    root = _key(source)
    unit_edges = unit_edge_costs(
        root,
        edges,
        self_times,
        _line_counts(set(paths.values())),
        files=None if effective_only else paths,
    )
    return EdgeUnit(
        source=source,
        ok=True,
        wall_seconds=wall,
        self_seconds=sum(self_times.values()),
        edges=unit_edges,
    )


def collect_edge_units(
    tasks: Sequence,
    driver: CompilerDriver,
    *,
    jobs: int = 1,
    limit: int | None = None,
    timeout: float = 300.0,
    effective_only: bool = False,
) -> list[EdgeUnit]:
    """对一批编译单元采集边成本。

    ``jobs > 1`` 时并行——但并行会拉高墙钟噪声，只有单轮采集用得上；
    任何要比较耗时的结论都应当串行。
    """
    if driver.family is not CompilerFamily.CLANG:
        raise EdgeCostError(
            "边级成本目前只支持 Clang 系（-ftime-trace）。"
            f"当前工具链是 {driver.family.value}：GCC 只有 -ftime-report 阶段总量，"
            "MSVC 需要 /timetrace，都没有逐文件自耗时。"
        )
    selected = list(tasks)[: limit] if limit else list(tasks)
    if not selected:
        raise EdgeCostError("没有找到编译单元（需要 compile_commands.json 或源码文件）")

    with tempfile.TemporaryDirectory(prefix="heavyheader-edge-") as tmp:
        workdir = Path(tmp)

        def _one(index_task: tuple[int, object]) -> EdgeUnit:
            index, task = index_task
            return collect_unit(
                task.source,
                driver,
                task.options,
                workdir,
                slot=f"u{index}",
                cwd=task.cwd,
                timeout=timeout,
                effective_only=effective_only,
            )

        indexed = list(enumerate(selected))
        if jobs <= 1:
            return [_one(item) for item in indexed]
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(_one, indexed))
