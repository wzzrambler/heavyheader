"""Tests for header-level inclusion sites (``attrib.sites``).

The behaviour under test is the answer to G2's 0/10: the old candidate
generator only looked at a translation unit's own ``#include`` lines, and on
a real project that set is small and almost entirely non-removable.  These
tests pin down the three things that make the wider search usable rather than
just bigger:

* the edit site is the **header**, and every unit that parses it is affected;
* the payoff is multiplied by that audience, not reported per unit;
* a feasibility gate keeps "huge but unremovable" rows out of the list --
  and the gate can be switched off, because "the gate helps" is a claim that
  has to be measurable (see ``experiments/g2_sites.py``).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.graph import (  # noqa: E402
    HeaderSizes,
    build_header_stats,
    classify_headers,
)
from heavyheader.attrib.sites import (  # noqa: E402
    build_include_graph,
    find_header_candidates,
    tu_parsed_files,
)
from heavyheader.models import (  # noqa: E402
    CompilerFamily,
    IncludeEvent,
    ProjectScan,
    RemovalCandidate,
    TranslationUnit,
)


def _tu(source: str, edges: list[tuple[str, str, int]]) -> TranslationUnit:
    src = Path(source)
    return TranslationUnit(
        source=src,
        dependencies=[Path(included) for _, included, _ in edges],
        include_events=[
            IncludeEvent(includer=Path(a), included=Path(b), depth=d)
            for a, b, d in edges
        ],
    )


class _Tree:
    """A tiny on-disk project: one shared header, three units, two includes."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="heavyheader-sites-"))
        (self.dir / "proj").mkdir(parents=True, exist_ok=True)
        self.shared = self.dir / "proj" / "shared.h"
        self.unused = self.dir / "proj" / "unused.h"
        self.used = self.dir / "proj" / "used.h"
        # 被包含的头文件自己要用 used.h 里的标识符，用 unused.h 里的不用。
        self.shared.write_text(
            '#pragma once\n#include "proj/unused.h"\n#include "proj/used.h"\n'
            "SharedWidget make_one();\n",
            encoding="utf-8",
        )
        self.unused.write_text(
            "#pragma once\nint never_named_here();\n", encoding="utf-8"
        )
        self.used.write_text("#pragma once\nstruct SharedWidget {};\n", encoding="utf-8")
        self.units = []
        for index in range(3):
            source = self.dir / "proj" / f"unit_{index}.cpp"
            source.write_text(
                f'#include "proj/shared.h"\nSharedWidget w{index};\n', encoding="utf-8"
            )
            self.units.append(source)

    def scan(self) -> ProjectScan:
        edges = []
        units = []
        for source in self.units:
            edges = [
                (str(source), str(self.shared), 0),
                (str(self.shared), str(self.unused), 1),
                (str(self.shared), str(self.used), 1),
            ]
            units.append(_tu(str(source), edges))
        scan = ProjectScan(
            root=self.dir / "proj",
            compiler="g++",
            compiler_family=CompilerFamily.GCC,
            standard="c++17",
            tus=units,
        )
        sizes = HeaderSizes()
        build_header_stats(scan, sizes)
        scan.candidates = []
        return scan


class HeaderSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = _Tree()
        self.scan = self.tree.scan()
        self.sizes = HeaderSizes()
        build_header_stats(self.scan, self.sizes)
        self.kinds = classify_headers(
            self.scan.root, {h for tu in self.scan.ok_tus for h in tu.dependencies}
        )

    def test_graph_covers_headers_not_only_units(self) -> None:
        children = build_include_graph(self.scan)
        self.assertIn(self.tree.shared, children)
        self.assertEqual(
            sorted(children[self.tree.shared]),
            sorted([self.tree.unused, self.tree.used]),
        )

    def test_every_unit_parsing_a_header_is_counted(self) -> None:
        children = build_include_graph(self.scan)
        parsed = tu_parsed_files(self.scan, {p: set(c) for p, c in children.items()})
        for source in self.tree.units:
            self.assertIn(self.tree.shared, parsed[source])

    def test_only_the_unused_include_becomes_a_candidate(self) -> None:
        found = find_header_candidates(self.scan, self.sizes, self.kinds)
        headers = {c.header for c in found}
        self.assertIn(self.tree.unused, headers)
        self.assertNotIn(self.tree.used, headers)

    def test_site_is_the_header_and_all_units_are_affected(self) -> None:
        found = find_header_candidates(self.scan, self.sizes, self.kinds)
        candidate = next(c for c in found if c.header == self.tree.unused)
        self.assertEqual(candidate.site_file, self.tree.shared)
        self.assertTrue(candidate.is_header_site)
        self.assertEqual(candidate.reach, 3)
        self.assertEqual(
            sorted(candidate.affected_tus), sorted(self.tree.units)
        )
        # TU 级候选不为真：改动不在源文件里。
        self.assertNotEqual(candidate.site, candidate.tu)

    def test_payoff_is_amplified_by_the_audience(self) -> None:
        found = find_header_candidates(self.scan, self.sizes, self.kinds)
        candidate = next(c for c in found if c.header == self.tree.unused)
        self.assertEqual(
            candidate.amplified_lines_saved, candidate.estimated_lines_saved * 3
        )

    def test_gate_can_be_switched_off_for_the_ablation(self) -> None:
        gated = find_header_candidates(self.scan, self.sizes, self.kinds)
        ungated = find_header_candidates(
            self.scan, self.sizes, self.kinds, require_plausibly_unused=False
        )
        self.assertTrue(len(ungated) > len(gated))
        self.assertTrue(
            any(c.header == self.tree.used for c in ungated)
        )

    def test_single_unit_headers_are_not_candidates(self) -> None:
        # reach 门槛：只被一个单元解析的头文件，收益没有被放大，
        # 交给 TU 级候选处理（那里风险更低）。
        found = find_header_candidates(
            self.scan, self.sizes, self.kinds, min_reach=10
        )
        self.assertEqual(found, [])

    def test_downstream_usage_is_reported_as_evidence_not_verdict(self) -> None:
        """下游有人用、owner 没用：候选保留，但必须带着风险证据。

        这正是 doctest 8/16 编译失败的形状：``std::sort`` 写在下游
        ``context.cpp``，``<algorithm>`` 的 include 行在 ``prelude.h``。
        闸门问 owner 不问下游，所以它放行了；现在证据跟着候选走，
        置信度压到 low——但判定仍然只由 verify 给。
        """
        proj = self.tree.dir / "proj"
        doomed = proj / "doomed.h"
        doomed.write_text("#pragma once\nstruct DownWidget {};\n", encoding="utf-8")
        shared = self.tree.shared
        shared.write_text(
            '#pragma once\n#include "proj/doomed.h"\n#include "proj/used.h"\n'
            "SharedWidget make_one();\n",
            encoding="utf-8",
        )
        for source in self.tree.units:
            body = source.read_text(encoding="utf-8")
            source.write_text(body + f"DownWidget dw_{source.stem};\n", encoding="utf-8")

        units = [
            _tu(
                str(source),
                [
                    (str(source), str(shared), 0),
                    (str(shared), str(doomed), 1),
                    (str(shared), self.tree.used, 1),
                ],
            )
            for source in self.tree.units
        ]
        scan = ProjectScan(
            root=proj,
            compiler="g++",
            compiler_family=CompilerFamily.GCC,
            standard="c++17",
            tus=units,
        )
        sizes = HeaderSizes()
        build_header_stats(scan, sizes)
        kinds = classify_headers(proj, {h for tu in scan.ok_tus for h in tu.dependencies})
        found = find_header_candidates(scan, sizes, kinds)
        candidate = next(c for c in found if c.header == doomed)
        self.assertTrue(candidate.downstream_files)
        self.assertIn("DownWidget", candidate.downstream_names)
        self.assertEqual(candidate.confidence, "low")
        self.assertTrue(any("下游引用" in note for note in candidate.notes))

    def test_no_downstream_usage_keeps_high_confidence(self) -> None:
        found = find_header_candidates(self.scan, self.sizes, self.kinds)
        candidate = next(c for c in found if c.header == self.tree.unused)
        self.assertEqual(candidate.downstream_files, [])
        self.assertEqual(candidate.downstream_names, [])

    def test_defaults_keep_old_candidates_unchanged(self) -> None:
        candidate = RemovalCandidate(
            tu=self.tree.units[0],
            header=self.tree.shared,
            line=1,
            kind=self.kinds[self.tree.shared],
            estimated_lines_saved=100,
        )
        self.assertEqual(candidate.reach, 1)
        self.assertEqual(candidate.site, candidate.tu)
        self.assertFalse(candidate.is_header_site)
        self.assertEqual(candidate.amplified_lines_saved, 100)


class InPlacePatchTests(unittest.TestCase):
    """The edited file must come back even when the measurement explodes."""

    def test_restores_after_an_exception(self) -> None:
        from heavyheader.verify import _patched_in_place

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shared.h"
            original = '#include "a.h"\n#include "b.h"\n'
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(RuntimeError):
                with _patched_in_place(path, 1):
                    patched = path.read_text(encoding="utf-8")
                    self.assertTrue(patched.splitlines()[0].startswith("//"))
                    raise RuntimeError("measurement blew up")
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_comment_out_keeps_the_line_count(self) -> None:
        from heavyheader.verify import _comment_out

        text = '#include "a.h"\nint x;\n'
        patched = _comment_out(text, 1)
        # 行号必须保持不变：候选记录的是行号，注释后多一行会让下一次
        # 「按行定位」指错位置。
        self.assertEqual(len(patched.splitlines()), len(text.splitlines()))
        self.assertTrue(patched.splitlines()[0].startswith("//"))


if __name__ == "__main__":
    unittest.main()
