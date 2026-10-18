"""Tests for the "nothing to delete" diagnosis (``attrib.diagnosis``).

G2 measured the removal pipeline on three projects and got feasible
candidates on exactly one of them.  The other two produced an empty table --
and an empty table is read as "your build is fine", which is the opposite of
what happened.  These tests pin down the two properties that keep the empty
case honest:

* every rejected include line is still reported, **with the reason**, so the
  negative result is auditable instead of silent;
* the diagnosis never relaxes the gate.  It explains rejections; it does not
  turn them into suggestions.

The third property is a performance one: collecting rejections is opt-in, so
a default scan pays nothing for a feature it does not use.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.diagnosis import (  # noqa: E402
    BLOCK_REASONS,
    build_diagnosis,
)
from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers  # noqa: E402
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.models import (  # noqa: E402
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    IncludeEvent,
    ProjectScan,
    TranslationUnit,
)
from heavyheader.report.jsonio import scan_to_dict  # noqa: E402
from heavyheader.report.markdown import render_markdown  # noqa: E402


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
    """One shared header, three units, one removable and one real include."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="heavyheader-diagnosis-"))
        (self.dir / "proj").mkdir(parents=True, exist_ok=True)
        self.shared = self.dir / "proj" / "shared.h"
        self.unused = self.dir / "proj" / "unused.h"
        self.used = self.dir / "proj" / "used.h"
        # shared.h 自己要用 used.h 里的 SharedWidget；unused.h 里的东西不用。
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


class RejectionCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = _Tree()
        self.scan = self.tree.scan()
        self.sizes = HeaderSizes()
        build_header_stats(self.scan, self.sizes)
        self.kinds = classify_headers(
            self.scan.root, {h for tu in self.scan.ok_tus for h in tu.dependencies}
        )

    def test_rejected_line_records_why(self) -> None:
        blocked: list = []
        found = find_header_candidates(
            self.scan, self.sizes, self.kinds, blocked=blocked
        )
        self.assertEqual([c.header for c in found], [self.tree.unused])
        rejection = next(b for b in blocked if b.header == self.tree.used)
        self.assertEqual(rejection.reason, "owner_uses")
        self.assertEqual(rejection.site_file, self.tree.shared)
        self.assertEqual(rejection.reach, 3)
        # 证据必须具体到标识符：一句「在用」无法被读者核对。
        self.assertIn("SharedWidget", rejection.detail)

    def test_rejected_line_carries_a_readable_reason_label(self) -> None:
        blocked: list = []
        find_header_candidates(self.scan, self.sizes, self.kinds, blocked=blocked)
        for item in blocked:
            self.assertIn(item.reason, BLOCK_REASONS)
            self.assertTrue(item.reason_label)

    def test_reach_below_threshold_is_reported_as_such(self) -> None:
        blocked: list = []
        found = find_header_candidates(
            self.scan, self.sizes, self.kinds, min_reach=10, blocked=blocked
        )
        self.assertEqual(found, [])
        self.assertTrue(all(item.reason == "low_reach" for item in blocked))
        # 即使被判为「够不着门槛」，也要带上规模，否则诊断无法排序。
        self.assertTrue(any(item.amplified_lines >= 0 for item in blocked))

    def test_not_collecting_rejections_changes_nothing(self) -> None:
        """opt-in 收集：不传 blocked 时结果与从前完全一致。"""
        plain = find_header_candidates(self.scan, self.sizes, self.kinds)
        collected: list = []
        with_collection = find_header_candidates(
            self.scan, self.sizes, self.kinds, blocked=collected
        )
        self.assertEqual(
            [(str(c.site_file), str(c.header)) for c in plain],
            [(str(c.site_file), str(c.header)) for c in with_collection],
        )
        self.assertTrue(collected)


class DiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = _Tree()
        self.scan = self.tree.scan()
        self.sizes = HeaderSizes()
        build_header_stats(self.scan, self.sizes)
        self.kinds = classify_headers(
            self.scan.root, {h for tu in self.scan.ok_tus for h in tu.dependencies}
        )

    def _diagnosis(self, **kwargs):
        blocked: list = []
        found = find_header_candidates(
            self.scan, self.sizes, self.kinds, blocked=blocked, **kwargs
        )
        return build_diagnosis(self.scan, blocked, candidate_count=len(found))

    def test_candidate_count_and_reason_counts(self) -> None:
        diagnosis = self._diagnosis()
        self.assertEqual(diagnosis.candidate_count, 1)
        self.assertFalse(diagnosis.is_empty_handed)
        self.assertEqual(diagnosis.counts.get("owner_uses"), 1)

    def test_empty_handed_is_a_result_not_a_failure(self) -> None:
        diagnosis = self._diagnosis(min_reach=10)
        self.assertTrue(diagnosis.is_empty_handed)
        joined = " ".join(diagnosis.advice)
        self.assertIn("0 条", joined)
        self.assertIn("不会为了给出建议而降低", joined)

    def test_concentration_sums_to_a_share_of_the_total(self) -> None:
        diagnosis = self._diagnosis()
        self.assertTrue(diagnosis.concentration)
        self.assertGreater(diagnosis.total_attributed_lines, 0)
        self.assertGreater(diagnosis.top_share, 0.0)
        self.assertLessEqual(diagnosis.top_share, 1.0)
        for row in diagnosis.concentration:
            self.assertIn(row.kind, (HeaderKind.PROJECT, HeaderKind.SYSTEM))

    def test_advice_never_endorses_a_removal(self) -> None:
        for diagnosis in (self._diagnosis(), self._diagnosis(min_reach=10)):
            self.assertTrue(diagnosis.advice)
            self.assertTrue(
                any("verify" in item for item in diagnosis.advice),
                "诊断必须以「任何删除都要先过 verify」收尾",
            )

    def test_system_heavy_project_points_at_other_levers(self) -> None:
        """成本主要在系统头时，建议必须指向拆头/PCH，而不是删 include 行。"""
        scan = ProjectScan(
            root=Path("/proj"),
            compiler="g++",
            compiler_family=CompilerFamily.GCC,
            standard=None,
            tus=[],
        )
        scan.header_stats = {
            Path("/usr/include/vector"): HeaderStats(
                path=Path("/usr/include/vector"),
                kind=HeaderKind.SYSTEM,
                attributed_lines=900,
            ),
            Path("/proj/a.h"): HeaderStats(
                path=Path("/proj/a.h"), kind=HeaderKind.PROJECT, attributed_lines=100
            ),
        }
        diagnosis = build_diagnosis(scan, [])
        self.assertAlmostEqual(diagnosis.project_share_percent, 10.0, places=1)
        joined = " ".join(diagnosis.advice)
        self.assertIn("减少", joined)
        self.assertIn("--pch", joined)


class ReportRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = _Tree()
        self.scan = self.tree.scan()
        self.sizes = HeaderSizes()
        build_header_stats(self.scan, self.sizes)
        self.kinds = classify_headers(
            self.scan.root, {h for tu in self.scan.ok_tus for h in tu.dependencies}
        )
        blocked: list = []
        found = find_header_candidates(
            self.scan, self.sizes, self.kinds, blocked=blocked, min_reach=10
        )
        self.scan.site_diagnosis = build_diagnosis(
            self.scan, blocked, candidate_count=len(found)
        )

    def test_markdown_renders_the_section_when_the_gate_is_empty(self) -> None:
        text = render_markdown(self.scan)
        self.assertIn("没有可删的了", text)
        self.assertIn("拦下原因分布", text)
        self.assertIn("下一步能做什么", text)

    def test_markdown_says_nothing_when_not_computed(self) -> None:
        self.scan.site_diagnosis = None
        text = render_markdown(self.scan)
        self.assertNotIn("没有可删的了", text)
        self.assertNotIn("成本去向", text)

    def test_html_renders_the_section(self) -> None:
        from heavyheader.report.html import render_html

        page = render_html(self.scan)
        self.assertIn("没有可删的了", page)
        self.assertIn("拦下原因", page)
        # 建议列表必须以「删除要先过 verify」收尾，不能只报原因不给边界。
        self.assertIn("本诊断不构成删除建议", page)

    def test_html_falls_back_to_the_plain_note(self) -> None:
        from heavyheader.report.html import render_html

        self.scan.site_diagnosis = None
        page = render_html(self.scan)
        self.assertNotIn("没有可删的了", page)
        self.assertIn("没有发现值得精简的 include", page)

    def test_json_exports_the_diagnosis(self) -> None:
        payload = scan_to_dict(self.scan)
        payload_diag = payload["site_diagnosis"]
        self.assertIsNotNone(payload_diag)
        self.assertEqual(payload_diag["candidate_count"], 0)
        self.assertTrue(payload_diag["blocked"])
        self.assertTrue(payload_diag["advice"])
        self.assertIn("reason_label", payload_diag["blocked"][0])


class CliFlagTests(unittest.TestCase):
    def test_include_system_sites_is_accepted(self) -> None:
        from heavyheader.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["scan", ".", "--header-sites", "--include-system-sites"]
        )
        self.assertTrue(args.include_system_sites)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
