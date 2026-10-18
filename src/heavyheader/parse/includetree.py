"""Parsers for the compiler's *include tree* output.

GCC and Clang print the tree to stderr with ``-H``::

    . /abs/foo.cpp
    .. /abs/foo.h
    ... /abs/bar.h
    .. /abs/baz.h

The number of leading dots is the nesting depth (one dot = included directly
by the TU's source file).  Crucially, **every inclusion is printed**, so a
header pulled in by five different parents appears five times -- which is
exactly what we need to measure redundant work.

GCC additionally prints a trailer when it spots repeated headers without
guards::

    Multiple include guards may be useful for:
    /abs/unguarded.h

MSVC's ``/showIncludes`` is the same idea with different decoration::

    Note: including file:  C:\\abs\\foo.h
    Note: including file:   C:\\abs\\bar.h

Depth is encoded in the amount of leading whitespace, which MSVC derives
from the include stack.  We recover it by counting spaces and dividing by the
observed step size (MSVC uses two spaces per level in practice).
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

from ..models import IncludeEvent

__all__ = ["parse_include_tree", "parse_include_guards", "parse_show_includes"]


_DOT_LINE = re.compile(r"^(?P<dots>\.+)\s(?P<path>.+?)\s*$")
# MSVC 的 /showIncludes 前缀会跟随系统语言本地化：英文 Windows 是
# "Note: including file:"，中文 Windows 是「注意: 包含文件:」。两种都接受，
# 否则在中文环境下前缀匹配不上、整个 include 树被解析成 0 条边。
_NOTE_LINE = re.compile(
    r"^(?:Note:\s*including file:|注意:\s*包含文件:)(?P<indent>\s*)(?P<path>.+?)\s*$",
    re.IGNORECASE,
)
_GUARD_HEADER = re.compile(r"^Multiple include guards may be useful for:", re.IGNORECASE)


def _normalise(raw: str) -> Path:
    """Normalise an MSVC-reported path (see depfile._normalise).

    ``/showIncludes`` paths are always Windows-flavoured, even when the
    parser runs on Linux/macOS, so the drive-letter/backslash forms must be
    canonicalised through ``PureWindowsPath``.  Converting via ``as_posix()``
    before wrapping in the platform ``Path`` is what keeps the result
    comparable with paths coming from ``-H`` on any host.
    """
    cleaned = raw.strip().strip('"')
    if re.match(r"^[A-Za-z]:[\\/]", cleaned) or cleaned.startswith("\\\\"):
        return Path(PureWindowsPath(cleaned).as_posix())
    return Path(cleaned)


def parse_include_tree(stderr_text: str, tu_source: Path) -> list[IncludeEvent]:
    """Parse GCC/Clang ``-H`` output into :class:`IncludeEvent` objects.

    ``tu_source`` is the translation unit being compiled; it is the root of
    the tree and is never emitted as an event itself.

    The traversal is depth-first, so the includer of an event at depth *d* is
    always the most recent event at depth *d-1*.  We track that with an
    explicit stack, which keeps parsing linear in the number of lines.
    """
    return _parse_with_stack(stderr_text, tu_source)


def _parse_with_stack(stderr_text: str, tu_source: Path) -> list[IncludeEvent]:
    """Depth-first traversal with an explicit parent stack."""
    events: list[IncludeEvent] = []
    stack: list[Path] = []  # stack[i] == header at depth i

    for raw_line in stderr_text.splitlines():
        line = raw_line.rstrip("\r")
        match = _DOT_LINE.match(line)
        if match is None:
            continue
        depth = len(match.group("dots")) - 1
        if depth < 0:
            continue
        path = _normalise(match.group("path"))
        if path == tu_source:
            continue
        includer = stack[depth - 1] if depth >= 1 and len(stack) >= depth else tu_source
        events.append(IncludeEvent(includer=includer, included=path, depth=depth))
        del stack[depth:]
        stack.append(path)
    return events


def parse_include_guards(stderr_text: str) -> list[Path]:
    """Extract the 'Multiple include guards may be useful for' trailer."""
    guards: list[Path] = []
    in_trailer = False
    for raw_line in stderr_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _GUARD_HEADER.match(line):
            in_trailer = True
            continue
        if in_trailer:
            # The trailer ends at the first blank line or a new section.
            if line.startswith(".") or line.lower().startswith("multiple"):
                in_trailer = False
                continue
            guards.append(_normalise(line))
    return guards


def parse_show_includes(text: str, tu_source: Path) -> list[IncludeEvent]:
    """Parse MSVC ``/showIncludes`` output.

    MSVC indents nested includes by an extra two spaces per level.  Two
    details make this less obvious than it looks:

    * The indentation of the *shallowest* level is not zero -- there is
      always at least one space after ``including file:``.  Depth is
      therefore computed **relative to the minimum indentation observed**,
      not by dividing the raw count.
    * The step size is inferred from the smallest non-zero gap between
      observed indentation levels, so the parser survives MSVC changing it
      between versions.
    """
    notes: list[tuple[int, Path]] = []
    for raw_line in text.splitlines():
        match = _NOTE_LINE.match(raw_line.rstrip("\r"))
        if match is None:
            continue
        path = _normalise(match.group("path"))
        if path == tu_source:
            continue
        notes.append((len(match.group("indent")), path))

    if not notes:
        return []

    base = min(indent for indent, _ in notes)
    # The step size is the smallest gap between two *distinct* indentation
    # levels, not the offset of the first nested level.  Using the offset
    # would report every nested include as depth 0 whenever the base
    # indentation is one space and nesting adds one more.
    distinct = sorted({indent for indent, _ in notes})
    step = min(
        (b - a for a, b in zip(distinct, distinct[1:], strict=False)), default=1
    ) or 1

    events: list[IncludeEvent] = []
    stack: list[Path] = []
    for indent, path in notes:
        depth = (indent - base) // step
        includer = stack[depth - 1] if depth >= 1 and len(stack) >= depth else tu_source
        events.append(IncludeEvent(includer=includer, included=path, depth=depth))
        del stack[depth:]
        stack.append(path)
    return events
