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

Scope: what "this include" means
--------------------------------
The first version of this module compared the source against the *candidate
header's own text* only.  That is the wrong unit of analysis, and the
controlled experiment (``experiments/control.py``) caught it: the tool
proposed removing ``doctest_compatibility.h`` from a test unit that uses
``TEST_CASE`` dozens of times, because ``TEST_CASE`` is declared in
``doctest.h`` -- one level down, inside the closure that disappears with the
include.  Five out of five suggestions failed to compile, all for this
reason.

What a deletion actually removes is the candidate's *exclusive closure*: the
headers that become unreachable once this ``#include`` is gone (see
:func:`heavyheader.attrib.graph.find_removal_candidates`).  So the question is
not "does the source name something this file declares" but "does the source
name something **anything in the disappearing set** declares".  Callers pass
that set in as ``closure``; the verdict is the answer for the whole set, and
``provided_by`` says which file actually owns the matched symbol so the
report can point at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = [
    "UsageVerdict",
    "UsageSignal",
    "IdentifierIndex",
    "analyse_usage",
    "identifiers_in",
]


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
    #: The file inside the disappearing set that declares the matched symbol.
    #: ``None`` when nothing matched (or when the caller passed no closure).
    #: Reported verbatim: "``TEST_CASE`` 由 ``doctest.h`` 提供" is actionable,
    #: "这个头在用" is not.
    provided_by: Path | None = None
    #: 命中了、但因为在消失集合里声明得过于普遍而**不能**当作使用证据的
    #: 标识符。留着它是为了把「为什么判不了」说清楚，而不是扔一个
    #: 「无法判定」让人猜。
    ambient_identifiers: tuple[str, ...] = ()

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


_INCLUDE_DIRECTIVE = re.compile(r"\s*#\s*include\b")


def _strip_noise(text: str) -> str:
    """Blank out comments, string/char literals and ``#include`` lines.

    Preserving the line count matters because callers match verdicts and
    reports back to line numbers.

    Why ``#include`` lines are blanked and not just their quotes: the file
    name in an angle-bracket include is a bare word, and the header it points
    at very often declares an identifier with that same name -- ``<regex>``
    declares ``std::regex``, so the text of ``#include <regex>`` contributes
    the identifier ``regex`` to the *including* file.  The usage test then
    finds a match it created itself and reports "in use" for a header nobody
    uses.  That is not a theoretical concern: it is why the injected-include
    recall experiment (``experiments/g2_recall.py``) scored 0/1 on header
    sites until this was fixed.

    Other preprocessor lines are deliberately **kept**: a header pulled in
    only for a macro that guards an ``#ifdef`` is a real dependency, and
    blanking those lines would turn it into a confident "unused".
    """
    out: list[str] = []
    in_block = False
    for line in text.splitlines():
        if not in_block and _INCLUDE_DIRECTIVE.match(line):
            out.append(" " * len(line))
            continue
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


class IdentifierIndex:
    """Per-scan cache of ``path -> identifiers``.

    Why a cache and not a module-level dict: one scan evaluates hundreds of
    candidates whose closures overlap heavily (every unit that includes
    ``json.hpp`` sees the same thirty detail headers), so caching turns the
    work into "read each header once".  A module-level cache would keep the
    whole stdlib's identifier sets alive for the lifetime of the process,
    which is exactly wrong for the long-running dashboard.

    An unreadable file is cached as an empty set rather than retried, so one
    broken header cannot make a scan quadratic.
    """

    __slots__ = ("_headers", "_sources")

    def __init__(self) -> None:
        self._headers: dict[Path, frozenset[str]] = {}
        self._sources: dict[Path, frozenset[str]] = {}

    def header_ids(self, path: Path) -> frozenset[str]:
        cached = self._headers.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        ids = frozenset(identifiers_in(_strip_noise(text))) if text else frozenset()
        self._headers[path] = ids
        return ids

    def source_ids(self, path: Path) -> frozenset[str] | None:
        """``None`` (not an empty set) when the source cannot be read.

        The distinction matters: "read it, found no identifiers" and "could not
        read it" lead to different verdicts, and collapsing them would turn a
        permissions problem into a confident "unused".
        """
        cached = self._sources.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        if text is None:
            return None
        ids = frozenset(identifiers_in(_strip_noise(text)))
        self._sources[path] = ids
        return ids


