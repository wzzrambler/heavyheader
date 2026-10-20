"""副作用分层与宏体依赖：本轮（2026-10-20）两处判据的回归测试。

两条都是「文本层说错了，编译器说得对」的实例：

* UCRT 系统头里满是带 SAL 标注的**声明**，旧判据把它们当成「名字空间作用域里
  执行的动作」，于是 stdio.h / stdlib.h / string.h 的候选永久判「在用」；
* 依赖可以藏在宏体里：源码写 ``lua_getlocaledecpoint()``，``localeconv`` 只在
  宏体里出现，旧判据把 ``<locale.h>`` 判成「没用过」，删掉直接编译失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.attrib.usage import (
    IdentifierIndex,
    UsageVerdict,
    analyse_usage,
    macro_bodies_in,
    side_effect_kinds_in,
    side_effects_in,
)


def test_sal_annotated_declaration_is_not_an_side_effect():
    """``_Success_(...) char* gets_s(...);`` 是声明，不是执行。"""
    text = """
_Success_(return != 0)
_ACRTIMP char* __cdecl gets_s(char* _Buffer, rsize_t _Size);
_CRT_INSECURE_DEPRECATE_GLOBALS(_get_pgmptr ) _ACRTIMP char** __cdecl _get_pgmptr(void);
"""
    assert side_effects_in(text) == ()


def test_macro_registration_still_counts_as_a_side_effect():
    """doctest 的静态注册不能被上面那条判据放过去。"""
    kinds = side_effect_kinds_in('DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter);')
    assert kinds == (("call", 'DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter)'),)


def test_object_definition_with_initialiser_is_a_certain_side_effect():
    kinds = side_effect_kinds_in("static int reporter_id = register_reporter(&impl);")
    assert kinds and kinds[0][0] == "object"


def test_multiline_macro_body_is_not_parsed_as_a_statement():
    """``#define gco2ts(o) check_exp(...)`` 的续行不是名字空间作用域的语句。

    修之前 ``lstate.h`` 因为宏体里的 ``check_exp(...)`` 被判「有副作用」，
    连带把六条真实的删除机会锁死。
    """
    text = """
#define gco2ts(o)  \\
    check_exp(novariant((o)->tt) == LUA_TSTRING, &((cast_u(o))->ts))
