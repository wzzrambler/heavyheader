"""Tests for the precompiled-header estimate.

The fixtures are deliberately small enough to check the arithmetic by hand:
if the closure or eligibility logic drifts, these numbers stop matching and
the test fails.
"""

from __future__ import annotations

from pathlib import Path

from heavyheader.attrib.pch import (
    PCHRecommendation,
    estimate_pch,
    macro_prefixed_units,
)
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    HeaderStats,
    IncludeEvent,
    PhaseTiming,
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


def test_recommends_by_net_saving_not_by_header_size(tmp_path):
    """排序目标是被测出来的净节省行数，不是头文件自身的行数。

    防护的是一个经典错误：按 ``line_count`` 排。这里 big.h 有 5000 行但只被
    2 个单元包含（净节省 5000 行），small.h 只有 100 行却被 100 个单元包含
    （净节省 9900 行）——按自身大小会选中大的那个，按净节省选小的。
    """
    small = _write(tmp_path, "small.h", 100)
    big = _write(tmp_path, "big.h", 5000)

    tus = []
    for i in range(100):
        tus.append(
            TranslationUnit(
                source=tmp_path / f"t{i:03d}.cpp",
                include_events=[
                    IncludeEvent(
                        includer=tmp_path / f"t{i:03d}.cpp", included=small, depth=0
                    )
                ],
            )
        )
    for i in range(2):
        tus[i].include_events.append(
            IncludeEvent(includer=tus[i].source, included=big, depth=0)
        )

    rec = estimate_pch(_scan(tmp_path, tus, [small, big]), top_k=5)

    assert rec.headers == [small]
    assert rec.eligible_tus == 100
    assert rec.net_lines_saved == 100 * 99  # 100 行 × 99 个额外使用者


def test_greedy_refuses_a_header_that_lowers_the_net_saving(tmp_path):
    """贪心必须拒绝让净节省下降的头——这是 top-k 切片的真实缺陷。

    10 个单元：5 个含 common.h，另 5 个含 special.h，两个头等大。
    旧实现按 (reach, 闭包行数) 取前 2 个，把 special.h 也放进去，结果
    eligible 从 10 掉到 5、闭包翻倍，净节省从 13,527 行降到 12,024 行。
    新实现按净节省增量贪心，加 special.h 是净损失，于是只选 common.h。
    """
    common = _write(tmp_path, "common.h", 1503)
    special = _write(tmp_path, "special.h", 1503)

    # 10 个单元全都包含 common.h，其中 5 个额外包含 special.h——这正是
    # ``experiments/_work/fragile-demo`` 的真实形状（那里实测 13,527 vs 12,024）。
    tus = []
    for i in range(10):
        events = [
            IncludeEvent(
                includer=tmp_path / f"t{i:02d}.cpp", included=common, depth=0
            )
        ]
        if i < 5:
            events.append(
                IncludeEvent(
                    includer=tmp_path / f"t{i:02d}.cpp", included=special, depth=0
                )
            )
        tus.append(
            TranslationUnit(source=tmp_path / f"t{i:02d}.cpp", include_events=events)
        )

    rec = estimate_pch(_scan(tmp_path, tus, [common, special]), top_k=5)

    assert rec.headers == [common]
    assert rec.eligible_tus == 10
    assert rec.net_lines_saved == 1503 * 9  # 13,527
    # special.h 既没进闭包，也就不会成为脆弱头。
    assert special not in rec.closure_headers
    assert rec.fragile_headers == []


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
    b = _write(tmp_path, "b.h", 2000)  # 大得足以让「加它」成为净增益
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