def analyse_usage(
    source: Path,
    header: Path,
    closure: "tuple[Path, ...] | list[Path] | set[Path] | None" = None,
    index: IdentifierIndex | None = None,
) -> UsageSignal:
    """Compare the identifiers a TU uses with those that disappear with an include.

    ``closure`` is the *exclusive* closure of the candidate include -- the
    headers that stop being parsed if it is removed -- and normally includes
    ``header`` itself.  Passing it is what makes the verdict correct for
    headers that forward other headers; omitting it keeps the old
    single-file behaviour, which is only right when the candidate is a leaf.

    Note that a header which merely aggregates others (``#pragma once`` plus
    a wall of ``#include``) is *not* judged on its own text: with a closure it
    is judged on everything it brings in, which is precisely what a reader of
    the code means by "is this include used".
    """
    index = index or IdentifierIndex()

    source_ids = index.source_ids(source)
    if source_ids is None:
        return UsageSignal(verdict=UsageVerdict.UNKNOWN)

    extra = [Path(p) for p in (closure or ()) if Path(p) != header]
    # Header first: when several files in the set declare the same name, the
    # candidate itself is the most useful thing to name in the report.
    ordered = [header, *sorted(extra)]

    total = 0
    first_nonempty: Path | None = None
    # name -> (declaring header, how many headers of this set declare it)
    hits: dict[str, tuple[Path, int]] = {}
    headers_with_text = 0

    for path in ordered:
        ids = index.header_ids(path)
        if not ids:
            continue
        headers_with_text += 1
        total += len(ids)
        if first_nonempty is None:
            first_nonempty = path
        for name in ids & source_ids:
            # 第一次见到的声明者先记下；次数在整轮遍历完后才算得准。
            hits.setdefault(name, (path, 0))
            provider, count = hits[name]
            hits[name] = (provider, count + 1)

    if hits:
        # **特异性**：命中一个标识符不等于这个 include 被使用。
        #
        # 实测数据（nlohmann/json 的 locale 候选）把这个问题摆得很清楚：
        # 源码确实「命中」了闭包里的标识符，但命中的是 `config`（108 个头
        # 里 40 个都声明它）、`value`（23 个）、`move`（20 个）——这类词是
        # 标准库的环境噪音，不是依赖证据。第一版判定因此把 218/222 个候选
        # 全判成「正在使用」，过度保守到几乎不再给任何建议。
        #
        # 规则：只有「在消失集合里足够罕见」的命中才算证据。阈值随集合大小
        # 浮动，但设下限 2——只有一两个头声明的名字才是具体依赖。
        threshold = max(2, headers_with_text // 25)
        specific = {
            name: provider
            for name, (provider, count) in hits.items()
            if count <= threshold
        }
        if specific:
            # 具体的命中：按声明者路径排序，保证输出稳定可复现。
            name = sorted(specific)[0]
            return UsageSignal(
                verdict=UsageVerdict.IN_USE,
                shared_identifiers=tuple(sorted(specific)[:8]),
                header_identifier_count=total,
                provided_by=specific[name],
                ambient_identifiers=tuple(
                    sorted(
                        n
                        for n, (_p, c) in hits.items()
                        if c > threshold
                    )[:8]
                ),
            )
        # 有命中，但全部过于普遍：这不是「在用」的证据，也不该算「没用」。
        return UsageSignal(
            verdict=UsageVerdict.UNKNOWN,
            shared_identifiers=tuple(sorted(hits)[:8]),
            header_identifier_count=total,
            ambient_identifiers=tuple(sorted(hits)[:8]),
        )

    if total == 0:
        # Includes nothing, declares nothing, or could not be read.  Saying
        # "unused" here would be a guess dressed up as a finding.
        return UsageSignal(verdict=UsageVerdict.UNKNOWN, header_identifier_count=0)

    if not extra and first_nonempty is not None:
        text = _read(first_nonempty)
        if text is not None and _declares_nothing(text):
            # Aggregation/macro-only header evaluated on its own text alone:
            # its "identifiers" are the names of the headers it forwards, so a
            # naive comparison reports a false unused.  With a closure this
            # branch is never reached -- the closure is the real comparison.
            return UsageSignal(verdict=UsageVerdict.UNKNOWN, header_identifier_count=0)

    return UsageSignal(
        verdict=UsageVerdict.PLAUSIBLY_UNUSED,
        header_identifier_count=total,
    )
