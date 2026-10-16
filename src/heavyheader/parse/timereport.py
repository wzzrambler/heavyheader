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

from ..models import PhaseTiming

__all__ = ["TimeReportError", "parse_time_report", "summarise"]


class TimeReportError(ValueError):
    """Raised when ``-ftime-report`` output cannot be understood."""


_ROW = re.compile(
    r"^\s*(?P<name>[^:]+?)\s*:\s*"
    r"(?P<wall>\d+(?:\.\d+)?)"
    r"(?:\s*\(\s*(?P<wall_pct>\d+(?:\.\d+)?)%\s*\))?"
    r"(?:\s+(?P<mem>\d+(?:\.\d+)?)(?P<mem_unit>[kMG]?)\s*"
    r"(?:\(\s*(?P<mem_pct>\d+(?:\.\d+)?)%\s*\))?)?"
    r"\s*$"
)

_HEADER = re.compile(r"^\s*Time variable\b", re.IGNORECASE)

_MEM_MULTIPLIER = {"": 1, "k": 1024, "M": 1024**2, "G": 1024**3}


def parse_time_report(stderr_text: str) -> list[PhaseTiming]:
    """Extract every timing row from ``-ftime-report`` output.

    Rows that do not match (blank lines, the ``Time variable`` header,
    unrelated diagnostics) are skipped rather than treated as errors: the
    report is a diagnostic channel and other tools may write to it too.
    """
    timings: list[PhaseTiming] = []
    for raw_line in stderr_text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip() or _HEADER.match(line):
            continue
        match = _ROW.match(line)
        if match is None:
            continue
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

    if not timings:
        raise TimeReportError(
            "no timing rows found -- did the compiler actually support "
            "-ftime-report and was its stderr captured?"
        )
    return timings


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