def test_maintenance_tax_counts_every_unit_that_uses_the_pch(tmp_path):
    """税的影响面是「使用 PCH 的单元」，不是被改头自己触达的单元。

    .gch 作废后，每个用 ``-include`` 的单元都要重新生成 .gch 再编译自己，
    与是哪个头触发失效无关。这里 5 个单元：u0-u3 含 A.h（它拉进 C.h），
    u2-u4 含 B.h。贪心只选 A.h（加 B.h 会降低净节省），于是使用 PCH 的
    就是含 A.h 的 4 个单元；A.h 自己触达的也是这 4 个，放大 1.0 倍。
    """
    a = _write(tmp_path, "A.h", 100)
    b = _write(tmp_path, "B.h", 10)
    c = _write(tmp_path, "C.h", 5)
    includes = [
        [a, c],   # u0
        [a, c],   # u1
        [a, c, b],  # u2
        [a, c, b],  # u3
        [b],      # u4
    ]
    tus = []
    for i, hs in enumerate(includes):
        events = []
        for h in hs:
            # C.h 是被 A.h 包含的，不是被单元直接包含的——includer 必须是 A.h，
            # 否则闭包算不出来，C.h 会变成一个独立的候选头。
            includer = a if h is c else tmp_path / f"t{i}.cpp"
            events.append(
                IncludeEvent(includer=includer, included=h, depth=1 if h is c else 0)
            )
        tus.append(
            TranslationUnit(
                source=tmp_path / f"t{i}.cpp",
                include_events=events,
                wall_seconds=1.0 * (i + 1),
            )
        )

    rec = estimate_pch(_scan(tmp_path, tus, [a, b, c]), top_k=5)

    # 贪心只选 A.h：加入 B.h 会让 eligible 从 4 掉到 2、闭包变大，净节省下降。
    assert rec.headers == [a]
    assert rec.rebuild_header == a
    assert rec.rebuild_units == 4            # 使用 PCH 的单元（含 A.h 的 4 个）
    assert rec.baseline_rebuild_units == 4   # 无 PCH 时改 A.h 也只重编这 4 个
    assert rec.rebuild_amplification == 1.0
    assert rec.rebuild_seconds == 10.0       # 1+2+3+4
    assert rec.fragile_headers == []


def test_maintenance_tax_falls_back_to_time_report_total(tmp_path):
    """没有驱动墙钟时退化到 ``-ftime-report`` 的 TOTAL，而不是记成 0。

    记成 0 会让维护税看起来是免费的——那正是这条建议最容易误导人的方向。
    """
    common = _write(tmp_path, "common.h", 100)
    tus = [
        TranslationUnit(
            source=tmp_path / f"t{i}.cpp",
            include_events=[
                IncludeEvent(includer=tmp_path / f"t{i}.cpp", included=common, depth=0)
            ],
            timings=[PhaseTiming(name="TOTAL", wall_seconds=2.5)],
        )
        for i in range(2)
    ]

    rec = estimate_pch(_scan(tmp_path, tus, [common]), top_k=5)

    assert rec.rebuild_units == 2
    assert rec.baseline_rebuild_units == 2
    assert rec.rebuild_amplification == 1.0
    assert rec.rebuild_seconds == 5.0
    # 两个单元都包含 common.h：没有「只服务部分单元」的头，清单为空。
    assert rec.fragile_headers == []


