"""Template instantiation cost: the number nobody surfaces.

``-ftime-report`` has reported a ``template instantiation`` row since GCC 9,
and for template-heavy C++ it is often the single largest phase.  A file that
includes ``nlohmann/json.hpp`` spends about 39% of its build time there
(measured: 0.44s of 1.13s).  Yet no existing build-analysis tool puts that
number in front of anyone.

What this module computes, and how honest each part is:

**Exact** -- per translation unit, the seconds and percentage spent in
template instantiation, read straight from ``-ftime-report``.

**Heuristic** -- per header, a *template surface*: the number of
``template<`` declarations multiplied by how many translation units reach it.
This answers "where does that cost come from", which the compiler cannot
answer because it only reports per-phase totals, never per-header attribution.

The heuristic's limits, stated plainly:

* It is textual.  A ``template<`` inside a disabled ``#if 0`` branch counts,
  so it over-estimates -- deliberately, because under-reporting would hide
  real cost.
* A header full of templates that nobody instantiates scores the same as one
  whose templates are instantiated in every TU.  Surface is opportunity, not
  realised cost.
* Correlation, not causation.  A high-surface header in a TU with high
  instantiation time is a lead to investigate, not a verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["TemplateProfile", "count_template_declarations", "profile_project"]


# ``template<`` possibly followed by typename/class and a parameter list.
_TEMPLATE_RE = re.compile(r"\btemplate\s*<")


def _strip_comments_preserving_lines(text: str) -> str:
    """Blank comments and string literals without changing line count."""
    out: list[str] = []
    in_block = False
    for line in text.splitlines():
        result: list[str] = []
        index = 0
        while index < len(line):
            if in_block:
                end = line.find("*/", index)
                if end == -1:
                    result.append(" " * (len(line) - index))
                    index = len(line)
                else:
                    result.append(" " * (end + 2 - index))
                    index = end + 2
                    in_block = False
                continue
            pair = line[index : index + 2]
            if pair == "/*":
                in_block = True
                result.append("  ")
                index += 2
                continue
            if pair == "//":
                result.append(" " * (len(line) - index))
                index = len(line)
                continue
            char = line[index]
            if char in "\"'":
                closing = line.find(char, index + 1)
                if closing == -1:
                    result.append(" " * (len(line) - index))
                    index = len(line)
                else:
                    result.append(" " * (closing + 1 - index))
                    index = closing + 1
                continue
            result.append(char)
            index += 1
        out.append("".join(result))
    return "\n".join(out) + "\n"


def count_template_declarations(path: Path) -> int:
    """Count ``template<`` declarations in a header.

    Comments and string literals are stripped first so that a doc comment
    mentioning ``template<`` does not inflate the count.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return len(_TEMPLATE_RE.findall(_strip_comments_preserving_lines(text)))


@dataclass(slots=True)
class TemplateProfile:
    """Project-wide template cost picture."""

    #: Per-TU (source, seconds, percent), sorted by percent descending.
    per_tu: list[tuple[Path, float, float]]
    #: Headers ranked by template surface (declarations x reach).
    by_surface: list[tuple[Path, int, int]]
    #: Total seconds spent in template instantiation across all TUs.
    total_seconds: float
    #: That total as a percentage of the whole build.
    overall_percent: float
    #: TUs where template instantiation dominates (> 25% of build time).
    heavy_tus: list[Path]

    @property
    def is_significant(self) -> bool:
        """Whether template cost is worth reporting at all.

        A project where instantiation is 2% of the build does not need a
        section about it; a report full of noise gets ignored.
        """
        return self.overall_percent >= 5.0 and self.total_seconds >= 0.05


def profile_project(scan) -> TemplateProfile:
    """Build the template cost profile for a completed scan."""
    per_tu: list[tuple[Path, float, float]] = []
    heavy: list[Path] = []
    total_seconds = 0.0
    total_build = 0.0

    for tu in scan.ok_tus:
        seconds = tu.template_instantiation_seconds()
        total = tu.total_wall_seconds()
        if total <= 0:
            continue
        percent = seconds / total * 100.0
        per_tu.append((tu.source, seconds, percent))
        total_seconds += seconds
        total_build += total
        if percent >= 25.0:
            heavy.append(tu.source)

    per_tu.sort(key=lambda item: item[2], reverse=True)

    by_surface: list[tuple[Path, int, int]] = []
    for header, stats in scan.header_stats.items():
        declarations = count_template_declarations(header)
        stats.template_declarations = declarations
        stats.template_surface = declarations * stats.tu_reach
        if declarations > 0:
            by_surface.append((header, declarations, stats.tu_reach))
    by_surface.sort(key=lambda item: item[1] * item[2], reverse=True)

    overall = (total_seconds / total_build * 100.0) if total_build > 0 else 0.0

    return TemplateProfile(
        per_tu=per_tu,
        by_surface=by_surface,
        total_seconds=total_seconds,
        overall_percent=overall,
        heavy_tus=heavy,
    )
