"""Tests for the cost-attribution engine.

These are the most valuable tests in the suite.  The exclusive-closure
computation is the one piece of logic where a subtle mistake produces
*plausible but wrong* output rather than a crash, and a bug there silently
disables the entire candidate detector.  A regression test pins the behaviour
down.
"""

from __future__ import annotations

from pathlib import Path

from heavyheader.attrib.graph import (
    HeaderSizes,
    build_header_stats,
    classify_headers,
    closure_of,
    find_removal_candidates,
    redundant_reparse_warnings,
)
from heavyheader.attrib.usage import UsageVerdict, analyse_usage
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    IncludeEvent,
    ProjectScan,
    TranslationUnit,
)


def _tu(source: str, edges: list[tuple[str, str, int]]) -> TranslationUnit:
    """Build a TU from ``(includer, included, depth)`` triples."""
    src = Path(source)
    return TranslationUnit(
        source=src,
        dependencies=[Path(i) for _, i, _ in edges],
        include_events=[
            IncludeEvent(includer=Path(a), included=Path(b), depth=d)
            for a, b, d in edges
        ],
    )


def _write(root: Path, name: str, lines: int) -> Path:
    """Materialise a header of ``lines`` lines so size lookups are real."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"int symbol_{name.replace('/', '_')}_{i}();" for i in range(lines))
    path.write_text(body + "\n", encoding="utf-8")
    return path


def _scan(tus: list[TranslationUnit], root: Path) -> ProjectScan:
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=tus,
    )
    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    scan.candidates = find_removal_candidates(
        scan, sizes, classify_headers(root, set(scan.header_stats))
    )
    return scan


# --------------------------------------------------------------------------
# closure
# --------------------------------------------------------------------------


def test_closure_follows_transitive_includes():
    adjacency = {
        Path("a.h"): [Path("b.h")],
        Path("b.h"): [Path("c.h")],
        Path("c.h"): [],
    }
    assert closure_of(adjacency, Path("a.h")) == {Path("b.h"), Path("c.h")}


def test_closure_excludes_the_start_node():
    adjacency = {Path("a.h"): [Path("a.h")]}  # self-include, degenerate
    assert closure_of(adjacency, Path("a.h")) == set()


def test_closure_survives_cycles():
    adjacency = {
        Path("a.h"): [Path("b.h")],
        Path("b.h"): [Path("a.h")],
    }
    # A cycle cannot happen with include guards, but a parser fed malformed
    # input must still terminate.
    assert closure_of(adjacency, Path("a.h")) == {Path("b.h")}


def test_closure_handles_deep_chains_without_recursion_error():
    depth = 5000
    adjacency = {Path(f"h{i}.h"): [Path(f"h{i + 1}.h")] for i in range(depth)}
    adjacency[Path(f"h{depth}.h")] = []
    assert len(closure_of(adjacency, Path("h0.h"))) == depth


# --------------------------------------------------------------------------
# the regression that matters: exclusive closure
# --------------------------------------------------------------------------


def test_exclusive_closure_is_not_emptied_by_the_candidate_itself(tmp_path):
    """The bug this guards against.

    An earlier implementation built the "already paid for" union from *all*
    direct includes, including the candidate under consideration.  That put
    the candidate's own closure into the shared set, made every exclusive
    set empty, and silently produced zero candidates on every project.

    The symptom is indistinguishable from "nothing to optimise", which is
    why it needs a test rather than a careful reading.
    """
    root = tmp_path
    _write(root, "cheap.h", 2)
    _write(root, "expensive.h", 3)
    _write(root, "huge_a.h", 40)
    _write(root, "huge_b.h", 50)
    source = root / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    main = _tu(
        str(source),
        [
            (str(source), str(root / "cheap.h"), 0),
            (str(source), str(root / "expensive.h"), 0),
            (str(root / "expensive.h"), str(root / "huge_a.h"), 1),
            (str(root / "huge_a.h"), str(root / "huge_b.h"), 2),
        ],
    )
    scan = _scan([main], root)

    assert scan.candidates, "candidate detector produced nothing"
    expensive = [c for c in scan.candidates if c.header == root / "expensive.h"]
    assert len(expensive) == 1
    candidate = expensive[0]
    # huge_a.h and huge_b.h are reachable only through expensive.h.
    assert set(candidate.exclusive_headers) == {
        root / "huge_a.h",
        root / "huge_b.h",
    }
    assert candidate.estimated_lines_saved == 90


def test_shared_closure_is_not_counted_as_a_saving(tmp_path):
    """If another include already pays for a header, deleting this one saves nothing."""
    root = tmp_path
    _write(root, "a.h", 5)
    _write(root, "b.h", 5)
    _write(root, "shared.h", 30)
    source = root / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    main = _tu(
        str(source),
        [
            (str(source), str(root / "a.h"), 0),
            (str(source), str(root / "b.h"), 0),
            (str(root / "a.h"), str(root / "shared.h"), 1),
            (str(root / "b.h"), str(root / "shared.h"), 1),
        ],
    )
    scan = _scan([main], root)
    # Neither include owns shared.h exclusively.
    for candidate in scan.candidates:
        assert root / "shared.h" not in candidate.exclusive_headers


def test_only_direct_includes_become_candidates(tmp_path):
    root = tmp_path
    _write(root, "top.h", 5)
    _write(root, "deep.h", 50)
    source = root / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    main = _tu(
        str(source),
        [
            (str(source), str(root / "top.h"), 0),
            (str(root / "top.h"), str(root / "deep.h"), 1),
        ],
    )
    scan = _scan([main], root)
    assert scan.candidates, "expected the direct include to be a candidate"
    assert all(c.header == root / "top.h" for c in scan.candidates)
    # deep.h is reached only through top.h, so removing top.h saves it.
    assert scan.candidates[0].exclusive_headers == [root / "deep.h"]


# --------------------------------------------------------------------------
# header statistics
# --------------------------------------------------------------------------


def test_attributed_lines_is_lines_times_reach(tmp_path):
    root = tmp_path
    _write(root, "common.h", 7)
    a = root / "a.cpp"
    b = root / "b.cpp"
    for path in (a, b):
        path.write_text("int main() { return 0; }\n", encoding="utf-8")

    tu_a = _tu(str(a), [(str(a), str(root / "common.h"), 0)])
    tu_b = _tu(str(b), [(str(b), str(root / "common.h"), 0)])
    scan = _scan([tu_a, tu_b], root)
    stats = scan.header_stats[root / "common.h"]
    assert stats.tu_reach == 2
    assert stats.attributed_lines == stats.line_count * 2


def test_redundant_reparse_detects_missing_guards(tmp_path):
    """An unguarded header is parsed once per inclusion, so events > TUs."""
    root = tmp_path
    _write(root, "p1.h", 2)
    _write(root, "p2.h", 2)
    _write(root, "u.h", 3)
    source = root / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    # p1.h and p2.h both include the unguarded u.h; main.cpp includes it too.
    # GCC prints one -H line per *processed* inclusion, so an unguarded header
    # shows up three times while a guarded one would show up once.
    main = _tu(
        str(source),
        [
            (str(source), str(root / "p1.h"), 0),
            (str(root / "p1.h"), str(root / "u.h"), 1),
            (str(source), str(root / "p2.h"), 0),
            (str(root / "p2.h"), str(root / "u.h"), 1),
            (str(source), str(root / "u.h"), 0),
        ],
    )
    scan = _scan([main], root)
    warnings = dict(
        (path, count) for path, count, _ in redundant_reparse_warnings(scan)
    )
    assert warnings[root / "u.h"] == 3
    stats = scan.header_stats[root / "u.h"]
    assert stats.inclusion_events == 3
    assert stats.tu_reach == 1
    assert stats.inclusion_events > stats.tu_reach


def test_guarded_header_is_not_flagged_as_redundant(tmp_path):
    """A guarded header appears once in -H output, so it can never look redundant.

    This is the fixture that matters: it models what GCC *actually* prints for
    a guarded header pulled in by two different parents -- a single line, not
    one per parent.  Modelling it as two lines would make the tool report
    every well-guarded header in the project as a problem.
    """
    root = tmp_path
    _write(root, "a.h", 5)
    _write(root, "b.h", 5)
    _write(root, "g.h", 30)
    source = root / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    # g.h is included by both a.h and b.h, but the guard means the compiler
    # only *processes* the first one, so -H prints exactly one line for it.
    main = _tu(
        str(source),
        [
            (str(source), str(root / "a.h"), 0),
            (str(root / "a.h"), str(root / "g.h"), 1),
            (str(source), str(root / "b.h"), 0),
        ],
    )
    scan = _scan([main], root)
    assert not redundant_reparse_warnings(scan)
    stats = scan.header_stats[root / "g.h"]
    assert stats.inclusion_events == 1
    assert stats.tu_reach == 1


def test_classify_headers_splits_project_from_foreign(tmp_path):
    root = tmp_path / "proj"
    _write(root, "src/a.h", 3)
    kinds = classify_headers(root, {root / "src" / "a.h", Path("/usr/include/vector")})
    assert kinds[root / "src" / "a.h"] is HeaderKind.PROJECT
    assert kinds[Path("/usr/include/vector")] is HeaderKind.SYSTEM


# --------------------------------------------------------------------------
# usage heuristic
# --------------------------------------------------------------------------


def test_usage_flags_a_header_nothing_references(tmp_path):
    header = tmp_path / "unused.h"
    header.write_text("int never_called();\n", encoding="utf-8")
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    signal = analyse_usage(source, header)
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED


def test_usage_flags_a_header_whose_symbol_is_used(tmp_path):
    header = tmp_path / "used.h"
    header.write_text("struct Widget { int value; };\n", encoding="utf-8")
    source = tmp_path / "main.cpp"
    source.write_text(
        '#include "used.h"\nint main() { Widget w; return w.value; }\n',
        encoding="utf-8",
    )
    signal = analyse_usage(source, header)
    assert signal.verdict is UsageVerdict.IN_USE
    assert "Widget" in signal.shared_identifiers


def test_usage_ignores_identifiers_inside_comments_and_strings(tmp_path):
    header = tmp_path / "h.h"
    header.write_text("int real();\n", encoding="utf-8")
    source = tmp_path / "main.cpp"
    source.write_text(
        'int main() { /* real() is mentioned here */ return 0; }\n',
        encoding="utf-8",
    )
    signal = analyse_usage(source, header)
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED


def test_usage_cannot_judge_an_aggregation_header(tmp_path):
    """A header that only forwards other headers cannot be judged.

    Reporting it as "plausibly unused" would be a false positive on one of the
    most common patterns in large C++ codebases: the aggregation header exists
    to hand ``<vector>`` and friends to whoever includes *it*.
    """
    header = tmp_path / "prelude.h"
    header.write_text(
        "#pragma once\n#include <vector>\n#include <map>\n", encoding="utf-8"
    )
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    signal = analyse_usage(source, header)
    assert signal.verdict is UsageVerdict.UNKNOWN


def test_usage_cannot_judge_a_macro_only_header(tmp_path):
    header = tmp_path / "macros.h"
    header.write_text("#define FOO 1\n#define BAR 2\n", encoding="utf-8")
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    signal = analyse_usage(source, header)
    assert signal.verdict is UsageVerdict.UNKNOWN


def test_usage_cannot_judge_a_comment_only_header(tmp_path):
    header = tmp_path / "notes.h"
    header.write_text("// nothing to see here\n/* really */\n", encoding="utf-8")
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    signal = analyse_usage(source, header)
    assert signal.verdict is UsageVerdict.UNKNOWN


def test_usage_reports_unknown_when_a_file_is_unreadable(tmp_path):
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    signal = analyse_usage(source, tmp_path / "does_not_exist.h")
    assert signal.verdict is UsageVerdict.UNKNOWN