def test_fragile_headers_lists_headers_below_the_pch_user_count(tmp_path):
    """脆弱头清单按放大倍数降序，且只收触达小于 PCH 使用者的项目头。

    构造故意让贪心**仍然选中**那个会制造脆弱头的组合：base.h 的闭包
    （base + common = 5,000 行）远大于 mid.h（100 行），于是「加 base.h」
    尽管把 eligible 从 6 压到 2，净节省仍从 500 行涨到 5,100 行——贪心选它。
    此时使用 PCH 的是 6 个单元，而 base.h 与 common.h 只服务 2 个，
    放大 3.0 倍。这正是「净收益口径下仍会出现的税」，也是清单存在的理由。
    """
    mid = _write(tmp_path, "mid.h", 100)
    base = _write(tmp_path, "base.h", 1000)
    common = _write(tmp_path, "common.h", 4000)

    def unit(name, direct, via=None):
        events = [IncludeEvent(includer=tmp_path / name, included=direct, depth=0)]
        if via is not None:
            events.append(IncludeEvent(includer=direct, included=via, depth=1))
        return TranslationUnit(source=tmp_path / name, include_events=events)

    # u0-u3 只用 mid.h；u4/u5 用 mid.h + base.h（它拉进 common.h）。
    tus = [unit(f"t{i}.cpp", mid) for i in range(4)]
    tus += [unit("t4.cpp", base, common), unit("t5.cpp", base, common)]
    for tu in tus[4:]:
        tu.include_events.insert(
            0, IncludeEvent(includer=tu.source, included=mid, depth=0)
        )
    for tu in tus:
        tu.wall_seconds = 1.0
    scan = _scan(tmp_path, tus, [mid, base, common])

    rec = estimate_pch(scan, top_k=5)

    # 贪心按净增益的顺序选：base.h 先（5,000 行 > mid.h 的 500 行），
    # 然后 mid.h 仍能再加 100 行净增益，于是两个都进。
    assert rec.headers == [base, mid]
    assert rec.eligible_tus == 2
    assert rec.rebuild_units == 6              # 全体单元都使用 PCH
    got = [(p.name, r, round(a, 2)) for p, r, a in rec.fragile_headers]
    assert got == [("base.h", 2, 3.0), ("common.h", 2, 3.0)]


def test_fragile_headers_is_empty_when_every_header_is_universal(tmp_path):
    """所有项目头都被全体单元包含时，清单必须为空——不能为了有内容而编内容。

    这正是 nlohmann/json 的真实形状（63/63 全触达），所以这条同时是
    「json 语料上不报脆弱头」的回归保护。
    """
    common = _write(tmp_path, "common.h", 100)
    tus = [
        TranslationUnit(
            source=tmp_path / f"t{i}.cpp",
            include_events=[
                IncludeEvent(includer=tmp_path / f"t{i}.cpp", included=common, depth=0)
            ],
            wall_seconds=1.0,
        )
        for i in range(4)
    ]

    rec = estimate_pch(_scan(tmp_path, tus, [common]), top_k=5)

    assert rec.eligible_tus == 4
    assert rec.rebuild_units == 4
    assert rec.rebuild_amplification == 1.0
    assert rec.fragile_headers == []


# -- 预置宏预警 -----------------------------------------------------------


def test_macro_prefixed_units_flags_macro_before_the_header(tmp_path):
    """在包含候选头**之前**改过宏的单元要被点出来。

    这不是风格问题：``-include`` 把 PCH 插到每个单元最前面，头文件看到的是
    「include 位置之前」那套宏状态。先 #define 再 include 的单元，看到的宏就
    跟它本该看到的不一样了。
    """
    common = _write(tmp_path, "common.h", 50)
    # 对照组：宏在 include **之后**。PCH 插到最前面时，头看到的宏状态与原本一致。
    (tmp_path / "late.cpp").write_text(
        '#include "common.h"\n#define LATE 1\nint a();\n', encoding="utf-8"
    )
    # 对照组：彻底没有宏。
    (tmp_path / "clean.cpp").write_text(
        '#include "common.h"\nint b();\n', encoding="utf-8"
    )
    # 被测：宏在 include **之前**——这正是 json 里 unit-class_lexer.cpp 的形状。
    (tmp_path / "prefixed.cpp").write_text(
        '#define JSON_TESTS_PRIVATE\n#include "common.h"\nint c();\n', encoding="utf-8"
    )

    def tu(name: str) -> TranslationUnit:
        return TranslationUnit(
            source=tmp_path / name,
            include_events=[
                IncludeEvent(includer=tmp_path / name, included=common, depth=0)
            ],
            wall_seconds=1.0,
        )

    tus = [tu("late.cpp"), tu("clean.cpp"), tu("prefixed.cpp")]
    scan = _scan(tmp_path, tus, [common])

    found = {
        path.name: (lineno, text)
        for path, lineno, text in macro_prefixed_units(scan, [common])
    }

    assert set(found) == {"prefixed.cpp"}
    assert found["prefixed.cpp"] == (1, "#define JSON_TESTS_PRIVATE")


