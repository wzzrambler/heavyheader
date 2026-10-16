"""Usage heuristic: is a header plausibly still needed?

The cost model in :mod:`heavyheader.attrib.graph` answers *how much* a header
costs.  It says nothing about whether the code still needs it, and a report
that conflates the two is worse than no report: the first person who follows
its advice and breaks the build will never trust the tool again.

This module adds the missing half with a deliberately cheap and deliberately
honest test:

    If the translation unit's source mentions none of the identifiers the
    header declares, the header is *plausibly* unused.

Why it is only a heuristic, stated plainly:

* It is textual, not semantic.  A header pulled in purely for a side effect
  (``<iostream``> for stream initialisation, a header that only defines
  macros used by another header) declares identifiers nobody writes down.
* It cannot see through aliases, ``using`` declarations, ADL, or a type that
  reaches the source through a template argument.
* A header whose identifiers all happen to be common English words will look
  "used" when it is not.

So the result is reported as ``plausibly_unused`` / ``in_use`` /
``unknown`` -- never as "safe to delete".  The authoritative answer comes
from :mod:`heavyheader.verify`, which actually removes the include and
recompiles.  That is the whole point of having both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = ["UsageVerdict", "UsageSignal", "analyse_usage", "identifiers_in"]


class UsageVerdict(Enum):
    PLAUSIBLY_UNUSED = "plausibly_unused"
    IN_USE = "in_use"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class UsageSignal:
    """What the textual analysis concluded about one include."""

    verdict: UsageVerdict
    shared_identifiers: tuple[str, ...] = ()
    header_identifier_count: int = 0

    @property
    def label(self) -> str:
        return {
            UsageVerdict.PLAUSIBLY_UNUSED: "疑似未使用",
            UsageVerdict.IN_USE: "正在使用",
            UsageVerdict.UNKNOWN: "无法判定",
        }[self.verdict]


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Words that carry no information about whether a header is used: language
# keywords, preprocessor noise, and the handful of tokens that appear in
# essentially every header.
_IGNORED = frozenset(
    """
    alignas alignof and and_eq asm auto bitand bitor bool break case catch char
    char8_t char16_t char32_t class compl concept const consteval constexpr
    constinit const_cast continue co_await co_return co_yield decltype default
    delete do double dynamic_cast else enum explicit export extern false float
    for friend goto if inline int long mutable namespace new noexcept not
    not_eq nullptr operator or or_eq private protected public register
    reinterpret_cast requires return short signed sizeof static static_assert
    static_cast struct switch template this thread_local throw true try
    typedef typeid typename union unsigned using virtual void volatile
    wchar_t while xor xor_eq
    define defined elif else endif error ifdef ifndef include line pragma
    undef warning once
    std size_t ptrdiff_t nullptr_t
    """.split()
)


def identifiers_in(text: str) -> set[str]:
    """Extract meaningful identifiers from C++ source text."""
    found: set[str] = set()
    for match in _IDENTIFIER.finditer(text):
        word = match.group(0)
        if word in _IGNORED or len(word) < 2:
            continue
        # ALL_CAPS tokens are almost always macros; keep them but they are
        # noisy, so cap how many we collect.
        found.add(word)
    return found


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _strip_noise(text: str) -> str:
    """Blank out comments and string/char literals, preserving line count."""
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


def _declares_nothing(text: str) -> bool:
    """True when a header contains no declarations of its own.

    A header made up purely of ``#include`` directives, ``#define`` macros and
    comments is an *aggregation* header: it exists to hand other headers to
    whoever includes it.  Its "identifiers" are the names of the things it
    forwards, so the usage test would report it as plausibly unused whenever
    the direct includer happens not to name them -- a false positive on one of
    the most common header patterns in large C++ codebases.
    """
    stripped = _strip_noise(text)
    for line in stripped.splitlines():
        code = line.strip()
        if not code:
            continue
        if code.startswith("#"):
            continue
        return False
    return True


def analyse_usage(source: Path, header: Path) -> UsageSignal:
    """Compare the identifiers a TU uses with those a header declares."""
    source_text = _read(source)
    header_text = _read(header)
    if source_text is None or header_text is None:
        return UsageSignal(verdict=UsageVerdict.UNKNOWN)

    if _declares_nothing(header_text):
        # An aggregation or macro-only header: nothing here to match against.
        return UsageSignal(verdict=UsageVerdict.UNKNOWN, header_identifier_count=0)

    header_ids = identifiers_in(_strip_noise(header_text))
    if not header_ids:
        return UsageSignal(verdict=UsageVerdict.UNKNOWN, header_identifier_count=0)

    source_ids = identifiers_in(_strip_noise(source_text))
    shared = header_ids & source_ids

    if shared:
        return UsageSignal(
            verdict=UsageVerdict.IN_USE,
            shared_identifiers=tuple(sorted(shared)[:8]),
            header_identifier_count=len(header_ids),
        )
    return UsageSignal(
        verdict=UsageVerdict.PLAUSIBLY_UNUSED,
        header_identifier_count=len(header_ids),
    )
