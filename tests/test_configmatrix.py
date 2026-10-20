"""配置矩阵的纯逻辑测试：解析、派生编译库、求差、渲染。

扫描本身不打桩——那是引擎的职责，已经有别的测试覆盖；这里只锁「对齐与比较」
这一段，因为**对齐错一行，报告就会把两条不同的候选说成同一条**。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.configmatrix import (
    ConfigSpec,
    derive_database,
    render_matrix,
    table_of,
    verdict_changes,
)


def test_spec_parses_flags():
    spec = ConfigSpec.parse("mt:-DZSTD_MULTITHREAD -DNDEBUG")
    assert spec.name == "mt"
    assert spec.flags == ("-DZSTD_MULTITHREAD", "-DNDEBUG")


def test_spec_without_flags_is_allowed():
    spec = ConfigSpec.parse("plain")
    assert spec.name == "plain"
    assert spec.flags == ()


@pytest.mark.parametrize("raw", [":-DX", "  :-DX"])
def test_spec_rejects_empty_name(raw):
    with pytest.raises(ValueError):
        ConfigSpec.parse(raw)


def test_spec_rejects_space_in_name():
    with pytest.raises(ValueError):
        ConfigSpec.parse("two words:-DX")


def test_derive_appends_flags_and_keeps_originals_intact():
    entries = [{"command": "clang -c a.c", "file": "a.c"}]
    derived = derive_database(entries, ("-DFOO", "-DBAR"))
    assert derived[0]["command"] == "clang -c a.c -DFOO -DBAR"
    # 原表不能被改：调用方要拿它派生下一套配置。
    assert entries[0]["command"] == "clang -c a.c"


def test_derive_without_flags_is_a_copy():
    entries = [{"command": "clang -c a.c"}]
    derived = derive_database(entries, ())
    assert derived == entries
    assert derived is not entries and derived[0] is not entries[0]


class _Candidate:
    """扫出来的候选只需要这几个字段。"""

    def __init__(self, tu, header, line, verdict, seconds=0.0, reason=""):
        self.tu = Path(tu)
        self.header = Path(header)
        self.line = line
        self.usage_verdict = verdict
        self.estimated_seconds_saved = seconds
        self.usage_reason = reason


def test_table_keys_include_line_and_header_name():
    table = table_of(
        [
            _Candidate("/p/src/a.c", "/p/src/x.h", 3, "in_use"),
            _Candidate("/p/src/a.c", "/p/src/x.h", 9, "unknown"),
        ],
        tu_name=True,
    )
    assert set(table) == {("a.c", "x.h", 3), ("a.c", "x.h", 9)}


def test_changes_report_verdict_flip_and_disappearance():
    before = table_of(
        [
            _Candidate("/p/a.c", "/p/alloc.h", 13, "unknown"),
            _Candidate("/p/a.c", "/p/debug.h", 15, "unknown"),
            _Candidate("/p/b.c", "/p/keep.h", 7, "in_use"),
        ],
        tu_name=True,
    )
    after = table_of(
        [
            _Candidate("/p/a.c", "/p/alloc.h", 13, "in_use"),
            _Candidate("/p/b.c", "/p/keep.h", 7, "in_use"),
        ],
        tu_name=True,
    )
    changes = verdict_changes(before, after)
    described = [change.describe() for change in changes]
    assert described == [
        "a.c → alloc.h：unknown → in_use",
        "a.c → debug.h：unknown → （不再是候选）",
    ]


def test_identical_tables_have_no_changes():
    table = table_of([_Candidate("/p/a.c", "/p/x.h", 3, "in_use")], tu_name=True)
    assert verdict_changes(table, table) == []


def test_render_lists_counts_and_each_change():
    specs = [ConfigSpec.parse("single:"), ConfigSpec.parse("mt:-DZSTD_MULTITHREAD")]
    summaries = {
        "single": {
            "tu": 39,
            "candidates": 90,
            "verdicts": {"plausibly_unused": 7, "unknown": 8, "in_use": 75},
        },
        "mt": {
            "tu": 39,
            "candidates": 91,
            "verdicts": {"plausibly_unused": 8, "unknown": 5, "in_use": 78},
        },
    }
    before = table_of([_Candidate("/p/pool.c", "/p/allocations.h", 13, "unknown")], tu_name=True)
    after = table_of([_Candidate("/p/pool.c", "/p/allocations.h", 13, "in_use")], tu_name=True)
    text = render_matrix(specs, summaries, {"mt": verdict_changes(before, after)})
    assert "| `single` |" in text and "| `mt` |" in text
    assert "相对 `single` 的判定变化" in text
    assert "`single` → `mt`：1 条不同" in text
    assert "unknown | in_use |" in text


def test_render_says_identical_when_nothing_changes():
    specs = [ConfigSpec.parse("a:"), ConfigSpec.parse("b:-DX")]
    empty = {"tu": 1, "candidates": 1, "verdicts": {}}
    summaries = {"a": empty, "b": empty}
    text = render_matrix(specs, summaries, {"b": []})
    assert "判定完全一致。" in text