#define gco2u(o)  check_exp((o)->tt == LUA_VUSERDATA, &((cast_u(o))->u))
"""
    assert side_effect_kinds_in(text) == ()


def test_macro_bodies_exclude_parameters():
    bodies = macro_bodies_in("#define sq(x) ((x) * (x))\n")
    assert bodies["sq"] == frozenset()


def test_macro_body_names_are_collected():
    bodies = macro_bodies_in("#define lua_getlocaledecpoint() (localeconv()->decimal_point[0])\n")
    assert bodies["lua_getlocaledecpoint"] == frozenset({"localeconv", "decimal_point"})


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_suspected_side_effect_demotes_to_unknown(tmp_path):
    """宏调用形态的副作用只到「无法判定」，不进推荐列表。"""
    header = _write(tmp_path / "registry.h", "REGISTER_THING(alpha, 0, AlphaThing);\n")
    source = _write(tmp_path / "unit.c", "int main(void) { return 0; }\n")
    signal = analyse_usage(source, header, closure=[header])
    assert signal.verdict is UsageVerdict.UNKNOWN
    assert signal.suspected_side_effects


def test_certain_side_effect_stays_in_use(tmp_path):
    header = _write(tmp_path / "registry.h", "static int alpha_id = register_alpha();\n")
    source = _write(tmp_path / "unit.c", "int main(void) { return 0; }\n")
    signal = analyse_usage(source, header, closure=[header])
    assert signal.verdict is UsageVerdict.IN_USE
    assert signal.side_effects


def test_macro_body_dependency_makes_it_in_use(tmp_path):
    """源码用的宏在幸存头里，宏体里的名字只有这条 include 提供 → 在用。"""
    header = _write(
        tmp_path / "locale.h", "_ACRTIMP struct lconv* __cdecl localeconv(void);\n"
    )
    survivor = _write(
        tmp_path / "luaconf.h",
        "#define lua_getlocaledecpoint() (localeconv()->decimal_point[0])\n",
    )
    source = _write(
        tmp_path / "liolib.c",
        '#include "locale.h"\n#include "luaconf.h"\n'
        "int f(void) { return lua_getlocaledecpoint(); }\n",
    )
    signal = analyse_usage(
        source,
        header,
        closure=[header],
        index=IdentifierIndex(),
        declarers={"localeconv": frozenset({header})},
        alias_defs={},
        macro_defs={
            "lua_getlocaledecpoint": ((survivor, frozenset({"localeconv", "decimal_point"})),)
        },
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert signal.macro_expansion == ("lua_getlocaledecpoint", "localeconv")


def test_macro_body_dependency_ignored_when_the_symbol_survives(tmp_path):
    """同一个名字幸存侧还有声明者 → 不是依赖。"""
    header = _write(tmp_path / "locale.h", "_ACRTIMP struct lconv* __cdecl localeconv(void);\n")
    survivor = _write(tmp_path / "luaconf.h", "#define lua_getlocaledecpoint() (localeconv())\n")
    other = _write(tmp_path / "also.h", "struct lconv* localeconv(void);\n")
    source = _write(
        tmp_path / "liolib.c",
        '#include "locale.h"\n#include "luaconf.h"\n'
        "int f(void) { return lua_getlocaledecpoint(); }\n",
    )
    signal = analyse_usage(
        source,
        header,
        closure=[header],
        index=IdentifierIndex(),
        declarers={
            "localeconv": frozenset({header, other}),
            "lua_getlocaledecpoint": frozenset({survivor}),
        },
        alias_defs={},
        macro_defs={"lua_getlocaledecpoint": ((survivor, frozenset({"localeconv"})),)},
    )
    assert signal.verdict is not UsageVerdict.IN_USE or not signal.macro_expansion


def test_macro_vanishing_with_the_header_is_left_to_the_hit_path(tmp_path):
    """宏本身随头消失时不走宏体判据——那条路由命中路径负责。"""
    header = _write(
        tmp_path / "stuff.h",
        "#define STUFF_LOCALE() (localeconv())\n_ACRTIMP int localeconv(void);\n",
    )
    source = _write(
        tmp_path / "unit.c",
        '#include "stuff.h"\nint f(void) { return 0; }\n',
    )
    signal = analyse_usage(
        source,
        header,
        closure=[header],
        index=IdentifierIndex(),
        declarers={"localeconv": frozenset({header})},
        alias_defs={},
        macro_defs={"STUFF_LOCALE": ((header, frozenset({"localeconv"})),)},
    )
    assert signal.macro_expansion == ()


@pytest.mark.parametrize("text", ["", "/* nothing here */\n"])
def test_empty_header_unknown_has_a_reason(tmp_path, text):
    header = _write(tmp_path / "empty.h", text)
    source = _write(tmp_path / "unit.c", '#include "empty.h"\nint f(void) { return 0; }\n')
    signal = analyse_usage(source, header, closure=[header])
    assert signal.verdict is UsageVerdict.UNKNOWN
    assert signal.note


def test_provider_inside_a_dead_conditional_region_does_not_count(tmp_path):
    """``#if defined(IMPLEMENT)`` 里的声明，本单元没定义 IMPLEMENT 就不算供给者。

    doctest 的 ``class TraversalState`` 正是这个形态：``doctest.h`` 里它被
    ``#if defined(DOCTEST_CONFIG_IMPLEMENT)`` 包着，文本层却把整包当成供给者，
    于是把 ``test_traversal.cpp → traversal.h`` 推荐了出来，删掉就编不过。
    """
    amalgam = _write(
        tmp_path / "amalgam.h",
        "#ifndef AMALGAM\n#define AMALGAM\n"
        "#if defined(IMPLEMENT) && !defined(SHARED)\n"
        "class TraversalState { public: int depth; };\n"
        "#endif\n"
        "int always_here(void);\n"
        "#endif\n",
    )
    index = IdentifierIndex()
    assert "TraversalState" in index.header_provider_ids(amalgam)
    assert "TraversalState" not in index.header_live_provider_ids(amalgam)
    assert "always_here" in index.header_live_provider_ids(amalgam)


def test_mention_inside_a_dead_ifdef_branch_is_not_a_use(tmp_path):
    """``#ifdef`` 没定义时，分支体里的引用不算「用到」。

    实测 zstd ``lib/common/pool.c → allocations.h``：``ZSTD_customCalloc`` /
    ``ZSTD_customFree`` 的全部引用都在 ``#ifdef ZSTD_MULTITHREAD`` 里，本构建
    没定义它。旧判定据此判「在用」，而真值是这个头整个可删、且是 zstd 上最大的
    一条（384 → 213 ms，解析文件 184 → 32）。
    """
    header = _write(tmp_path / "alloc.h", "void* ZSTD_customCalloc(unsigned long);\n")
    source = _write(
        tmp_path / "pool.c",
        '#include "alloc.h"\n'
        "#ifdef ZSTD_MULTITHREAD\n"
        "void f(void) { ZSTD_customCalloc(8UL); }\n"
        "#endif\n"
        "int live(void) { return 0; }\n",
    )
    signal = analyse_usage(
        source,
        header,
        closure=[header],
        index=IdentifierIndex(),
        declarers={"ZSTD_customCalloc": frozenset({header})},
        alias_defs={},
        defined_macros=frozenset(),
    )
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED


def test_mention_on_the_guard_line_still_counts_as_use(tmp_path):
    """条件指令**本身**永远被求值：``#ifdef NO_GZ`` 里的宏删了会换分支。"""
    header = _write(tmp_path / "gzguts.h", "#define NO_GZ 1\n")
    source = _write(
        tmp_path / "zutil.c",
        '#include "gzguts.h"\n'
        "#ifdef NO_GZ\n"
        "int compact(void) { return 1; }\n"
        "#else\n"
        "int compact(void) { return 0; }\n"
        "#endif\n",
    )
    signal = analyse_usage(
        source,
        header,
        closure=[header],
        index=IdentifierIndex(),
        declarers={"NO_GZ": frozenset({header})},
        alias_defs={},
        defined_macros=frozenset(),
    )
    assert signal.verdict is UsageVerdict.IN_USE
