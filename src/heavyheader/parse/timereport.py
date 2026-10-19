"""Parser for ``-ftime-report`` output.

GCC (9+) and Clang (17+) print a table to stderr::

    Time variable                                  wall           GGC
     phase setup                        :   0.02 (  5%)  1856k (  4%)
     phase parsing                      :   0.28 ( 74%)    40M ( 83%)
     preprocessing                      :   0.14 ( 36%)  2723k (  5%)
     TOTAL                              :   0.37           49M

The awkward parts are the optional columns: the wall percentage, the GGC
memory figure and its percentage are all independently omittable, and phase
names contain spaces and dots (``lang. deferred``, ``parser struct body``).
We therefore match the two anchors we can rely on -- a trailing ``name :``
and a leading floating point number -- and treat everything after the first
number as optional decoration.

Memory suffixes are binary (``k`` = 1024, ``M`` = 1024**2, ``G`` =
1024**3), matching GCC's own reporting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import PhaseTiming

__all__ = [
    "TimeReportError",
    "parse_time_report",
    "summarise",
    "sanitise_timings",
    "TimingSanityReport",
]


class TimeReportError(ValueError):
    """Raised when ``-ftime-report`` output cannot be understood."""


#: A compiler phase cannot take longer than the process that contains it, so
#: anything above ``measured_wall + _WALL_SLACK`` is not a measurement, it is
#: a broken timer.  The slack absorbs the difference between the compiler's
#: internal clock and our ``perf_counter`` reading.
_WALL_SLACK = 1.0


_ROW = re.compile(
    r"^\s*(?P<name>[^:]+?)\s*:\s*"
    r"(?P<wall>\d+(?:\.\d+)?)"
    r"(?:\s*\(\s*(?P<wall_pct>\d+(?:\.\d+)?)%\s*\))?"
    r"(?:\s+(?P<mem>\d+(?:\.\d+)?)(?P<mem_unit>[kMG]?)\s*"
    r"(?:\(\s*(?P<mem_pct>\d+(?:\.\d+)?)%\s*\))?)?"
    r"\s*$"
)

#: Clang 的行是**数字在前、名字在后**（GCC 相反），且带 1–3 个数字列
#: （system / user+sys / wall）。形如::
#:
#:     0.0469 (100.0%)   0.0469 (100.0%)   0.0395 (100.0%)  Front end
#:     0.0003 ( 63.4%)  Global Splitting
#:
#: 旧版只认 GCC 的 ``name :`` 形态，于是 clang 的 -ftime-report **从来没被
#: 解析过**：每次扫描都走「TOTAL 缺失 → 驱动实测墙钟」兜底，而扫描是并行的，
#: 墙钟被 N 个编译进程争抢——预估锚点因此虚高（实测 lgc.c 上虚高 5 倍，叠加
#: 代码生成占大头后总偏差 ~50 倍）。
_CLANG_ROW = re.compile(
    r"^\s*(?:\d+(?:\.\d+)?\s*\(\s*\d+(?:\.\d+)?%\s*\)\s+)+"
    r"(?P<name>\S.*?)\s*$"
)

#: Clang 报告结尾的权威总计::
#:
#:     Total Execution Time: 0.0469 seconds (0.0395 wall clock)
#:
#: 括号里那个才是进程墙钟；第一个数字是 CPU 时间（可能远大于墙钟）。
_CLANG_TOTAL = re.compile(
    r"^\s*Total Execution Time:\s*"
    r"(?P<cpu>\d+(?:\.\d+)?)\s*seconds?\s*"
    r"\((?P<wall>\d+(?:\.\d+)?)\s*wall clock\)",
    re.IGNORECASE,
)

_HEADER = re.compile(r"^\s*Time variable\b", re.IGNORECASE)

_MEM_MULTIPLIER = {"": 1, "k": 1024, "M": 1024**2, "G": 1024**3}


#: clang 计时表的一个数字列：``0.0395 (100.0%)``。
_CLANG_COLUMN = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*\(\s*\d+(?:\.\d+)?%\s*\)\s*"
)


def _parse_clang_row(line: str) -> tuple[float, str] | None:
    """解析 clang 形态的一行，返回 ``(墙钟秒, 阶段名)``。

    clang 的表可能有 1–3 个数字列（system / user+sys / wall），**最后一列才是
    墙钟**。单列形态（LLVM pass 计时器）那一列就是墙钟。
    """
    position = 0
    wall: float | None = None
    body = line.lstrip()  # clang 的表有前导缩进，数字列必须从行首开始才算
    for match in _CLANG_COLUMN.finditer(body, position):
        if match.start() != position:
            return None  # 数字列必须连续顶格，中间有别的内容就不是计时行
        wall = float(match.group("num"))
        position = match.end()
    if wall is None or position >= len(body):
        return None
    name = body[position:].strip()
    if not name:
        return None
    return wall, name


def parse_time_report(stderr_text: str) -> list[PhaseTiming]:
    """Extract every timing row from ``-ftime-report`` output.

    Rows that do not match (blank lines, the ``Time variable`` header,
    unrelated diagnostics) are skipped rather than treated as errors: the
    report is a diagnostic channel and other tools may write to it too.

    Handles both dialects: GCC's ``name : wall (pct%) mem`` and Clang's
    ``wall (pct%) [wall (pct%) [wall (pct%)]] name``.  Clang's
    ``Total Execution Time: … (… wall clock)`` trailer becomes the row named
    ``TOTAL`` -- it is emitted **first** because every Clang pass group also
    prints its own ``Total`` and those are *not* the compile's total.
    """
    timings: list[PhaseTiming] = []
    for raw_line in stderr_text.splitlines():
        line = raw_line.rstrip("\r")
        trailer = _CLANG_TOTAL.match(line)
        if trailer is not None:
            # 整份报告只有一个总汇总行；重复出现时以第一处为准。
            if not any(t.name == "TOTAL" for t in timings):
                timings.append(
                    PhaseTiming(
                        name="TOTAL",
                        wall_seconds=float(trailer.group("wall")),
                        wall_percent=100.0,
                    )
                )
            continue
        if not line.strip() or _HEADER.match(line):
            continue
        match = _ROW.match(line)
        if match is not None:
            wall = float(match.group("wall"))
            wall_pct = match.group("wall_pct")
            mem = match.group("mem")
            memory_bytes: int | None = None
            if mem is not None:
                unit = match.group("mem_unit") or ""
                memory_bytes = int(float(mem) * _MEM_MULTIPLIER[unit])
            timings.append(
                PhaseTiming(
                    name=match.group("name").strip(),
                    wall_seconds=wall,
                    wall_percent=float(wall_pct) if wall_pct is not None else None,
                    memory_bytes=memory_bytes,
                )
            )
            continue
        clang_row = _parse_clang_row(line)
        if clang_row is not None:
            wall, name = clang_row
            timings.append(PhaseTiming(name=name, wall_seconds=wall))

    if not timings:
        raise TimeReportError(
            "no timing rows found -- did the compiler actually support "
            "-ftime-report and was its stderr captured?"
        )
    return timings


@dataclass(slots=True)
class TimingSanityReport:
    """What the sanity check threw away, and why.

    Kept on the scan rather than logged and forgotten: a user who sees a
    build cost of 584 years deserves to know the compiler lied, not to
    quietly receive a corrected number.
    """

    discarded: list[str] = field(default_factory=list)
    total_was_discarded: bool = False

    @property
    def clean(self) -> bool:
        return not self.discarded


def sanitise_timings(
    timings: list[PhaseTiming], measured_wall_seconds: float
) -> tuple[list[PhaseTiming], TimingSanityReport]:
    """Drop timing rows that cannot physically be true.

    A compiler phase cannot take longer than the process that contains it.
    We already measure that process with ``perf_counter`` around the
    subprocess, so we have an independent upper bound to check against.

    This is not defensive programming for its own sake.  GCC on MinGW has a
    real bug where ``-ftime-report`` prints ``2**64 / 1e9`` seconds for some
    phases -- roughly 584 years -- and a tool that faithfully reports that
    number is worse than one that reports nothing, because the output looks
    authoritative.  Observed on GCC 16.2.0 (MinGW-w64) compiling files that
    include ``<windows.h>``.

    Returns the surviving rows plus a report of what was dropped.
    """
    ceiling = measured_wall_seconds + _WALL_SLACK
    report = TimingSanityReport()
    kept: list[PhaseTiming] = []

    for timing in timings:
        if timing.wall_seconds > ceiling:
            report.discarded.append(
                f"{timing.name}={timing.wall_seconds:.2f}s "
                f"(exceeds measured wall {measured_wall_seconds:.2f}s)"
            )
            if timing.is_total:
                report.total_was_discarded = True
            continue
        kept.append(timing)

    return kept, report


def summarise(timings: list[PhaseTiming]) -> dict[str, float]:
    """Roll a timing table up to the handful of phases we care about.

    Returns seconds per canonical bucket.  ``TOTAL`` wins over the sum when
    present because it includes phases we do not model individually.
    """
    buckets = {
        "total": 0.0,
        "preprocessing": 0.0,
        "parsing": 0.0,
        "template_instantiation": 0.0,
        "opt_and_generate": 0.0,
        "other": 0.0,
    }
    for timing in timings:
        name = timing.name.strip().lower()
        if name == "total":
            buckets["total"] = timing.wall_seconds
        elif name == "preprocessing":
            buckets["preprocessing"] += timing.wall_seconds
        elif name.startswith("parser") or name == "phase parsing" or name == "parsing":
            buckets["parsing"] += timing.wall_seconds
        elif "template" in name:
            buckets["template_instantiation"] += timing.wall_seconds
        elif "opt and generate" in name or "codegen" in name:
            buckets["opt_and_generate"] += timing.wall_seconds
        else:
            buckets["other"] += timing.wall_seconds
    return buckets
