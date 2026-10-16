"""Tests for forward-declaration suggestions.

The important tests here are the *negative* ones.  A false positive here tells
a developer to make a change that will not compile, which is worse than saying
nothing -- so the suite spends most of its effort proving the detector stays
quiet when it should.
"""

from __future__ import annotations

from pathlib import Path

from heavyheader.attrib.forward import (
    ForwardCandidate,
    declared_types,
    find_forward_declaration_candidates,
)
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    IncludeEvent,
    ProjectScan,
    TranslationUnit,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _scan(root: Path, tu: TranslationUnit, headers: list[Path]) -> ProjectScan:
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
    )
    for header in headers:
        scan.header_stats[header] = HeaderStats(
            path=header, kind=HeaderKind.PROJECT, tu_reach=1
        )
    return scan


# --------------------------------------------------------------------------
# declared_types
# --------------------------------------------------------------------------


def test_declared_types_finds_classes_and_structs(tmp_path):
    header = _write(
        tmp_path / "a.h",
        "#pragma once\n"
        "class Widget {};\n"
        "struct Point { int x, y; };\n"
        "template <typename T> class Holder {};\n"
        "int plain_function();\n"
        "enum class Color { Red };\n",
    )
    assert set(declared_types(header)) == {"Widget", "Point", "Holder"}


def test_declared_types_ignores_comments(tmp_path):
    header = _write(
        tmp_path / "b.h",
        "// class Ghost {};\n"
        "/* struct Phantom {}; */\n"
        "int real();\n",
    )
    assert declared_types(header) == []


def test_declared_types_handles_attributes(tmp_path):
    header = _write(
        tmp_path / "c.h",
        "class __attribute__((visibility(\"default\"))) Visible {};\n",
    )
    assert declared_types(header) == ["Visible"]


# --------------------------------------------------------------------------
# positive cases
# --------------------------------------------------------------------------


def test_pointer_only_usage_is_a_candidate(tmp_path):
    header = _write(tmp_path / "widget.h", "#pragma once\nclass Widget {};\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "widget.h"\n'
        "void f(Widget* w);\n"
        "void g(const Widget& w);\n"
        "int main() { Widget* p = nullptr; return 0; }\n",
    )
    tu = TranslationUnit(
        source=source,
        include_events=[
            IncludeEvent(includer=source, included=header, depth=0)
        ],
    )
    scan = _scan(tmp_path, tu, [header])
    candidates = find_forward_declaration_candidates(scan)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.is_actionable
    assert candidate.forwardable_types == ["Widget"]
    assert candidate.blocking_types == []


# --------------------------------------------------------------------------
# negative cases: each of these must produce NO candidate
# --------------------------------------------------------------------------


def test_member_access_blocks_the_suggestion(tmp_path):
    """``w->value`` needs the complete type."""
    header = _write(tmp_path / "widget.h", "#pragma once\nclass Widget { public: int value; };\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "widget.h"\n'
        "int f(Widget* w) { return w->value; }\n",
    )
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    assert find_forward_declaration_candidates(scan) == []


def test_by_value_object_blocks_the_suggestion(tmp_path):
    header = _write(tmp_path / "widget.h", "#pragma once\nclass Widget {};\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "widget.h"\nWidget global;\n',
    )
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    assert find_forward_declaration_candidates(scan) == []


def test_sizeof_blocks_the_suggestion(tmp_path):
    header = _write(tmp_path / "widget.h", "#pragma once\nclass Widget {};\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "widget.h"\nconstexpr auto N = sizeof(Widget);\n',
    )
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    assert find_forward_declaration_candidates(scan) == []


def test_inheritance_blocks_the_suggestion(tmp_path):
    header = _write(tmp_path / "base.h", "#pragma once\nclass Base {};\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "base.h"\nclass Derived : public Base {};\n',
    )
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    assert find_forward_declaration_candidates(scan) == []


def test_unused_header_is_not_reported(tmp_path):
    """A header whose types never appear is not forward-declaration evidence.

    It might be removable outright -- that is the usage heuristic's job, not
    this module's.  Reporting it here would conflate two different findings.
    """
    header = _write(tmp_path / "unused.h", "#pragma once\nclass Widget {};\n")
    source = _write(tmp_path / "main.cpp", '#include "unused.h"\nint main() { return 0; }\n')
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    assert find_forward_declaration_candidates(scan) == []


def test_header_without_declarations_is_skipped(tmp_path):
    header = _write(tmp_path / "macros.h", "#pragma once\n#define FOO 1\n")
    source = _write(tmp_path / "main.cpp", '#include "macros.h"\nint main() { return FOO; }\n')
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    assert find_forward_declaration_candidates(scan) == []


def test_mixed_types_are_split_into_forwardable_and_blocking(tmp_path):
    """One forwardable type plus one blocking type must be reported as such.

    The report says the whole include cannot be replaced, only the
    forwardable part split out -- silently suggesting a full replacement
    here would break the build.
    """
    header = _write(
        tmp_path / "mixed.h",
        "#pragma once\nclass Handle {};\nclass Value { public: int n; };\n",
    )
    source = _write(
        tmp_path / "main.cpp",
        '#include "mixed.h"\n'
        "void use(Handle* h);\n"
        "int read(Value* v) { return v->n; }\n",
    )
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    candidates = find_forward_declaration_candidates(scan)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.forwardable_types == ["Handle"]
    assert candidate.blocking_types == ["Value"]
    # Not actionable as a whole-include replacement.
    assert not candidate.is_actionable


def test_transitive_includes_are_not_reported(tmp_path):
    """Only direct includes are our call; transitive ones belong to the
    including header's owner."""
    top = _write(tmp_path / "top.h", '#pragma once\n#include "deep.h"\n')
    deep = _write(tmp_path / "deep.h", "#pragma once\nclass Deep {};\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "top.h"\nvoid f(Deep* d);\n',
    )
    tu = TranslationUnit(
        source=source,
        include_events=[
            IncludeEvent(includer=source, included=top, depth=0),
            IncludeEvent(includer=top, included=deep, depth=1),
        ],
    )
    scan = _scan(tmp_path, tu, [top, deep])
    # top.h declares nothing, deep.h is transitive -> nothing to report.
    assert find_forward_declaration_candidates(scan) == []


def test_dereferenced_pointer_blocks_the_suggestion(tmp_path):
    """``X* v; v->member`` needs the complete type.

    The type name only ever appears in a pointer position, so a purely
    textual check calls it forwardable -- a false negative that would tell a
    developer to make a change which does not compile.  Tracking the pointer
    variables and checking for ``->`` / ``.'' is what catches it.
    """
    header = _write(tmp_path / "widget.h", "#pragma once\nclass Widget { public: int value; };\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "widget.h"\n'
        "int f(Widget* w) { return w->value; }\n",
    )
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    assert find_forward_declaration_candidates(scan) == []


def test_undereferenced_pointer_still_qualifies(tmp_path):
    """The positive control for the test above: never dereferenced, still fine."""
    header = _write(tmp_path / "widget.h", "#pragma once\nclass Widget {};\n")
    source = _write(
        tmp_path / "main.cpp",
        '#include "widget.h"\n'
        "void store(Widget* w);\n"
        "void observe(const Widget& w);\n"
        "int main() { return 0; }\n",
    )
    tu = TranslationUnit(
        source=source,
        include_events=[IncludeEvent(includer=source, included=header, depth=0)],
    )
    scan = _scan(tmp_path, tu, [header])
    candidates = find_forward_declaration_candidates(scan)
    assert len(candidates) == 1
    assert candidates[0].is_actionable