def test_macro_prefixed_units_only_looks_before_the_candidate_include(tmp_path):
    """每个候选头在自己的 include 位置之前各算一次。"""
    common = _write(tmp_path, "common.h", 50)
    other = _write(tmp_path, "other.h", 20)
    src = tmp_path / "x.cpp"
    src.write_text(
        '#include "other.h"\n'
        "#define AFTER_OTHER 1\n"
        '#include "common.h"\n',
        encoding="utf-8",
    )
    tu = TranslationUnit(
        source=src,
        include_events=[
            IncludeEvent(includer=src, included=other, depth=0),
            IncludeEvent(includer=src, included=common, depth=0),
        ],
        wall_seconds=1.0,
    )
    scan = _scan(tmp_path, [tu], [common, other])

    # common.h 在第 3 行，它之前有宏 → 标记（行号是宏所在行 2）。
    found = macro_prefixed_units(scan, [common])
    assert len(found) == 1 and found[0][1] == 2

    # 只把 other.h 当候选时，它在第 1 行、前面什么都没有 → 不标记。
    assert macro_prefixed_units(scan, [other]) == []


def test_macro_prefixed_units_is_empty_without_headers(tmp_path):
    assert macro_prefixed_units(_scan(tmp_path, [], []), []) == []


def test_recommendation_reports_prefixed_units_and_keeps_caveats(tmp_path):
    """预警进推荐对象，且不改动既有的收益口径。"""
    common = _write(tmp_path, "common.h", 100)
    bad = tmp_path / "bad.cpp"
    bad.write_text('#define CFG 1\n#include "common.h"\n', encoding="utf-8")
    good = tmp_path / "good.cpp"
    good.write_text('#include "common.h"\n', encoding="utf-8")
    tus = [
        TranslationUnit(
            source=path,
            include_events=[IncludeEvent(includer=path, included=common, depth=0)],
            wall_seconds=1.0,
        )
        for path in (bad, good)
    ]
    rec = estimate_pch(_scan(tmp_path, tus, [common]))

    assert rec.macro_prefixed_count == 1
    assert rec.macro_prefixed_units[0][0] == bad
    # 预警不改变收益：两个单元都仍然算 eligible。
    assert rec.eligible_tus == 2


def test_macro_prefixed_units_checks_every_candidate_header(tmp_path):
    """候选头不止一个时，不能停在第一个。

    真实形状（json 的每一个单元）：

        #include "doctest_compatibility.h"   ← 第一个候选头，它前面没有宏
        #define JSON_TESTS_PRIVATE          ← 真正的风险在这里
        #include <nlohmann/json.hpp>        ← 第二个候选头

    第一版在第一个候选头处就返回了（前缀为空 → 不标记），于是 json 上一条预警
    都报不出来。这个函数存在的唯一理由就是抓这类单元。
    """
    compat = _write(tmp_path, "doctest_compatibility.h", 10)
    main_h = _write(tmp_path, "common.h", 50)
    src = tmp_path / "unit-like.cpp"
    src.write_text(
        '#include "doctest_compatibility.h"\n'
        "#define JSON_TESTS_PRIVATE\n"
        '#include "common.h"\n',
        encoding="utf-8",
    )
    tu = TranslationUnit(
        source=src,
        include_events=[
            IncludeEvent(includer=src, included=compat, depth=0),
            IncludeEvent(includer=src, included=main_h, depth=0),
        ],
        wall_seconds=1.0,
    )
    scan = _scan(tmp_path, [tu], [compat, main_h])

    found = macro_prefixed_units(scan, [compat, main_h])
    assert len(found) == 1
    assert found[0][1] == 2  # 宏在第 2 行
    assert found[0][2] == "#define JSON_TESTS_PRIVATE"


