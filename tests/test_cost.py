"""Tests for the measured-cost model (``attrib.cost``).

The behaviour these pin down is the reason the module exists: ranking by
attributed lines put a header that **cannot be removed** at rank 1, while the
one removal that actually compiled and actually saved 3.01% sat at rank 2.
Ranking by measured microseconds swaps them.  If a future change makes the
two models order those the same way again, these tests should fail loudly
rather than let the tool quietly go back to ranking by proxy.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from heavyheader.attrib.cost import HeaderCosts, parse_trace


def _trace(path: Path, spans: list[tuple[str, int, int]]) -> Path:
    """Write a minimal ``-ftime-trace`` file with ``Source`` b/e pairs.

    ``spans`` are ``(detail, start_us, end_us)``.  All share one tid/pid, so
    nesting is expressed purely by the intervals -- same as the real thing.
    """
    events = []
    for index, (detail, start, end) in enumerate(spans):
        events.append(
            {
                "pid": 1,
                "tid": 2,
                "id": index,
                "ph": "b",
                "ts": start,
                "name": "Source",
                "args": {"detail": detail},
            }
        )
        events.append({"pid": 1, "tid": 2, "id": index, "ph": "e", "ts": end, "name": "Source"})
    path.write_text(json.dumps({"traceEvents": events}), encoding="utf-8")
    return path


class ParseTraceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="heavyheader-cost-"))

    def test_nested_child_time_is_subtracted_from_the_parent(self) -> None:
        # parent spans 0..100 and fully contains a child at 20..80, so the
        # parent's *self* time is 40, not 100.  Getting this wrong is what
        # makes umbrella headers look expensive.
        trace = _trace(
            self.dir / "a.json",
            [
                ("/proj/parent.h", 0, 100),
                ("/proj/child.h", 20, 80),
            ],
        )
        parsed = parse_trace(trace)
        self.assertAlmostEqual(parsed["/proj/parent.h"][0], 40.0)
        self.assertAlmostEqual(parsed["/proj/child.h"][0], 60.0)

    def test_siblings_do_not_subtract_from_each_other(self) -> None:
        trace = _trace(
            self.dir / "b.json",
            [
                ("/proj/a.h", 0, 50),
                ("/proj/b.h", 60, 110),
            ],
        )
        parsed = parse_trace(trace)
        self.assertAlmostEqual(parsed["/proj/a.h"][0], 50.0)
        self.assertAlmostEqual(parsed["/proj/b.h"][0], 50.0)

    def test_repeated_header_accumulates(self) -> None:
        trace = _trace(
            self.dir / "c.json",
            [
                ("/proj/x.h", 0, 10),
                ("/proj/x.h", 100, 115),
            ],
        )
        parsed = parse_trace(trace)
        self.assertAlmostEqual(parsed["/proj/x.h"][0], 25.0)
        self.assertEqual(parsed["/proj/x.h"][1], 2)

    def test_missing_or_broken_file_yields_nothing(self) -> None:
        # "we could not parse it" must never become "this header is free".
        self.assertEqual(parse_trace(self.dir / "nope.json"), {})
        broken = self.dir / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        self.assertEqual(parse_trace(broken), {})

    def test_paths_are_matched_case_and_separator_insensitively(self) -> None:
        trace = _trace(self.dir / "d.json", [("C:\\Proj\\X.h", 0, 10)])
        parsed = parse_trace(trace)
        self.assertIn("c:/proj/x.h", parsed)


class HeaderCostsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.costs = HeaderCosts(basis="ftime-trace")
        self.costs.self_us["/proj/a.h"] = 300.0
        self.costs.count["/proj/a.h"] = 3
        self.costs.self_us["/proj/b.h"] = 40.0
        self.costs.count["/proj/b.h"] = 1

    def test_per_parse_is_the_average_not_the_sum(self) -> None:
        self.assertAlmostEqual(self.costs.per_parse_us(Path("/proj/a.h")), 100.0)

    def test_unknown_header_reports_none_instead_of_zero(self) -> None:
        # Zero would rank unmeasured headers as free, which silently puts
        # them at the bottom of a list sorted by descending saving.
        self.assertIsNone(self.costs.per_parse_us(Path("/proj/missing.h")))
        self.assertIsNone(self.costs.total_us([Path("/proj/missing.h")]))

    def test_total_sums_what_is_known_and_reports_partial_coverage(self) -> None:
        total = self.costs.total_us(
            [Path("/proj/a.h"), Path("/proj/b.h"), Path("/proj/missing.h")]
        )
        self.assertAlmostEqual(total, 140.0)
        self.assertEqual(self.costs.coverage([Path("/proj/a.h"), Path("/proj/missing.h")]), (1, 2))

    def test_empty_costs_are_unavailable(self) -> None:
        empty = HeaderCosts()
        self.assertFalse(empty.available)
        self.assertTrue(self.costs.available)


class CostRankingTest(unittest.TestCase):
    """The regression that motivated the whole module.

    Numbers are taken from the doctest run (24 units): ``algorithm`` has the
    most attributed lines but measured cheaper than ``unordered_set``, and
    ``unordered_set`` is the only one of the five that actually compiles
    without its include.
    """

    def test_more_lines_does_not_mean_more_cost(self) -> None:
        costs = HeaderCosts(basis="ftime-trace")
        # algorithm: 216456 lines, 0.3583 s of self time across the run
        costs.self_us["/proj/algorithm"] = 358_300.0
        costs.count["/proj/algorithm"] = 24
        # unordered_set: 177024 lines, 0.6136 s -- fewer lines, more time
        costs.self_us["/proj/unordered_set"] = 613_600.0
        costs.count["/proj/unordered_set"] = 24

        algorithm = costs.total_us([Path("/proj/algorithm")])
        unordered_set = costs.total_us([Path("/proj/unordered_set")])
        self.assertGreater(unordered_set, algorithm)
        self.assertGreater(
            unordered_set / algorithm, 1.5,
            "差距必须足够大，否则换排序依据就没有意义",
        )


if __name__ == "__main__":
    unittest.main()
