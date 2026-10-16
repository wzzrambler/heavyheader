"""Parser for make-style dependency files.

Both GCC and Clang emit dependencies with ``-M``/``-MM``/``-MD``/``-MF`` in
the same format::

    target.o: src/foo.cpp \\
     /abs/path/foo.h \\
     /abs/path/bar.h

Three details make this less trivial than it looks:

1. Lines are wrapped with a trailing backslash, and the wrapping point is
   arbitrary (the compiler breaks wherever it likes).
2. Spaces inside paths are escaped as ``\\ `` and must not be treated as
   separators.
3. The rule may declare several targets (``a.o b.o: ...``) and the target
   names are *not* necessarily object files when ``-MT`` is used.

We therefore tokenise the whole file first (joining continuations), then
split off the target list at the first unescaped colon.
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

__all__ = ["DepfileError", "parse_depfile", "parse_depfile_text"]


class DepfileError(ValueError):
    """Raised when a dependency file cannot be understood."""


_RULE_SPLIT = re.compile(r"(?<!\\):")
_TARGET_SPLIT = re.compile(r"\s+")


def _split_rule(joined: str) -> tuple[str, str]:
    """Split a make rule into ``(targets, prerequisites)``.

    The separator is the first colon that is followed by whitespace or the
    end of the string.  Requiring a following space is what makes Windows
    paths work: ``C:/build/t.o: C:/src/a.h`` has a drive-letter colon at
    offset 1 followed by ``/``, which is *not* a separator, and the real
    separator later on.  Splitting on the first bare colon instead silently
    truncates the target and turns the rest of the rule into prerequisites.
    """
    escaped = False
    for index, char in enumerate(joined):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ":" and (index + 1 >= len(joined) or joined[index + 1] in " \t\r\n"):
            return joined[:index], joined[index + 1 :]
    raise DepfileError(
        "dependency file does not look like a make rule "
        f"(no rule-separating ':' found in {joined[:80]!r}...)"
    )


def _join_continuations(text: str) -> str:
    """Collapse ``\\<newline>`` sequences into single spaces."""
    # A backslash immediately before a newline is a line continuation.
    return re.sub(r"\\\r?\n", " ", text)


def _split_paths(blob: str) -> list[str]:
    """Split a whitespace-separated path list, honouring ``\\ `` escapes."""
    paths: list[str] = []
    current: list[str] = []
    escaped = False
    for char in blob:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in " \t\r\n":
            if current:
                paths.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        paths.append("".join(current))
    return paths


def _normalise(raw: str) -> Path:
    """Turn a compiler-reported path into a comparable :class:`Path`.

    Compilers are inconsistent about drive-letter casing and separators on
    Windows, so we canonicalise through ``PureWindowsPath`` when the string
    looks like a Windows path.
    """
    cleaned = raw.strip().strip('"')
    if re.match(r"^[A-Za-z]:[\\/]", cleaned) or cleaned.startswith("\\\\"):
        return Path(PureWindowsPath(cleaned))
    return Path(cleaned)


def parse_depfile_text(text: str) -> list[Path]:
    """Parse dependency-file *content* and return the prerequisite paths.

    The targets (everything left of the first unescaped colon) are dropped.
    Order is preserved and duplicates are removed while keeping the first
    occurrence, which is the order the compiler walked the include stack.
    """
    joined = _join_continuations(text).strip()
    if not joined:
        return []

    targets, prerequisites = _split_rule(joined)
    # The compiler may still emit a trailing backslash if the file was
    # truncated; drop a dangling escape.
    prerequisites = prerequisites.rstrip("\\").strip()

    seen: dict[Path, None] = {}
    for raw in _split_paths(prerequisites):
        if not raw:
            continue
        seen.setdefault(_normalise(raw))
    return list(seen)


def parse_depfile(path: str | Path) -> list[Path]:
    """Read ``path`` from disk and parse it."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - trivial I/O guard
        raise DepfileError(f"cannot read {p}: {exc}") from exc
    return parse_depfile_text(text)
