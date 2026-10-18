"""闸门的两档松紧，以及「只验编译」的档位。

两个能力解决的其实是同一个问题：**闸门放行不等于删了能编**。doctest 上放行
16 条、8 条编译失败（`g2-sites-doctest-all16`），这让「候选」这个词变得含糊。
于是：

* ``include_unknown=True``（CLI ``--allow-unknown-sites``）把「文本判定给不出
  结论」的站点也放进来，用一次编译换召回——这些站点必须带低可信度标记，
  否则读者会把它当成闸门的判断；
* ``scan --probe-compile`` 只验编译、不测时间，把候选表分成「还能编」与
  「编不过」两类。测试要钉住的是**它不能冒充计时结论**。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers  # noqa: E402
from heavyheader.attrib.sites import find_header_candidates  # noqa: E402
from heavyheader.models import (  # noqa: E402
    CompilerFamily,
    IncludeEvent,
    ProjectScan,
    RemovalCandidate,
    TranslationUnit,
)
from heavyheader.report.markdown import render_markdown  # noqa: E402


def _tu(source: str, edges: list[tuple[str, str, int]]) -> TranslationUnit:
    return TranslationUnit(
        source=Path(source),
        dependencies=[Path(b) for _, b, _ in edges],
        include_events=[
            IncludeEvent(includer=Path(a), included=Path(b), depth=d) for a, b, d in edges
        ],
    )


class _AmbientTree:
    """一个「命中的全是环境噪音」的站点。

    ``site.h`` 的正文用了 ``config``，而 ``config`` 在三个头里都声明——这正是
    nlohmann/json 上把 218/222 个候选判成「无法判定」的那种情况：既不能说
    「在用」（谁都能提供它），也不能说「没用」。
    """

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="heavyheader-gate-"))
        (self.dir / "proj").mkdir(parents=True, exist_ok=True)
        self.site = self.dir / "proj" / "site.h"
        self.h1 = self.dir / "proj" / "h1.h"
        self.site.write_text(
            '#pragma once\n#include "proj/h1.h"\nint use() { return config; }\n',
            encoding="utf-8",
        )
        self.h1.write_text(
            '#pragma once\n#include "proj/a.h"\n#include "proj/b.h"\n#include "proj/c.h"\n',
            encoding="utf-8",
        )
        for name in ("a", "b", "c"):
            (self.dir / "proj" / f"{name}.h").write_text(
                "#pragma once\nint config;\n", encoding="utf-8"
            )
        self.units = []
        for index in range(3):
            source = self.dir / "proj" / f"unit_{index}.cpp"
            source.write_text(
                f'#include "proj/site.h"\nint v{index} = use();\n', encoding="utf-8"
            )
            self.units.append(source)

    def scan(self) -> ProjectScan:
        units = []
        for source in self.units:
            edges = [
                (str(source), str(self.site), 0),
                (str(self.site), str(self.h1), 1),
                (str(self.h1), str(self.dir / "proj" / "a.h"), 2),
                (str(self.h1), str(self.dir / "proj" / "b.h"), 2),
                (str(self.h1), str(self.dir / "proj" / "c.h"), 2),
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
        return scan


class UnknownSitesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = _AmbientTree()
        self.scan = self.tree.scan()
        self.sizes = HeaderSizes()
        build_header_stats(self.scan, self.sizes)
        self.kinds = classify_headers(
            self.scan.root, {h for tu in self.scan.ok_tus for h in tu.dependencies}
        )

    def test_unknown_site_is_blocked_by_default(self) -> None:
        blocked: list = []
        found = find_header_candidates(
            self.scan, self.sizes, self.kinds, blocked=blocked
        )
        # 这些候选里可能有 h1 -> a/b/c（h1 自己确实没用那三个头），所以不能
        # 断言整体为空；要断言的是**那一条无法判定的站点**没被放行。
        self.assertFalse(
            any(
                c.site_file == self.tree.site and c.header == self.tree.h1
                for c in found
            )
        )
        reasons = {item.reason for item in blocked}
        self.assertIn("owner_unknown", reasons)
        detail = next(b.detail for b in blocked if b.reason == "owner_unknown")
        self.assertIn("环境噪音", detail)
        self.assertIn("config", detail)

    def test_opt_in_makes_it_a_low_confidence_candidate(self) -> None:
        found = find_header_candidates(
            self.scan, self.sizes, self.kinds, include_unknown=True
        )
        candidate = next(
            c
            for c in found
            if c.site_file == self.tree.site and c.header == self.tree.h1
        )
        self.assertEqual(candidate.confidence, "low")
        # 必须自己说明「我是被开关放行的」，否则读者会以为闸门判过。
        joined = " ".join(candidate.notes)
        self.assertIn("allow-unknown-sites", joined)
        self.assertIn("给不出结论", joined)


class ProbeCompileModeTests(unittest.TestCase):
    def _scan_with(self, status: str) -> ProjectScan:
        tree = _AmbientTree()
        scan = tree.scan()
        candidate = RemovalCandidate(
            tu=tree.units[0],
            header=tree.h1,
            line=2,
            kind=classify_headers(scan.root, {tree.h1})[tree.h1],
        )
        from heavyheader.verify import VerificationResult

        scan.probe_mode = "probe-compile"
        scan.probe_results = [
            VerificationResult(
                candidate=candidate,
                status=status,
                message="移除后 3 个编译单元全部通过（未做耗时测量）"
                if status == "compiles"
                else "移除后编译失败",
            )
        ]
        return scan

    def test_title_says_it_only_checked_compilation(self) -> None:
        text = render_markdown(self._scan_with("compiles"))
        self.assertIn("只验编译", text)
        self.assertIn("「能编译」不等于「省时」", text)

    def test_compiles_are_listed_but_not_claimed_fast(self) -> None:
        text = render_markdown(self._scan_with("compiles"))
        self.assertIn("编译探测通过", text)
        self.assertNotIn("实测有效的改动", text)

    def test_compile_failure_is_still_reported(self) -> None:
        text = render_markdown(self._scan_with("compile-error"))
        self.assertIn("实测被拒的候选", text)


class CliFlagTests(unittest.TestCase):
    def test_new_flags_are_accepted(self) -> None:
        from heavyheader.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "scan",
                ".",
                "--header-sites",
                "--allow-unknown-sites",
                "--probe-compile",
            ]
        )
        self.assertTrue(args.allow_unknown_sites)
        self.assertTrue(args.probe_compile)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
