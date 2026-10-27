"""IWYU 输出解析与合并。

**这些测试验证的是「按官方格式构造的样本能被正确解析」，不是端到端实测。**
本项目构建不出 IWYU 二进制（需按 LLVM 版本源码编译），所以格式口径取自
IWYU 自己的 ``fix_includes.py``——即官方解析器。与它对齐意味着：IWYU 改格式时
我们至少和官方脚本同步失效，而不是各自漂移。
"""

from pathlib import Path

from heavyheader.attrib.iwyu import (
    compare_with_scan,
    parse_iwyu_log,
)
from heavyheader.models import (
    AdviceVerdict,
    CompilerFamily,
    HeaderKind,
    ProjectScan,
    RemovalAdvice,
    RemovalCandidate,
)
from heavyheader.report.markdown import render_iwyu_comparison


def _log(*blocks: str) -> str:
    return "\n".join(blocks) + "\n"


#: 只有一条删除建议的记录——单条时断言报告里出现/不出现某个词才有意义。
ONLY_MID = _log(
    "app/main.cpp should remove these lines:",
    '- #include "mid.h"  // lines 3-3',
    "",
    "The full include-list for app/main.cpp:",
    '#include "core.h"',
    "---",
)

#: 只有一条删除建议的记录——单条时断言报告里出现/不出现某个词才有意义。
ONLY_MID = _log(
    "app/main.cpp should remove these lines:",
    '- #include "mid.h"  // lines 3-3',
    "",
    "The full include-list for app/main.cpp:",
    '#include "core.h"',
    "---",
)

#: 完整的一条记录：add → remove → total → ---，外加一行编译器噪音。
FULL_RECORD = _log(
    "app/main.cpp:6:1: warning: something unrelated",
    "app/main.cpp should add these lines:",
    '#include "needed.h"',
    "",
    "app/main.cpp should remove these lines:",
    '- #include "mid.h"  // lines 3-3',
    '- #include "lonely.h"',
    "",
    "The full include-list for app/main.cpp:",
    '#include "core.h"',
    "---",
)


def test_parses_all_three_sections():
    records, ignored = parse_iwyu_log(FULL_RECORD)
    assert len(records) == 1
    # 编译器告警那一行不属于任何段，必须被忽略而不是当成建议。
    assert ignored == 1

    (record,) = records
    assert record.source == "app/main.cpp"
    assert record.sections == {"add", "remove", "total"}
    assert record.is_complete is True
    assert [s.include for s in record.add] == ['"needed.h"']
    assert [s.include for s in record.remove] == ['"mid.h"', '"lonely.h"']
    assert record.full_include_list == ["core.h"]


def test_line_numbers_are_an_inclusive_range():
    """``// lines 3-3`` 是闭区间——fix_includes.py 用 ``range(start, end+1)``。"""
    records, _ = parse_iwyu_log(FULL_RECORD)
    (mid,) = [s for s in records[0].remove if s.include == '"mid.h"']
    assert mid.lines == (3, 3)


def test_missing_line_numbers_are_none_not_guessed():
    records, _ = parse_iwyu_log(FULL_RECORD)
    (lonely,) = [s for s in records[0].remove if s.include == '"lonely.h"']
    assert lonely.lines is None


def test_no_edits_record():
    records, _ = parse_iwyu_log("(app/clean.cpp has correct #includes/fwd-decls)\n")
    (record,) = records
    assert record.no_edits is True
    assert record.is_complete is True
    assert record.remove == []


def test_truncated_log_is_marked_incomplete():
    """日志被截断时「IWYU 没建议删」是假的——它只是没说到那里。

    这是与官方 ``fix_includes.py`` 的有意分歧：它遇缺段即报错，我们为了容忍
    交错输出而允许缺段，所以必须把「不完整」显式记下来。
    """
    records, _ = parse_iwyu_log(
        _log(
            "app/main.cpp should add these lines:",
            '#include "needed.h"',
        )
    )
    (record,) = records
    assert record.is_complete is False
    assert record.sections == {"add"}


def test_not_an_iwyu_log_produces_nothing():
    """拿错文件不能悄悄给出一张空表。"""
    records, ignored = parse_iwyu_log("hello\nworld\n")
    assert records == []
    assert ignored == 2


# --- 合并 -------------------------------------------------------------------


def _scan(*candidates: RemovalCandidate) -> ProjectScan:
    return ProjectScan(
        root=Path("."),
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        candidates=list(candidates),
    )


