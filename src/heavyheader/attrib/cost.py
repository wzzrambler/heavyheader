"""Per-header *cost* measured by the compiler, as a replacement for line counts.

Why this module exists
----------------------
Everything before it ranked candidates by attributed lines (``lines x parse
count``).  Lines are a structural proxy, and the G2 injection experiment
measured exactly how bad the proxy is: a header whose removal really saved
**6.8%** of wall time was ranked **25th of 61** on a line-count model.  A
ranked list that buries its own best finding is not "imprecise", it is the
wrong quantity.

So this module measures what we actually care about -- microseconds the
compiler spent parsing each header -- using ``-ftime-trace``, and turns that
into the same currency the rest of the tool speaks.

The nested-interval correction
------------------------------
``-ftime-trace`` emits ``Source`` events as **nested** begin/end pairs: the
interval for ``json.hpp`` fully contains the interval for the
``adl_serializer.hpp`` it includes.  Summing raw durations therefore
double-counts every child and rewards "umbrella headers" purely for having
children.  We want the *self* time::

    self = interval length - sum(direct children lengths)

That is the only quantity comparable to "exclusive lines x parse count", and
it is why :func:`parse_trace` builds the interval tree instead of summing.

Two costs of using it, stated up front
--------------------------------------
1. **It requires a real compilation.** ``-fsyntax-only`` makes clang drop
   ``-ftime-trace`` with ``argument unused during compilation`` -- verified on
   clang 23.1.3.  So collecting costs means one full codegen pass per unit on
   top of the scan.  That is opt-in (``--cost-model trace``) and its overhead
   is measured, not assumed.
2. **Compiler-specific.** clang emits it; GCC and MSVC do not.  When no trace
   is available the tool falls back to the line model and says so in the
   report, rather than silently pretending it measured something.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "HeaderCosts",
    "parse_trace",
    "collect_trace_costs",
    "TRACE_UNAVAILABLE",
]

TRACE_UNAVAILABLE = "unavailable"


def _key(path: str | Path) -> str:
    """Case-insensitive, separator-insensitive key for a header path.

    Trace ``detail`` strings come straight from the compiler and do not
    always agree with the paths ``-H`` gave us -- different drive letter
    casing, mixed separators, or a ``..`` somewhere in the middle.  Matching
    them literally silently drops headers, which looks like "that header
    costs nothing" rather than "we failed to match it".
    """
    return os.path.normcase(os.path.normpath(str(path))).replace("\\", "/")


@dataclass
class HeaderCosts:
    """Aggregated self-cost per header across every traced unit.

    ``self_us`` is the sum over all traced units; ``count`` how many times
    the header was parsed.  Divide to get cost per parse -- that is the
    number to multiply by "how many units would stop parsing it".
    """

    self_us: dict[str, float] = field(default_factory=dict)
    count: dict[str, int] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    tu_count: int = 0
    basis: str = TRACE_UNAVAILABLE

    @property
    def available(self) -> bool:
        return self.basis != TRACE_UNAVAILABLE and bool(self.self_us)

    def per_parse_us(self, path: Path) -> float | None:
        """Average microseconds this header costs each time it is parsed."""
        key = _key(path)
        total = self.self_us.get(key)
        if total is None:
            return None
        times = self.count.get(key, 0)
        if times <= 0:
            return None
        return total / times

    def total_us(self, paths) -> float | None:
        """Self-cost of parsing *all* of ``paths`` once.

        ``None`` when we have no measurement for any of them -- the caller
        must then fall back to the line model instead of treating "unknown"
        as zero, which would rank unmeasured headers as free.
        """
        total = 0.0
        hit = 0
        for path in paths:
            per = self.per_parse_us(path)
            if per is None:
                continue
            total += per
            hit += 1
        if hit == 0:
            return None
        return total

    def coverage(self, paths) -> tuple[int, int]:
        """``(measured, total)`` headers among ``paths`` we actually timed.

        Partial coverage is the dangerous case: ``total_us`` then describes
        the headers we saw and quietly treats the rest as free.  Reporting
        the fraction lets the note say "3 of 7 headers timed" instead of
        implying the number is complete.
        """
        paths = list(paths)
        measured = sum(1 for path in paths if self.per_parse_us(path) is not None)
        return measured, len(paths)

    def as_dict(self) -> dict:
        return {
            "basis": self.basis,
            "tu_count": self.tu_count,
            "headers": {
                key: {
                    "path": self.paths.get(key, key),
                    "self_us": round(self.self_us.get(key, 0.0), 3),
                    "count": self.count.get(key, 0),
                }
                for key in sorted(self.self_us)
            },
        }


def parse_trace(trace_path: Path) -> dict[str, tuple[float, int]]:
    """``header key -> (self microseconds, parse count)`` for one unit.

    Returns an empty dict for a missing or malformed file: a unit that failed
    to trace must not silently become "every header is free".
    """
    try:
        events = json.loads(trace_path.read_text(encoding="utf-8")).get(
            "traceEvents", []
        )
    except (OSError, ValueError):
        return {}

    # Source events are b/e pairs sharing (pid, tid, id) -- not duration
    # events -- so they have to be re-paired before anything else.
    pending: dict[tuple, list[dict]] = {}
    spans: list[dict] = []
    for event in events:
        if event.get("name") != "Source":
            continue
        key = (event.get("pid"), event.get("tid"), event.get("id"))
        if event.get("ph") == "b":
            pending.setdefault(key, []).append(event)
        elif event.get("ph") == "e" and pending.get(key):
            begin = pending[key].pop()
            detail = ((begin.get("args") or {}).get("detail") or "").strip()
            if not detail:
                continue
            spans.append(
                {
                    "start": begin["ts"],
                    "end": event["ts"],
                    "key": _key(detail),
                    "path": detail,
                    "children": [],
                }
            )

    # Build the containment tree: a parent starts no later and ends no
    # earlier than its children, so sorting by (start, -end) makes every
    # parent precede its own children.
    spans.sort(key=lambda s: (s["start"], -s["end"]))
    stack: list[dict] = []
    for span in spans:
        while stack and span["start"] >= stack[-1]["end"]:
            stack.pop()
        if stack and span["end"] <= stack[-1]["end"]:
            stack[-1]["children"].append(span)
        stack.append(span)

    out: dict[str, tuple[float, int]] = {}
    for span in spans:
        total = span["end"] - span["start"]
        nested = sum(child["end"] - child["start"] for child in span["children"])
        self_us = max(total - nested, 0.0)
        previous_us, previous_count = out.get(span["key"], (0.0, 0))
        out[span["key"]] = (previous_us + self_us, previous_count + 1)
    return out


def collect_trace_costs(
    scan,
    driver,
    options,
    workdir: Path,
    *,
    timeout: float | None = None,
) -> HeaderCosts:
    """Compile every unit once with ``-ftime-trace`` and aggregate.

    One real codegen pass per unit -- ``-fsyntax-only`` makes clang ignore
    the flag.  Units that fail are skipped and counted, so a partial
    collection reports ``tu_count`` honestly instead of looking complete.
    """
    from dataclasses import replace

    workdir.mkdir(parents=True, exist_ok=True)
    costs = HeaderCosts(basis="ftime-trace")
    for index, tu in enumerate(scan.ok_tus):
        object_path = workdir / f"u{index}.o"
        trace_path = workdir / f"u{index}.json"
        # 每个单元要按**它自己**的编译参数跑：compile_commands.json 里各单元
        # 的 -I/-std/-target 常常不同，用共享参数重编会编不过或编出不同的头
        # 集合，成本就对不上号了。
        base = getattr(tu, "compile_options", None) or options
        unit_options = replace(
            base,
            output_path=object_path,
            extra_flags=list(base.extra_flags) + ["-ftime-trace"],
        )
        unit = driver.run(tu.source, unit_options)
        if unit is None or unit.error or not trace_path.exists():
            continue
        parsed = parse_trace(trace_path)
        if not parsed:
            continue
        for key, (self_us, count) in parsed.items():
            costs.self_us[key] = costs.self_us.get(key, 0.0) + self_us
            costs.count[key] = costs.count.get(key, 0) + count
        costs.tu_count += 1
        # 对象文件留着没用，trace 才是产出；留一个会累积几十 MB。
        try:
            object_path.unlink()
        except OSError:
            pass
    if costs.tu_count == 0:
        costs.basis = TRACE_UNAVAILABLE
    return costs