def test_empty_header_set_has_zero_coverage():
    """没有候选头时覆盖率是 0，不是 100%。

    空集合上的「所有 TU 都包含它」在集合论上成立，写进报告却会被读成
    「全项目都能用这个 PCH」。真实事故：相对 ``-I`` 让路径解析失败，估算
    选不出任何头，报告却显示「覆盖率 100%、净收益 0」——一个看起来完全
    正常的空结论。这一条守住 ``coverage`` 这个属性本身。
    """
    empty = PCHRecommendation(
        headers=[],
        closure_headers=[],
        closure_lines=0,
        eligible_tus=5,
        total_tus=5,
        net_lines_saved=0,
        percent_of_project=0.0,
    )
    assert empty.coverage == 0.0


def test_unreadable_header_lines_are_reported_not_silently_empty(tmp_path):
    """行数全读成 0 时要说「读不到」，不能让读者以为是「没有收益」。"""
    header = tmp_path / "ghost.h"
    header.write_text("int x;\n", encoding="utf-8")
    tus = [
        TranslationUnit(
            source=tmp_path / f"t{i}.cpp",
            include_events=[
                IncludeEvent(includer=tmp_path / f"t{i}.cpp", included=header, depth=0)
            ],
            wall_seconds=1.0,
        )
        for i in range(3)
    ]
    scan = _scan(tmp_path, tus, [header])
    rec = estimate_pch(scan, line_counter=lambda _p: 0)

    assert rec.headers == []
    assert rec.coverage == 0.0
    assert any("读成 0" in c for c in rec.caveats)


def test_gross_saving_counts_each_unit_s_own_closure(tmp_path):
    """条件编译下单靠乘法会高估：每个单元只解析闭包的一部分。

    真实形状（nlohmann/json）：``json.hpp`` 里几十个 ``#if`` 分支，没有任何
    单元解析了全部闭包。用 ``closure_lines × eligible`` 等于假设每个单元都
    解析整个闭包，实测得到「净收益 = 全项目解析量的 192%」——省下的行数
    不可能超过总解析量。
    """
    top = _write(tmp_path, "top.h", 100)
    side = _write(tmp_path, "side.h", 100)
    # TU1 解析 top + side；TU2 只因条件编译解析 top（side 没被展开）。
    tu1 = TranslationUnit(
        source=tmp_path / "a.cpp",
        include_events=[
            IncludeEvent(includer=tmp_path / "a.cpp", included=top, depth=0),
            IncludeEvent(includer=top, included=side, depth=1),
        ],
        wall_seconds=1.0,
    )
    tu2 = TranslationUnit(
        source=tmp_path / "b.cpp",
        include_events=[
            IncludeEvent(includer=tmp_path / "b.cpp", included=top, depth=0),
        ],
        wall_seconds=1.0,
    )
    scan = _scan(tmp_path, [tu1, tu2], [top, side])

    rec = estimate_pch(scan)

    assert rec.headers == [top]
    assert rec.closure_lines == 200
    # 精确：TU1 解析 200 行，TU2 解析 100 行 → 毛收益 300，减一次性 200 = 100。
    # 乘法会给出 200×2 − 200 = 200，高估一倍。
    assert rec.net_lines_saved == 100


def test_net_saving_never_exceeds_total_parsed_lines(tmp_path):
    """不变量：省下的行数不可能超过全项目总解析量。"""
    top = _write(tmp_path, "top.h", 100)
    side = _write(tmp_path, "side.h", 100)
    tus = []
    for index in range(4):
        events = [
            IncludeEvent(includer=tmp_path / f"t{index}.cpp", included=top, depth=0)
        ]
        if index % 2 == 0:  # 一半单元因条件编译展开 side
            events.append(IncludeEvent(includer=top, included=side, depth=1))
        tus.append(
            TranslationUnit(
                source=tmp_path / f"t{index}.cpp",
                include_events=events,
                wall_seconds=1.0,
            )
        )
    scan = _scan(tmp_path, tus, [top, side])
    rec = estimate_pch(scan)

    assert rec.net_lines_saved <= scan.total_included_lines()
    assert 0.0 <= rec.percent_of_project <= 100.0