def _candidate(source: str, header: str, line: int | None, **kw) -> RemovalCandidate:
    return RemovalCandidate(
        tu=Path(source),
        header=Path(header),
        line=line,
        kind=HeaderKind.PROJECT,
        **kw,
    )


def test_matches_by_line_number_in_preference_to_name():
    """行号是强证据；同名不同目录靠文件名会串，所以优先级必须明确。"""
    scan = _scan(
        _candidate("app/main.cpp", "other/mid.h", 7, estimated_lines_saved=1),
        _candidate("app/main.cpp", "app/mid.h", 3, estimated_lines_saved=99),
    )
    records, _ = parse_iwyu_log(FULL_RECORD)
    comparison = compare_with_scan(records, scan)

    (mid,) = [p for p in comparison.pairs if "mid" in p.include]
    assert mid.matched_by == "行号"
    assert mid.estimated_lines_saved == 99


def test_falls_back_to_filename_only_when_no_line_number():
    scan = _scan(_candidate("app/main.cpp", "app/lonely.h", 5))
    records, _ = parse_iwyu_log(FULL_RECORD)
    comparison = compare_with_scan(records, scan)

    (lonely,) = [p for p in comparison.pairs if "lonely" in p.include]
    assert lonely.matched_by == "文件名"


def test_unmeasured_candidate_is_not_reported_as_missing():
    """有候选但这一轮没跑 ``--probe``：是「未实测」，不是「我们无此候选」。

    两者混在一起等于把「没测过」说成「没有」——正是这份报告最不能犯的错。
    """
    scan = _scan(_candidate("app/main.cpp", "app/mid.h", 3, usage_label="正在使用"))
    records, _ = parse_iwyu_log(ONLY_MID)
    comparison = compare_with_scan(records, scan)

    (mid,) = comparison.pairs
    assert mid.has_candidate is True
    assert mid.our_verdict is None

    body = render_iwyu_comparison(comparison, scan)
    assert "未实测" in body
    assert "我们无此候选" not in body


def test_missing_candidate_is_said_so():
    scan = _scan(_candidate("app/other.cpp", "app/x.h", 1))
    records, _ = parse_iwyu_log(ONLY_MID)
    comparison = compare_with_scan(records, scan)

    assert all(p.has_candidate is False for p in comparison.pairs)
    assert "我们无此候选" in render_iwyu_comparison(comparison, scan)


def test_agreement_counts_only_when_we_also_say_remove():
    advice = RemovalAdvice(verdict=AdviceVerdict.REMOVE, reasons=[])
    scan = _scan(
        _candidate("app/main.cpp", "app/mid.h", 3, advice=advice),
        # 这一条 IWYU 没提过，所以应当计入「我们独有」。
        _candidate("app/main.cpp", "app/extra.h", 9, usage_label="正在使用"),
    )
    records, _ = parse_iwyu_log(FULL_RECORD)
    comparison = compare_with_scan(records, scan)

    assert comparison.agreement == 1
    assert comparison.our_unmatched == 1


def test_incomplete_records_are_counted():
    scan = _scan()
    records, _ = parse_iwyu_log(
        _log("app/main.cpp should add these lines:", '#include "a.h"')
    )
    comparison = compare_with_scan(records, scan)
    assert comparison.incomplete_records == 1
    assert "不完整" in render_iwyu_comparison(comparison, scan)


def test_report_states_the_end_to_end_boundary():
    """没有端到端实测过，报告必须自己说出来——不能让读者以为是实测结论。"""
    scan = _scan(_candidate("app/main.cpp", "app/mid.h", 3))
    records, _ = parse_iwyu_log(FULL_RECORD)
    body = render_iwyu_comparison(compare_with_scan(records, scan), scan)
    assert "未经端到端实测" in body


def test_report_does_not_rank_the_two_tools():
    """IWYU 判正确性、我们判成本，答的不是同一个问题。

    把它们排成一场比赛（准确率、谁对谁错）是误导，报告里不该出现这类词。
    """
    scan = _scan(_candidate("app/main.cpp", "app/mid.h", 3))
    records, _ = parse_iwyu_log(FULL_RECORD)
    body = render_iwyu_comparison(compare_with_scan(records, scan), scan)
    for word in ("准确率", "谁更准", "误报率"):
        assert word not in body


def test_cli_refuses_a_file_that_is_not_an_iwyu_log(tmp_path: Path, capsys):
    """拿错文件时最危险的形态是不报错、只给空表。"""
    from heavyheader.cli import main

    scan_json = tmp_path / "scan.json"
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("total garbage\n", encoding="utf-8")

    assert main(["iwyu", str(wrong), "--scan", str(scan_json)]) != 0
