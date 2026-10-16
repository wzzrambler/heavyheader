"""Tests for the precompiled-header estimate.

The fixtures are deliberately small enough to check the arithmetic by hand:
if the closure or eligibility logic drifts, these numbers stop matching and
the test fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.attrib.pch import estimate_pch
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    IncludeEvent,
    ProjectScan,
    TranslationUnit,
)


def _write(root: Path, name: str, lines: int) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"int symbol_{i}();" for i in range(lines)) + "\n", encoding="utf-8"
    )
    return path


def _scan(root: Path, tus: list[TranslationUnit], headers: list[Path]) -> ProjectScan:
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=tus,
    )
    for header in headers:
        lines = len(header.read_text(encoding="utf-8").splitlines())
        reach = sum(
            1
            for tu in tus
            if any(e.included == header for e in tu.include_events)
        )
        scan.header_stats[header] = HeaderStats(
            path=header,
            kind=HeaderKind.PROJECT,
            line_count=lines,
            tu_reach=reach,
            attributed_lines=lines * reach,
        )
    return scan


def test_recommends_the_most_included_headers(tmp_path):
    """The PCH set is chosen by reach, not by size.

    A 5000-line header included by 2 TUs is a worse PCH candidate than a
    50-line header included by 8.  Ranking by size alone is the classic
    mistake this guards against.
    """
    small = _write(tmp_path, "small.h", 50)
    big = _write(tmp_path, "big.h", 5000)
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    tus = []
    for i in range(8):
        tus.append(
            TranslationUnit(
                source=tmp_path / f"t{i}.cpp",
                include_events=[
                    IncludeEvent(includer=tmp_path / f"t{i}.cpp", included=small, depth=0)
                ],
            )
        )
    # big.h only reaches two TUs.
    for i in range(2):
        tus[i].include_events.append(
            IncludeEvent(includer=tmp_path / f"t{i}.cpp", included=big, depth=0)
        )

    scan = _scan(tmp_path, tus, [small, big])
    rec = estimate_pch(scan, top_k=5)

    assert rec.headers[0] == small
    assert big not in rec.headers[:1]


def test_net_saving_arithmetic(tmp_path):
    """Hand-checkable: closure parsed once, then saved for every other TU."""
    base = _write(tmp_path, "base.h", 100)
    user = _write(tmp_path, "user.h", 10)
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    # user.h includes base.h; 4 TUs include user.h.
    tus = []
    for i in range(4):
        tu = TranslationUnit(source=tmp_path / f"t{i}.cpp")
        tu.include_events = [
            IncludeEvent(includer=tu.source, included=user, depth=0),
            IncludeEvent(includer=user, included=base, depth=1),
        ]
        tus.append(tu)

    scan = _scan(tmp_path, tus, [base, user])
    rec = estimate_pch(scan, top_k=5)

    # closure = {user, base} = 110 lines
    assert rec.closure_lines == 110
    assert rec.eligible_tus == 4
    # gross = 110 * 4 = 440; net = 440 - 110 = 330
    assert rec.net_lines_saved == 330


def test_eligibility_requires_every_chosen_header(tmp_path):
    """A TU that misses one PCH member cannot use the PCH at all."""
    a = _write(tmp_path, "a.h", 20)
    b = _write(tmp_path, "b.h", 20)
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    # 三个 TU：ok.cpp 和 also.cpp 都包含 a 和 b；partial.cpp 只包含 a。
    # 于是 a.h reach=3、b.h reach=2，两者都通过 min_reach=2。
    tu_ok = TranslationUnit(source=tmp_path / "ok.cpp")
    tu_ok.include_events = [
        IncludeEvent(includer=tu_ok.source, included=a, depth=0),
        IncludeEvent(includer=tu_ok.source, included=b, depth=0),
    ]
    tu_also = TranslationUnit(source=tmp_path / "also.cpp")
    tu_also.include_events = [
        IncludeEvent(includer=tu_also.source, included=a, depth=0),
        IncludeEvent(includer=tu_also.source, included=b, depth=0),
    ]
    tu_partial = TranslationUnit(source=tmp_path / "partial.cpp")
    tu_partial.include_events = [
        IncludeEvent(includer=tu_partial.source, included=a, depth=0),
    ]

    scan = _scan(tmp_path, [tu_ok, tu_also, tu_partial], [a, b])
    rec = estimate_pch(scan, top_k=5)
    assert set(rec.headers) == {a, b}
    # partial.cpp 缺 b.h，用不了这个 PCH。
    assert rec.eligible_tus == 2
    assert rec.total_tus == 3


def test_no_candidate_headers_yields_an_empty_recommendation(tmp_path):
    scan = ProjectScan(
        root=tmp_path,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[],
    )
    rec = estimate_pch(scan)
    assert rec.headers == []
    assert not rec.is_worthwhile
    assert rec.caveats, "空推荐也必须说明原因"


def test_min_reach_filters_single_use_headers(tmp_path):
    lonely = _write(tmp_path, "lonely.h", 100)
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    tu = TranslationUnit(source=tmp_path / "only.cpp")
    tu.include_events = [
        IncludeEvent(includer=tu.source, included=lonely, depth=0)
    ]
    scan = _scan(tmp_path, [tu], [lonely])
    rec = estimate_pch(scan, min_reach=2)
    assert lonely not in rec.headers


def test_closure_includes_transitive_headers(tmp_path):
    """PCH 的收益来自整个闭包，不只是直接纳入的头文件。"""
    deep = _write(tmp_path, "deep.h", 70)
    mid = _write(tmp_path, "mid.h", 20)
    top = _write(tmp_path, "top.h", 10)
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    # 三个 TU 都包含 top.h（它带出 mid.h 和 deep.h），所以 top.h 的 reach 最高，
    # 会成为 PCH 首选——尽管它不是行数最大的那个。
    tus = []
    for i in range(3):
        tu = TranslationUnit(source=tmp_path / f"t{i}.cpp")
        tu.include_events = [
            IncludeEvent(includer=tu.source, included=top, depth=0),
            IncludeEvent(includer=top, included=mid, depth=1),
            IncludeEvent(includer=mid, included=deep, depth=2),
        ]
        tus.append(tu)
    scan = _scan(tmp_path, tus, [deep, mid, top])
    rec = estimate_pch(scan, top_k=1)
    assert rec.headers == [top]
    # 选 top.h 会带上它的整个闭包：10 + 20 + 70。
    assert rec.closure_lines == 100


def test_worthwhile_threshold_is_deliberately_high(tmp_path):
    """A PCH saving 2% is not worth the ordering constraints it imposes."""
    tiny = _write(tmp_path, "tiny.h", 5)
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    # 三个 TU 都包含 tiny.h，另外各自包含一个不同的大头文件。
    # 大头文件 reach=1，被 min_reach=2 过滤，于是 PCH 候选只有 tiny.h。
    tus = []
    for i in range(3):
        tu = TranslationUnit(source=tmp_path / f"t{i}.cpp")
        tu.include_events = [
            IncludeEvent(includer=tu.source, included=tiny, depth=0)
        ]
        big = _write(tmp_path, f"big{i}.h", 10000)
        tu.include_events.append(
            IncludeEvent(includer=tu.source, included=big, depth=0)
        )
        tus.append(tu)

    big_paths = [tmp_path / f"big{i}.h" for i in range(3)]
    scan = _scan(tmp_path, tus, [tiny, *big_paths])
    rec = estimate_pch(scan, top_k=1)
    assert rec.headers == [tiny]
    # 净收益 10 行，而项目总量 30015 行——占比 0.03%。
    assert rec.net_lines_saved == 10
    assert rec.percent_of_project < 1.0
    assert not rec.is_worthwhile


def test_ranks_by_closure_not_own_size(tmp_path):
    """A small header with a big closure beats a big header with none.

    Ranking PCH candidates by their own line count picks deep.h (70 lines)
    over top.h (10 lines) even though top.h pulls in the whole 100-line
    closure.  With equal reach that is strictly the worse choice, so the
    ranking must use closure size.  This is a regression test for exactly
    that mistake.
    """
    deep = _write(tmp_path, "deep.h", 70)
    mid = _write(tmp_path, "mid.h", 20)
    top = _write(tmp_path, "top.h", 10)
    source = tmp_path / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    tus = []
    for i in range(3):
        tu = TranslationUnit(source=tmp_path / f"t{i}.cpp")
        tu.include_events = [
            IncludeEvent(includer=tu.source, included=top, depth=0),
            IncludeEvent(includer=top, included=mid, depth=1),
            IncludeEvent(includer=mid, included=deep, depth=2),
        ]
        tus.append(tu)

    scan = _scan(tmp_path, tus, [deep, mid, top])
    rec = estimate_pch(scan, top_k=1)
    assert rec.headers == [top]
    assert rec.closure_lines == 100
    # 100 lines saved for the other two TUs: gross 300, net 200.
    assert rec.net_lines_saved == 200
