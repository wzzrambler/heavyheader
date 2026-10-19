"""阻断归因的测试。

样本全部取自项目里真实见过的编译错误（fmt / 构造工程 / MSVC 方言），
因为这类解析器的价值全在「真实报错长什么样」上，自己编的字符串验不出什么。
"""

from __future__ import annotations

from pathlib import Path

from heavyheader.attrib.blockers import (
    Attribution,
    BlockerKind,
    Remedy,
    attribute,
    parse_blockers,
)


def test_parses_gcc_missing_symbol_with_location():
    blockers = parse_blockers(
        "In file included from app/main.cpp:1:\n"
        "app/main.cpp:6:14: error: 'Core' was not declared in this scope\n"
    )
    assert len(blockers) == 1
    first = blockers[0]
    assert first.symbol == "Core"
    assert first.kind is BlockerKind.MISSING
    # 只留文件名：报告里不需要临时目录前缀，跨平台渲染也不会撕裂
    assert first.file == "main.cpp"
    assert first.line == 6


def test_parses_macro_and_type_phrasings():
    macro = parse_blockers("format.cc:10:1: error: 'FMT_BEGIN_NAMESPACE' does not name a type")
    assert macro[0].symbol == "FMT_BEGIN_NAMESPACE"
    c_style = parse_blockers("main.c:5:9: error: unknown type name 'Widget'")
    assert c_style[0].symbol == "Widget"
    incomplete = parse_blockers(
        "a.cpp:3:7: error: incomplete type 'Foo' used in nested name specifier"
    )
    assert incomplete[0].kind is BlockerKind.INCOMPLETE


def test_parses_msvc_dialect():
    blockers = parse_blockers("a.cpp(12): error C2065: 'Foo': undeclared identifier")
    assert blockers[0].symbol == "Foo"
    assert blockers[0].line == 12


def test_dedupes_repeated_symbol_across_units():
    text = (
        "a.cpp:1:1: error: 'Core' was not declared in this scope\n"
        "b.cpp:2:1: error: 'Core' was not declared in this scope\n"
    )
    assert len(parse_blockers(text)) == 1


def test_non_declaration_error_is_unrelated():
    result = attribute("x.cpp:1:1: error: expected ';' before 'x'")
    assert result.remedy is Remedy.UNRELATED
    assert result.blocker is None


def test_pointer_only_type_is_forward_declarable(tmp_path: Path):
    header = tmp_path / "core.h"
    header.write_text("class Core {\npublic:\n  int v;\n};\n", encoding="utf-8")
    source = "Core* make();\nvoid use(Core& c);\n"
    result = attribute(
        "main.cpp:3:8: error: 'Core' was not declared in this scope", header, source
    )
    assert result.blocker is not None
    assert result.remedy is Remedy.FORWARD_DECLARE
    assert "前向声明" in result.detail


def test_type_used_by_value_needs_definition(tmp_path: Path):
    header = tmp_path / "core.h"
    header.write_text("class Core {\npublic:\n  int v;\n};\n", encoding="utf-8")
    source = "Core instance;\nint f() { return instance.v; }\n"
    result = attribute(
        "main.cpp:1:1: error: 'Core' was not declared in this scope", header, source
    )
    assert result.remedy is Remedy.NEEDS_DEFINITION


def test_macro_symbol_is_not_a_type(tmp_path: Path):
    header = tmp_path / "fmt.h"
    header.write_text("#define FMT_BEGIN_NAMESPACE namespace fmt {\n", encoding="utf-8")
    result = attribute(
        "format.cc:10:1: error: 'FMT_BEGIN_NAMESPACE' does not name a type",
        header,
        "FMT_BEGIN_NAMESPACE\n",
    )
    assert result.remedy is Remedy.NOT_A_TYPE
    assert "宏" in result.detail


def test_member_error_means_full_definition(tmp_path: Path):
    header = tmp_path / "core.h"
    header.write_text("class Core { public: int v; };\n", encoding="utf-8")
    result = attribute(
        "main.cpp:2:3: error: 'Core' is not a member of 'ns'", header, "ns::Core c;\n"
    )
    assert result.remedy is Remedy.NEEDS_DEFINITION


def test_attribution_sentence_mentions_symbol_and_place():
    result = attribute("util.cpp:3:14: error: 'Core' was not declared in this scope")
    text = result.sentence()
    assert "Core" in text and "util.cpp:3" in text
    assert isinstance(result, Attribution)
