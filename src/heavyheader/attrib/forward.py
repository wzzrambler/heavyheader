"""Forward-declaration suggestions.

Replacing a ``#include`` with a forward declaration is the classic compile-time
optimisation, and the one with the highest yield: a forward declaration costs
the compiler a line of text instead of the header's whole closure.

It is also the easiest kind of advice to get *wrong*, because "only used as a
pointer" is a semantic property and we are working from text.  So this module
is deliberately conservative and always reports its evidence.

The rule
--------
A header is a forward-declaration candidate for a translation unit when

1. it declares at least one class or struct, **and**
2. every identifier the TU borrows from it appears **only** in a
   pointer/reference position, **and**
3. the TU contains none of the constructs that require the complete type:
   member access (``X::``), ``sizeof(X)``, ``new X``, ``delete`` on it,
   inheritance from it, or a by-value object.

Conditions 2 and 3 are checked with regular expressions, so they can be
fooled.  The output therefore names the specific type, the specific lines,
and what was checked -- a reviewer can confirm or reject in seconds, which is
the point.  A tool that says "you could forward-declare this" without saying
why is not actionable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ForwardCandidate",
    "TypeDeclaration",
    "declared_types",
    "find_forward_declaration_candidates",
    "type_declarations",
]


# class/struct/enum declarations, capturing the name.
_DECL_RE = re.compile(
    r"^\s*(?:template\s*<[^>]*>\s*)?"
    r"(?:class|struct|union)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)",
)

# ``__attribute__((...))`` can nest parentheses, so strip it before matching
# rather than trying to write a regex for arbitrary nesting.
_ATTRIBUTE_RE = re.compile(r"__attribute__\s*\(+[^;]*?\)+\s*")

# The same declaration, but keeping the pieces needed to write a *forward*
# declaration back out: the class-key (``class``/``struct``/``union`` must be
# repeated verbatim, MSVC warns on a mismatch), the template parameter list
# (a template cannot be forward-declared without it), and the name.
_NAMED_DECL_RE = re.compile(
    r"^\s*(?:template\s*<(?P<tmpl>[^>]*)>\s*)?"
    r"(?P<kind>class|struct|union)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
)

# Matched against the text *before* the opening brace, i.e. ``namespace a``
# (no ``{`` in the haystack) -- the scanner below consumes braces itself.
# ``namespace a `` / ``namespace a::b `` / ``inline namespace a ``.
_NAMESPACE_RE = re.compile(
    r"^\s*(?:inline\s+)?namespace\s+(?P<name>[A-Za-z_][A-Za-z0-9_:]*)\s*$"
)
# ``namespace {`` -- an unnamed namespace.  Its members have internal linkage
# and **cannot** be named from outside, so a forward declaration is impossible;
# callers must report that rather than emit something that only looks right.
_ANON_NAMESPACE_RE = re.compile(r"^\s*(?:inline\s+)?namespace\s*$")


@dataclass(slots=True)
class TypeDeclaration:
    """One class/struct/union declaration, with what a forward decl needs.

    The forward-declaration *suggestion* and the forward-declaration *trial*
    must agree on what counts as a declaration, or the trial will be run on a
    spelling the suggestion never saw.  Both read this one function.
    """

    name: str
    #: ``class`` / ``struct`` / ``union``, as spelled in the header.
    kind: str
    #: Text between ``template<`` and ``>``, or ``""``.  ``template<` vs
    #: ``template <`` spacing is normalised to ``template <...>``.
    template_params: str = ""
    #: Enclosing namespaces, outermost first.  Empty for the global namespace.
    #: ``("",)`` never occurs -- an unnamed namespace is reported separately
    #: because it cannot be forward-declared at all.
    namespaces: tuple[str, ...] = ()
    #: 1-based line number in the header.
    line: int = 0
    #: True when the declaration sits inside an unnamed namespace.
    internal_linkage: bool = False

    @property
    def template_prefix(self) -> str:
        """``template <typename T> `` or ``""``."""
        return f"template <{self.template_params}> " if self.template_params else ""


# Usages that need the complete type.  Built per type name so the patterns
# stay readable; ``{n}`` is replaced with the escaped name.
_NEEDS_DEFINITION_TEMPLATES = (
    r"\b{n}\s*::",                      # member access
    r"\bsizeof\s*\(\s*{n}\b",           # sizeof
    r"\bnew\s+{n}\b",                   # new
    r"\balignof\s*\(\s*{n}\b",
    r"\b(?:public|protected|private)\s+{n}\b",   # inheritance
    r"\b{n}\s+\w+\s*[;=,)\]]",          # by-value object or parameter
    r"\b{n}\s+\w+\s*\{",                # brace initialisation
    r"\btypeid\s*\(\s*{n}\b",
    r"\bdelete\s+\w*\s*{n}\b",
)

# Usages a forward declaration satisfies.
_POINTER_OR_REF_TEMPLATE = r"(?:\b{n}\s*[*&]|[*&]\s*\b{n}\b|\b{n}\s*\*\s*const\b)"

_IDENTIFIER = re.compile(r"\b{name}\b")


def _strip_comments_preserving_lines(text: str) -> str:
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


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def type_declarations(header: Path) -> list[TypeDeclaration]:
    """Every class/struct/union the header declares, with its context.

    Namespace context is tracked by counting braces -- the same shortcut
    ``strip_comments`` takes, and it fails the same way: a brace inside a
    string literal or a raw string would throw the depth off.  Headers that
    do that are rare, and when it happens the effect is a *wrong* namespace
    in a forward declaration, which the trial `forward-verify` then rejects
    by compiling.  That is the right division of labour: this function
    proposes, the compiler decides.
    """
    text = _read(header)
    if text is None:
        return []

    declarations: list[TypeDeclaration] = []
    #: One frame per open brace: ``("namespace", name)`` or ``("block", None)``.
    #: A declaration only counts when every enclosing frame is a namespace --
    #: a class declared inside a function body cannot be forward-declared from
    #: the outside, and proposing it would be advice nobody can act on.
    frames: list[tuple[str, str | None]] = []

    def record(match: re.Match[str], number: int) -> None:
        if any(kind != "namespace" for kind, _ in frames):
            return
        scope = [name or "" for _, name in frames]
        declarations.append(
            TypeDeclaration(
                name=match.group("name"),
                kind=match.group("kind"),
                template_params=(match.group("tmpl") or "").strip(),
                # ``namespace a::b {`` is C++17 syntax; expanding it into
                # nested names keeps the forward declaration valid under
                # C++11/14 as well.
                namespaces=tuple(
                    part for name in scope for part in name.split("::") if part
                ),
                line=number,
                internal_linkage="" in scope,
            )
        )

    for number, raw in enumerate(
        _strip_comments_preserving_lines(text).splitlines(), start=1
    ):
        # __attribute__((...)) nests parentheses, so strip it rather than
        # trying to regex arbitrary nesting.
        line = _ATTRIBUTE_RE.sub(" ", raw)
        buffer: list[str] = []

        for char in line:
            if char == "{":
                opened = "".join(buffer).strip()
                namespace = _NAMESPACE_RE.match(opened)
                if namespace:
                    frames.append(("namespace", namespace.group("name")))
                elif _ANON_NAMESPACE_RE.match(opened):
                    # Unnamed namespace: members have internal linkage and
                    # cannot be named from another translation unit, so the
                    # frame is remembered but never yields a declaration.
                    frames.append(("namespace", ""))
                else:
                    # ``class Foo : public Bar `` is a definition, not just a
                    # block; record it before the body opens.
                    match = _NAMED_DECL_RE.match(opened)
                    if match:
                        record(match, number)
                    frames.append(("block", None))
                buffer.clear()
            elif char == "}":
                if frames:
                    frames.pop()
                buffer.clear()
            elif char == ";":
                # ``class Foo;`` -- a declaration with no body.  Definitions
                # ending in ``};`` were already recorded at the ``{``.
                match = _NAMED_DECL_RE.match("".join(buffer).strip())
                if match:
                    record(match, number)
                buffer.clear()
            else:
                buffer.append(char)

    return declarations


def declared_types(header: Path) -> list[str]:
    """Names of classes/structs/unions declared in a header."""
    return [declaration.name for declaration in type_declarations(header)]


@dataclass(slots=True)
class ForwardCandidate:
    """One header that a TU might forward-declare instead of include."""

    tu: Path
    header: Path
    #: Types from the header that appear only in pointer/reference positions.
    forwardable_types: list[str] = field(default_factory=list)
    #: Types that appear but need the complete type (blocking).
    blocking_types: list[str] = field(default_factory=list)
    #: Line numbers in the TU where the forwardable types appear.
    evidence_lines: list[int] = field(default_factory=list)
    #: Lines in the header that would need a forward declaration.
    header_decl_lines: list[int] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return bool(self.forwardable_types) and not self.blocking_types


# A variable declared as ``X* v`` / ``X& v`` / ``X *const v``.
_DECL_VAR_RE = r"\b{n}\s*(?:\*\s*const|\*|&)\s*(\w+)"

# A forward declaration satisfies ``X* v`` only while ``v`` is never
# dereferenced.  ``v->member`` or ``v.member`` needs the complete type, even
# though the *type name* only ever appeared in a pointer position -- which is
# exactly the case that makes a purely textual check report a false negative.
_DEREF_RE = r"\b{var}\s*(?:->|\.)"


def _patterns_for(name: str) -> tuple[list[re.Pattern], re.Pattern, re.Pattern]:
    """Compile the usage patterns for one type name.

    Uses ``str.replace`` rather than ``str.format`` because several patterns
    contain literal ``{`` (brace initialisation), which ``format`` would
    choke on.
    """
    escaped = re.escape(name)
    needs = [
        re.compile(template.replace("{n}", escaped))
        for template in _NEEDS_DEFINITION_TEMPLATES
    ]
    pointer = re.compile(_POINTER_OR_REF_TEMPLATE.replace("{n}", escaped))
    decl_var = re.compile(_DECL_VAR_RE.replace("{n}", escaped))
    return needs, pointer, decl_var


def _scan_type_usage(source_text: str, name: str) -> tuple[list[int], bool]:
    """Return ``(lines_using_name, needs_definition)`` for one type name."""
    needs_patterns, pointer_pattern, decl_var_pattern = _patterns_for(name)
    ident = re.compile(rf"\b{re.escape(name)}\b")
    lines: list[int] = []
    needs_definition = False

    for number, line in enumerate(source_text.splitlines(), start=1):
        if not ident.search(line):
            continue
        lines.append(number)
        for pattern in needs_patterns:
            if pattern.search(line):
                needs_definition = True
                break
        # The name appears but never in a pointer/reference position, so a
        # forward declaration would not satisfy this use.
        if not pointer_pattern.search(line):
            needs_definition = True

    if not needs_definition:
        # Every mention was a pointer or reference.  Now check whether any of
        # those pointers is ever dereferenced: ``X* v; ... v->member`` needs
        # the complete type even though the type name itself never left a
        # pointer position.
        variables = {
            match.group(1)
            for match in decl_var_pattern.finditer(source_text)
        }
        for variable in variables:
            deref = re.compile(_DEREF_RE.replace("{var}", re.escape(variable)))
            if deref.search(source_text):
                needs_definition = True
                break

    return lines, needs_definition


def pointer_only_usage(source_text: str, name: str) -> bool:
    """``name`` 是否只以指针/引用出现——前向声明成立的必要条件。

    与 :func:`find_forward_declaration_candidates` 共用同一套判定，避免
    「建议前向声明」和「判定能否前向声明」两处规则各自漂移。
    """
    lines, needs_definition = _scan_type_usage(source_text, name)
    return bool(lines) and not needs_definition


def find_forward_declaration_candidates(
    scan,
    *,
    max_candidates: int = 20,
) -> list[ForwardCandidate]:
    """Find includes that could plausibly become forward declarations.

    Only *direct* includes of project headers are considered: a transitive
    include is the including header's owner's decision, not ours.
    """
    from ..models import HeaderKind

    candidates: list[ForwardCandidate] = []

    for tu in scan.ok_tus:
        source_text = _read(tu.source)
        if source_text is None:
            continue
        source_text = _strip_comments_preserving_lines(source_text)

        for header in tu.direct_includes():
            stats = scan.header_stats.get(header)
            if stats is None or stats.kind is not HeaderKind.PROJECT:
                continue

            types = declared_types(header)
            if not types:
                continue

            forwardable: list[str] = []
            blocking: list[str] = []
            evidence: list[int] = []
            header_lines: list[int] = []

            for name in types:
                lines, needs_definition = _scan_type_usage(source_text, name)
                if not lines:
                    # Declared but never mentioned: not evidence either way.
                    continue
                if needs_definition:
                    blocking.append(name)
                else:
                    forwardable.append(name)
                    evidence.extend(lines)

            if not forwardable:
                continue

            # Where in the header the forward declaration would go.
            header_text = _read(header)
            if header_text:
                for number, line in enumerate(
                    _strip_comments_preserving_lines(header_text).splitlines(), start=1
                ):
                    match = _DECL_RE.match(line)
                    if match and match.group(1) in forwardable:
                        header_lines.append(number)

            candidates.append(
                ForwardCandidate(
                    tu=tu.source,
                    header=header,
                    forwardable_types=sorted(set(forwardable)),
                    blocking_types=sorted(set(blocking)),
                    evidence_lines=sorted(set(evidence)),
                    header_decl_lines=header_lines,
                )
            )

    # Report every header with at least one forwardable type, even when other
    # types in the same header block a wholesale replacement.  "Split Handle
    # out but keep the rest" is actionable advice; filtering it away would
    # hide the finding.  ``is_actionable`` distinguishes the two cases for
    # the report.
    candidates = [c for c in candidates if c.forwardable_types]
    candidates.sort(
        key=lambda c: (
            c.is_actionable,
            len(c.forwardable_types),
            len(c.evidence_lines),
        ),
        reverse=True,
    )
    return candidates[:max_candidates]
