"""clang-include-cleaner 输出解析与合并。

**与 ``test_iwyu.py`` 的关键差别：这里有一部分是真跑出来的。** 本项目在本机
能拿到 clang-include-cleaner 二进制（LLVM 23.1.3），所以解析格式是从真实
进程输出校准的；找不到二进制时端到端那几条才 skip，纯解析的部分照跑。

端到端分组有意跑一个**编不过**的源文件：clang-include-cleaner 遇到编译错误
会跳过该文件而**退出码仍是 0**，这条不变量一旦被破坏，「跳过」就会被读成
「通过」，整份对位报告的意义就没了。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heavyheader.attrib.incclean import (
    analyse_database,
    compare_with_scan,
    find_binary,
    parse_include_cleaner_output,
    run_on_unit,
)
from heavyheader.models import (
    CompilerFamily,
    HeaderKind,
    ProjectScan,
    RemovalCandidate,
)

# demo 语料里唯一一条真实建议：main.cpp 第 5 行的 lonely.h 没人用。
DEMO_ROOT = Path(__file__).resolve().parent.parent / "benchmarks" / "cases" / "demo"
DEMO_MAIN = DEMO_ROOT / "app" / "main.cpp"

_BINARY = find_binary()
needs_binary = pytest.mark.skipif(
    _BINARY is None, reason="本机没有 clang-include-cleaner 二进制"
)


# --- 纯解析（不需要二进制） -------------------------------------------------


def test_parses_real_output_format():
    """格式取自真实进程的 ``--print=changes`` 输出。"""
    record = parse_include_cleaner_output(
        '- "lonely.h" @Line:5\n+ <utility>\n', "app/main.cpp"
    )
    assert [(e.spelling, e.name, e.line) for e in record.removes] == [
        ('"lonely.h"', "lonely.h", 5)
    ]
    # 插入不给行号——插哪儿由工具自己定，我们不该编一个出来。
    assert [(e.spelling, e.line) for e in record.inserts] == [("<utility>", None)]


def test_system_and_project_headers_keep_their_spelling():
    record = parse_include_cleaner_output("- <map> @Line:1\n", "a.cpp")
    (edit,) = record.removes
    assert edit.spelling == "<map>"
    assert edit.name == "map"


def test_removal_without_line_number_still_parses():
    """``@Line:`` 缺失不该让整条建议消失——只是对位退回文件名。"""
    record = parse_include_cleaner_output('- "foo.h"\n', "a.cpp")
    assert len(record.removes) == 1
    assert record.removes[0].lines is None


@pytest.mark.parametrize(
    "text",
    [
        "Skipping file a.cpp due to compiler errors.\n",
        "Error while processing a.cpp.\n",
        "-print requires a single input file\n",
    ],
)
def test_failure_modes_mark_the_file_as_skipped(text):
    """三种失败形态都必须被认出来，而不是产出一张空建议表。"""
    record = parse_include_cleaner_output(text, "a.cpp")
    assert record.skipped is True
    assert record.analysed is False


def test_skipped_file_is_not_treated_as_agreement():
    """「没分析」被当成「没意见」是这份报告最危险的读法。

    跳过的原因是编译失败，此时工具对这条 include **什么都没说**；把它算进
    「两边一致」的对位里，等于用一次失败给「删它是安全的」背书。
    """
    scan = _scan(_candidate("a.cpp", "a/foo.h", 3))
    # 同一份输出里既有「跳过」又有建议：以 skipped 为准，建议一律不算数。
    text = 'Skipping file a.cpp due to compiler errors.\n- "foo.h" @Line:3\n'
    records = [parse_include_cleaner_output(text, "a.cpp")]
    comparison = compare_with_scan(records, scan)

    assert comparison.skipped_files == 1
    assert comparison.pairs == []
    assert comparison.agreement == 0


def test_diagnostic_lines_are_kept_for_troubleshooting():
    record = parse_include_cleaner_output(
        "a.cpp:3:14: error: unknown type name 'B'\n"
        "Skipping file a.cpp due to compiler errors.\n",
        "a.cpp",
    )
    assert record.skipped is True
    assert any("unknown type name" in line for line in record.diagnostics)


def test_unrecognised_output_is_counted_not_swallowed():
    """拿错文件时不能给出一张看起来正常的空表。"""
    record = parse_include_cleaner_output("hello\nworld\n", "a.cpp")
    assert record.removes == []
    assert len(record.unrecognised) == 2


# --- 合并 -------------------------------------------------------------------


def _scan(*candidates: RemovalCandidate) -> ProjectScan:
    return ProjectScan(
        root=Path("."),
        compiler="clang++",
        compiler_family=CompilerFamily.CLANG,
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
    scan = _scan(
        _candidate("main.cpp", "other/lonely.h", 9, estimated_lines_saved=1),
        _candidate("main.cpp", "app/lonely.h", 5, estimated_lines_saved=99),
    )
    record = parse_include_cleaner_output('- "lonely.h" @Line:5\n', "main.cpp")
    comparison = compare_with_scan([record], scan)

    (pair,) = comparison.pairs
    assert pair.matched_by == "行号"
    assert pair.estimated_lines_saved == 99
    assert pair.has_candidate is True


def test_falls_back_to_filename_when_line_missing():
    scan = _scan(_candidate("main.cpp", "app/lonely.h", 5))
    record = parse_include_cleaner_output('- "lonely.h"\n', "main.cpp")
    comparison = compare_with_scan([record], scan)

    (pair,) = comparison.pairs
    assert pair.matched_by == "文件名"


def test_unmeasured_candidate_is_not_reported_as_missing():
    scan = _scan(_candidate("main.cpp", "app/lonely.h", 5))
    record = parse_include_cleaner_output('- "lonely.h" @Line:5\n', "main.cpp")
    comparison = compare_with_scan([record], scan)

    (pair,) = comparison.pairs
    assert pair.has_candidate is True
    assert pair.our_verdict is None


def test_header_site_candidates_are_not_matched():
    """外部工具只分析源文件，头里的 include 点不该被对上。"""
    scan = _scan(
        _candidate("main.cpp", "app/lonely.h", 5, site_file=Path("app/wrapper.h")),
    )
    record = parse_include_cleaner_output('- "lonely.h" @Line:5\n', "main.cpp")
    comparison = compare_with_scan([record], scan)

    assert comparison.pairs and comparison.pairs[0].has_candidate is False


def test_to_dict_reports_skipped_separately():
    scan = _scan(_candidate("a.cpp", "a/foo.h", 3))
    records = [
        parse_include_cleaner_output("Skipping file a.cpp due to compiler errors.\n", "a.cpp")
    ]
    payload = compare_with_scan(records, scan).to_dict()
    assert payload["skipped_files"] == 1
    assert payload["analysed_files"] == 0
    assert payload["skipped_detail"][0]["source"] == "a.cpp"


# --- 端到端（需要真实二进制） -----------------------------------------------


@needs_binary
def test_live_run_on_demo_corpus():
    """真跑一次：demo 的 main.cpp 上工具确实只指出 lonely.h。"""
    record = run_on_unit(
        _BINARY,
        DEMO_MAIN,
        extra_args=("-std=c++17", f"-I{DEMO_ROOT / 'include'}"),
    )
    assert record.analysed is True
    assert [e.name for e in record.removes] == ["lonely.h"]


@needs_binary
def test_live_compile_error_is_skipped_and_reports_nonzero(tmp_path):
    """编译失败 → 跳过，退出码非 0，但原因只能从文本里读。

    非 0 只说明「失败了」：编译错误、编译数据库加载失败、一次传多个文件
    都是同一个码。所以测试盯的是 ``skipped`` 与 ``skip_reason``——人拿到
    报告后要知道下一步做什么，靠退出码说不出来。
    """
    broken = tmp_path / "broken.cpp"
    broken.write_text(
        '#include "no_such_header_anywhere.h"\nint main(){return 0;}\n',
        encoding="utf-8",
    )

    record = run_on_unit(_BINARY, broken)
    assert record.skipped is True
    assert record.analysed is False
    assert record.removes == []
    assert record.exit_code != 0
    assert record.diagnostics


@needs_binary
def test_live_multiple_files_are_refused(tmp_path):
    """一次只能一个文件：传两个必须被识别为失败，而不是只读第一个。"""
    first = tmp_path / "a.cpp"
    second = tmp_path / "b.cpp"
    first.write_text("int main(){return 0;}\n", encoding="utf-8")
    second.write_text("int other(){return 0;}\n", encoding="utf-8")

    import subprocess

    done = subprocess.run(
        [str(_BINARY), "--print=changes", str(first), str(second), "--", "-std=c++17"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    record = parse_include_cleaner_output(
        (done.stdout or "") + "\n" + (done.stderr or ""), "a.cpp"
    )
    assert record.skipped is True
    # 明确是「一次只许一个」这条约束，而不是别的原因凑巧让退出码非 0。
    assert "single input file" in (record.skip_reason or "")


@needs_binary
def test_live_analyse_database_keeps_order_and_marks_skip(tmp_path):
    """批量分析：顺序按数据库原序，坏单元被标记而不是静默消失。"""
    broken = tmp_path / "broken.cpp"
    broken.write_text('#include "nope.h"\nint main(){return 0;}\n', encoding="utf-8")

    database = tmp_path / "compile_commands.json"
    # 第一个单元必须是**真能编过**的命令：走 -p 时工具直接照它解析，
    # 给一条残缺命令会让它失败，测试就成了在验证「假命令会让工具失败」。
    database.write_text(
        json.dumps(
            [
                {
                    "directory": str(DEMO_ROOT),
                    "file": str(DEMO_MAIN),
                    "command": (
                        f"clang++ -std=c++17 -I{DEMO_ROOT / 'include'} "
                        f"-c {DEMO_MAIN} -o {DEMO_MAIN}.o"
                    ),
                },
                {"directory": str(tmp_path), "file": str(broken), "command": "clang++ -c"},
            ]
        ),
        encoding="utf-8",
    )

    records = analyse_database(database, binary=_BINARY, jobs=1)
    assert len(records) == 2
    # 顺序是数据库的顺序，不是完成顺序——否则没法与扫描报告逐行对齐。
    assert Path(records[0].source).name == "main.cpp"
    assert records[0].analysed is True
    assert records[1].analysed is False
    assert records[1].skipped is True


@needs_binary
def test_live_end_to_end_against_our_own_scan(tmp_path):
    """完整闭环：真跑工具 → 与我们自己的候选对位 → 得出一致/分歧。

    这条是「第二个后端」这个说法能否成立的实际检验：不是解析器能不能解析，
    而是两套结论摆在一起时，人能不能从中读出可执行的判断。
    """
    scan = _scan(_candidate(str(DEMO_MAIN), str(DEMO_ROOT / "include" / "lonely.h"), 5))
    record = run_on_unit(
        _BINARY,
        DEMO_MAIN,
        extra_args=("-std=c++17", f"-I{DEMO_ROOT / 'include'}"),
    )
    comparison = compare_with_scan([record], scan)

    assert comparison.analysed_files == 1
    assert comparison.removals == 1
    (pair,) = comparison.pairs
    assert pair.has_candidate is True
    assert pair.matched_by == "行号"
