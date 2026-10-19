"""候选精度相关的回归测试。

两条改动都是「把已经证明是误判的口径换掉」，所以每条都要有能复现旧错误的用例：

1. **供给者唯一性**：命中一个标识符不等于这个 include 被使用——同名符号常常还有
   别的头在提供。旧判定在 doctest 上把 60/60 条候选全判「正在使用」，而真实编译
   有 29 条能过。
2. **潜在供给边**：``-H`` 只记录首次生效的包含点，被 include guard 吞掉的第二次
   包含不在树里。旧闭包据此宣布「删掉会带走 275 个头」，实际一个都没少。
"""

from __future__ import annotations

from pathlib import Path

from heavyheader.attrib.graph import (
    IncludeMentions,
    _potential_adjacency,
    closure_of,
)
from heavyheader.attrib.usage import IdentifierIndex, UsageVerdict, analyse_usage
from heavyheader.models import IncludeEvent, TranslationUnit


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_cross_provided_identifier_is_not_usage_evidence(tmp_path):
    """TU 用到 Widget，但另一个头也声明它 → 删掉这条 include 不会缺东西。

    这正是 doctest 上 31 条误判的形态：``reporter.h`` 声明的一堆名字，在别的
    仍然保留的头里也有。旧逻辑看到「闭包声明 + 源码提到」就判「正在使用」。
    """
    a = _write(tmp_path / "a.h", "struct Widget { int x; };\n")
    # 第二个头也声明同名类型：它才是「另有供给者」
    _write(tmp_path / "b.h", "struct Widget { int x; };\n")
    src = _write(
        tmp_path / "main.cpp",
        '#include "a.h"\n#include "b.h"\nint f() { Widget w; return w.x; }\n',
    )

    signal = analyse_usage(
        src,
        a,
        closure=[a],
        index=IdentifierIndex(),
        reachable_counts={"Widget": 2},  # a.h 与 b.h 都声明
    )
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED
    assert "Widget" in signal.cross_provided


def test_sole_provider_identifier_is_usage_evidence(tmp_path):
    """只有一个头提供这个符号时，删除就是真的缺东西——不能为了精度把它也放过。"""
    a = _write(tmp_path / "a.h", "struct Widget { int x; };\n")
    src = _write(tmp_path / "main.cpp", '#include "a.h"\nint f() { Widget w; return w.x; }\n')

    signal = analyse_usage(
        src, a, closure=[a], index=IdentifierIndex(), reachable_counts={"Widget": 1}
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert signal.provided_by == a


def test_without_counts_old_behaviour_is_kept(tmp_path):
    """不传 reachable_counts 时退回旧口径（调用方必须显式选择新口径）。"""
    a = _write(tmp_path / "a.h", "struct Widget { int x; };\n")
    src = _write(tmp_path / "main.cpp", '#include "a.h"\nint f() { Widget w; return w.x; }\n')

    assert analyse_usage(src, a, closure=[a], index=IdentifierIndex()).verdict is (
        UsageVerdict.IN_USE
    )


def test_rename_define_in_deleted_header_still_provides(tmp_path):
    """被删头里的 ``#define X Y`` 是在**定义**宏 X——删掉它 X 就没了。

    复刻 zlib minigzip.c 的形态：``fcntl.h`` 定义 ``O_BINARY``（形如重命名
    宏），源码用到它，删除后 ``use of undeclared identifier 'O_BINARY'``。
    一版修复把重命名宏从**所有**供给集合里剥掉，这里唯一的证据被误杀，
    判成可删——两侧语义不同：闭包侧看定义，幸存头侧才剥别名。
    """
    fcntl = _write(tmp_path / "fcntl.h", "#define O_BINARY _O_BINARY\n")
    src = _write(
        tmp_path / "main.c",
        '#include "fcntl.h"\nint f() { return O_BINARY == 0; }\n',
    )

    signal = analyse_usage(
        src, fcntl, closure=[fcntl], index=IdentifierIndex(), reachable_counts={}
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert signal.provided_by == fcntl


def test_rename_define_in_surviving_header_is_not_a_provider(tmp_path):
    """幸存头里的 ``#define X Y`` 只是别名——不能证明 Y 在删除后还有声明。

    复刻 zlib inffast.c 的形态：``zconf.h``（幸存）把 ``inflate_fast`` 重命名成
    ``z_inflate_fast``，真声明在被删的 ``inffast.h`` 里。重命名不算供给，
    所以 X 仍由闭包唯一提供 → 判「正在使用」（真值：删除确实编译失败）。
    """
    zconf = _write(tmp_path / "zconf.h", "#define inflate_fast z_inflate_fast\n")
    inffast = _write(tmp_path / "inffast.h", "void z_inflate_fast(void);\n")
    # 源码**调用**而不是定义：定义本身就会提供这个名字（见 definitions_in），
    # 那样测不到「幸存头一侧剥不剥别名」这件事。
    src = _write(
        tmp_path / "inffast.c",
        '#include "zconf.h"\n#include "inffast.h"\nvoid run(void) { z_inflate_fast(); }\n',
    )

    index = IdentifierIndex()
    # 头一侧：zconf.h 的文本确实提到 inflate_fast（供闭包侧使用）
    assert "inflate_fast" in index.header_ids(zconf)
    # 幸存头一侧：剥掉别名后不再算供给者（graph.py 的 reachable_counts 用它）
    assert "inflate_fast" not in index.header_provider_ids(zconf)

    signal = analyse_usage(
        src,
        inffast,
        closure=[inffast],
        index=index,
        # zconf.h 幸存且它对 inflate_fast 的供给被剥掉 → reachable_counts 里
        # 没有这个名字 → 闭包是唯一供给者
        reachable_counts={},
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert signal.provided_by == inffast


def test_directly_included_header_never_disappears(tmp_path):
    """候选下游里的另一个**直接包含**不能算进消失集合。

    ``closure_of`` 按定义不含起点，所以「别的直接 include 的头」只有被显式
    加进 ``shared`` 才会被排除。漏掉这一步时，zlib ``zutil.c -> gzguts.h``
    把 ``zutil.h``（另一条直接 include）算成会消失，于是两个头都定义的
    ``local`` 被判成「消失集合唯一提供 → 在用」；真值是删掉照样编。
    """
    _write(tmp_path / "zutil.h", "#define local static\n")
    guts = _write(tmp_path / "gzguts.h", '#include "zutil.h"\n#define local static\n')
    src = _write(
        tmp_path / "zutil.c",
        '#include "zutil.h"\n#include "gzguts.h"\nlocal int counter = 0;\n',
    )

    # 消失集合里只剩 gzguts.h：zutil.h 是直接 include，删不掉
    signal = analyse_usage(
        src,
        guts,
        closure=[guts],
        index=IdentifierIndex(),
        # zutil.h（幸存）+ gzguts.h 都提供 local → 2 > 1 → 另有供给
        reachable_counts={"local": 2},
    )
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED
    assert "local" in signal.cross_provided


def test_source_defined_function_is_not_a_dependency(tmp_path):
    """源码自己定义的函数与形参名，不是「头文件被使用」的证据。

    zlib ``inffast.c`` 定义了 ``inflate_fast(strm, start)``，而 ``inffast.h``
    只是声明它。旧判定看到源码里满是这两个名字就判「在用」——真值是删掉
    ``#include "inffast.h"`` 之后编译通过、且真的少解析一个文件。
    """
    header = _write(tmp_path / "inffast.h", "void inflate_fast(unsigned start);\n")
    src = _write(
        tmp_path / "inffast.c",
        '#include "inffast.h"\nvoid inflate_fast(unsigned start) {}\n',
    )

    signal = analyse_usage(
        src,
        header,
        closure=[header],
        index=IdentifierIndex(),
        reachable_counts={"inflate_fast": 1, "start": 1},
    )
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED
    assert {"inflate_fast", "start"} <= set(signal.self_provided)


def test_macro_call_that_looks_like_a_definition_is_still_a_use(tmp_path):
    """``MACRO(x) {`` 展开出来像函数定义，但宏名是头文件给的。

    要求定义前有「类型样式」的前缀正是为了这个：``TEST_CASE(one) {`` 前面
    什么都没有，不能被认成定义——否则删掉提供该宏的头就编不过了。
    """
    deep = _write(tmp_path / "deep.h", "#define TEST_CASE(name) void name()\n")
    compat = _write(tmp_path / "compat.h", '#include "deep.h"\n')
    src = _write(
        tmp_path / "main.cpp",
        '#include "compat.h"\nTEST_CASE(one) {}\n',
    )

    signal = analyse_usage(src, compat, closure=[compat, deep], index=IdentifierIndex())
    assert signal.verdict is UsageVerdict.IN_USE
    assert "TEST_CASE" in signal.shared_identifiers


def test_define_expanding_to_a_keyword_is_a_macro_not_an_alias(tmp_path):
    """``#define local static`` 是**定义宏**，不是给 ``static`` 起别名。

    把它当重命名剥掉之后，``local`` 从供给集合里消失，反而让唯一证据被抹掉。
    zlib 里 ``zutil.h`` 与 ``gzguts.h`` 都这么定义 ``local``。
    """
    other = _write(tmp_path / "other.h", "#define local static\n")
    index = IdentifierIndex()
    assert "local" in index.header_provider_ids(other)

    # 真正的重命名仍然要剥：目标是另一个标识符，别名不能证明目标有声明
    alias = _write(tmp_path / "alias.h", "#define inflate_fast z_inflate_fast\n")
    assert "inflate_fast" not in IdentifierIndex().header_provider_ids(alias)


def test_macro_used_only_in_ifdef_stays_in_use(tmp_path):
    """只出现在 ``#ifdef`` 里的宏**仍然判在用**——不能为了召回率降级。

    删掉定义它的头之后编译照样通过，它只是换了一个分支被编译。zlib
    ``zutil.c -> gzguts.h`` 就是这样：编译通过、少解析 15 个文件，看起来是
    一条完美的优化，但汇编指纹从 ``aac1b5f8…`` 变成 ``9b6bde46…``——被激活的
    分支变了，``zlibCompileFlags()`` 的返回值随之改变。判定必须挡住它，并
    把「理由是换分支而不是缺声明」单独记下来。
    """
    guts = _write(tmp_path / "gzguts.h", "#define NO_GZCOMPRESS\n")
    src = _write(
        tmp_path / "zutil.c",
        '#include "gzguts.h"\nlong f(void) {\n'
        "    long flags = 0;\n#ifdef NO_GZCOMPRESS\n    flags += 1L << 16;\n#endif\n"
        "    return flags;\n}\n",
    )

    signal = analyse_usage(
        src,
        guts,
        closure=[guts],
        index=IdentifierIndex(),
        reachable_counts={"NO_GZCOMPRESS": 1},
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert "NO_GZCOMPRESS" in signal.conditional_only


def test_guarded_re_include_becomes_a_potential_edge(tmp_path):
    """``-H`` 里没有的边，必须从文本补回来。

    复刻 doctest 的形态：``main.cpp`` 先包含 ``prelude.h``，再包含 ``type.h``；
    ``type.h`` 自己也有 ``#include "prelude.h"``，但第二次被 guard 吞掉，所以
    ``-H`` 树里没有这条边。旧闭包因此认为「删掉 main.cpp 的直连会让 prelude.h
    及其下游消失」，实际一个文件都没少。
    """
    prelude = _write(tmp_path / "prelude.h", '#include "extra.h"\nstruct Common {};\n')
    extra = _write(tmp_path / "extra.h", "struct Extra {};\n")
    type_h = _write(tmp_path / "type.h", '#include "prelude.h"\nstruct Type {};\n')
    src = _write(
        tmp_path / "main.cpp",
        '#include "prelude.h"\n#include "type.h"\nint main() { return 0; }\n',
    )

    tu = TranslationUnit(
        source=src,
        include_events=[
            IncludeEvent(includer=src, included=prelude, depth=0),
            IncludeEvent(includer=prelude, included=extra, depth=1),
            IncludeEvent(includer=src, included=type_h, depth=0),
            # 注意：type.h -> prelude.h 这条被 guard 吞掉，故意不出现
        ],
    )
    parsed = {src, prelude, extra, type_h}

    adjacency = _potential_adjacency(tu, IncludeMentions(), parsed)
    assert prelude in adjacency[type_h], "被 guard 吞掉的包含边没有被补回来"

    # 于是 prelude.h 在 type.h 的闭包里 → 删掉 main.cpp 的直连时它是「共享」的，
    # 不再被算成独占闭包（旧实现会把整棵 275 个头的子树算进来）。
    assert prelude in closure_of(adjacency, type_h)


def test_unresolvable_mention_does_not_invent_an_edge(tmp_path):
    """同名的两个头且无法相对定位时不猜——宁可少补一条边。"""
    one = _write(tmp_path / "a" / "thing.h", "struct A {};\n")
    two = _write(tmp_path / "b" / "thing.h", "struct B {};\n")
    src = _write(tmp_path / "main.cpp", '#include "thing.h"\n')
    user = _write(tmp_path / "user.h", '#include "thing.h"\n')

    tu = TranslationUnit(
        source=src,
        include_events=[
            IncludeEvent(includer=src, included=one, depth=0),
            IncludeEvent(includer=src, included=two, depth=0),
            IncludeEvent(includer=src, included=user, depth=0),
        ],
    )
    adjacency = _potential_adjacency(tu, IncludeMentions(), {src, one, two, user})
    assert adjacency.get(user, []) == []


def test_pasted_macro_name_blocks_unused_verdict(tmp_path):
    """``##`` 拼出来的名字两边文本里都不存在——文本层唯一能抓到的线索。

    复刻 Lua lcode.c 的形态：源码写 ``l_floatatt(MANT_DIG)``，宏把它拼成
    ``DBL_MANT_DIG``，而这个名字由被删的 ``<float.h>`` 提供。旧判定在源码里
    找不到 ``DBL_MANT_DIG`` 就判「疑似未使用」，删除后报
    ``use of undeclared identifier 'DBL_MANT_DIG'``。
    """
    lgc = _write(tmp_path / "float.h", "#define DBL_MANT_DIG 53\n")
    # float.h 是「只声明宏的头」：闭包里再放一个有真实声明的伴头，
    # 否则判定会走「无闭包聚合头」的保守分支，测不到粘贴规则。
    companion = _write(tmp_path / "float-decls.h", "typedef int floatmax_t;\n")
    luaconf = _write(tmp_path / "luaconf.h", "#define l_floatatt(n) (DBL_##n)\n")
    src = _write(
        tmp_path / "lcode.c",
        '#include "float.h"\n#include "luaconf.h"\nint f(void) { return l_floatatt(MANT_DIG); }\n',
    )

    index = IdentifierIndex()
    prefixes, _suffixes = index.header_paste_affixes(luaconf)
    assert prefixes == frozenset({"DBL_"})

    signal = analyse_usage(
        src,
        lgc,
        closure=[lgc, companion],
        index=index,
        reachable_counts={},
        paste_affixes=(prefixes, frozenset()),
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert "DBL_MANT_DIG" in signal.pasted


def test_surviving_header_mention_is_not_a_provider(tmp_path):
    """幸存头在宏体里**调用**一个函数不等于它**声明**了这个函数。

    复刻 Lua ldebug.c 的形态：``ldo.h`` 的宏展开体里有 ``luaC_checkGC``，
    真声明在被删的 ``lgc.h``。旧口径按「文本出现过」算供给，ldo.h 也被当成
    另一个供给者 → 判「疑似未使用」，删除后
    ``call to undeclared function 'luaC_checkGC'``。
    """
    ldo = _write(
        tmp_path / "ldo.h",
        "#define luaD_call(L, f) (luaD_callnoyield(L, f), luaC_checkGC(L))\n",
    )
    lgc = _write(tmp_path / "lgc.h", "void luaC_checkGC(struct lua_State *L);\n")
    src = _write(
        tmp_path / "ldebug.c",
        '#include "ldo.h"\n#include "lgc.h"\nvoid run(struct lua_State *L) { luaC_checkGC(L); }\n',
    )

    index = IdentifierIndex()
    # 闭包侧（header_ids）确实能看到 ldo.h 提到这个名字；
    # 幸存头侧（header_provider_ids）只认声明形态，宏体里的调用不算。
    assert "luaC_checkGC" in index.header_ids(ldo)
    assert "luaC_checkGC" not in index.header_provider_ids(ldo)
    assert "luaC_checkGC" in index.header_provider_ids(lgc)

    # 模拟 graph.py 的 reachable_counts：按 provider 口径数供给者。
    counts = sum(
        1 for p in (ldo, lgc) if "luaC_checkGC" in index.header_provider_ids(p)
    )
    signal = analyse_usage(
        src,
        lgc,
        closure=[lgc],
        index=index,
        reachable_counts={"luaC_checkGC": counts},
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert signal.provided_by == lgc


def test_command_line_macro_is_not_keep_evidence(tmp_path):
    """可达集里**没有任何头**提供的命中是幻影——删除 include 带不走它。

    复刻 doctest handler.cpp 的形态：``DOCTEST_CONFIG_DISABLE`` 只出现在
    ``#ifdef`` 里，真正的定义来自编译命令行 ``-D``。旧判定看到「闭包提到 +
    源码提到」就判「在用」，真值是删除后照样编译。
    """
    a = _write(tmp_path / "a.h", "#ifdef DOCTEST_CONFIG_DISABLE\nint x;\n#endif\n")
    src = _write(
        tmp_path / "main.c",
        '#include "a.h"\n#ifdef DOCTEST_CONFIG_DISABLE\nint y;\n#endif\n',
    )

    signal = analyse_usage(
        src,
        a,
        closure=[a],
        index=IdentifierIndex(),
        declarers={},
        alias_defs={},
    )
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED
    assert "DOCTEST_CONFIG_DISABLE" in signal.unprovided


def test_vanishing_alias_with_surviving_target_is_a_provider(tmp_path):
    """幸存头里的 ``#define X Y``：只要 Y 在幸存侧仍有声明者，X 就还可用。

    复刻 doctest 的警告宏链：候选头提到 ``POP``，而 ``POP`` 的定义是
    幸存头 ``warnings.h`` 里的 ``#define POP COMMON_POP``，``COMMON_POP``
    又由幸存头 ``doctest.h`` 定义。删掉候选后 POP 照常展开——命中不构成
    保留证据。
    """
    doctest_h = _write(tmp_path / "doctest.h", "#define COMMON_POP\nint x;\n")
    warnings_h = _write(tmp_path / "warnings.h", "#define POP COMMON_POP\n")
    old_h = _write(tmp_path / "old.h", "POP\nint old_api(void);\n")
    src = _write(
        tmp_path / "main.c",
        '#include "doctest.h"\n#include "warnings.h"\n#include "old.h"\nPOP\n',
    )

    # 模拟 graph.py 的两张底账：真实声明者（别名剥除）+ 别名定义处
    declarers = {"COMMON_POP": frozenset({doctest_h})}
    alias_defs = {"POP": ((warnings_h, "COMMON_POP"),)}
    signal = analyse_usage(
        src,
        old_h,
        closure=[old_h],
        index=IdentifierIndex(),
        declarers=declarers,
        alias_defs=alias_defs,
    )
    # 命中的名字由幸存头经别名链继续提供 → 疑似未使用
    assert signal.verdict is UsageVerdict.PLAUSIBLY_UNUSED


def test_vanishing_alias_with_dying_target_still_in_use(tmp_path):
    """别名目标只活在被删闭包里时，幸存头救不了它——zlib inflate_fast 形态。

    ``zconf.h``（幸存）把 ``foo`` 别名到 ``bar``，而 ``bar`` 的声明随被删的
    ``old.h`` 一起消失。删除后别名展开到一个未声明的名字——必须判「在用」。
    """
    zconf = _write(tmp_path / "zconf.h", "#define foo bar\n")
    old_h = _write(tmp_path / "old.h", "int old_api(int foo);\n")
    src = _write(
        tmp_path / "main.c",
        '#include "zconf.h"\n#include "old.h"\nint run(void) { return foo(); }\n',
    )

    declarers = {}  # bar 的声明随 old.h 一起消失
    alias_defs = {"foo": ((zconf, "bar"),)}
    signal = analyse_usage(
        src,
        old_h,
        closure=[old_h],
        index=IdentifierIndex(),
        declarers=declarers,
        alias_defs=alias_defs,
    )
    assert signal.verdict is UsageVerdict.IN_USE


def test_vanishing_side_alias_dies_with_its_header(tmp_path):
    """被删闭包里的 ``#define X Y``：X 这个名字随头一起消失。

    复刻 minigzip 的 ``O_BINARY``：目标 ``_O_BINARY`` 是系统头里到处都有的
    宏，但定义 O_BINARY 这个名字的只有被删的那一个头——删除后照样
    ``undeclared identifier``。
    """
    fcntl_h = _write(tmp_path / "fcntl.h", "#define O_BINARY _O_BINARY\n")
    system_h = _write(tmp_path / "system.h", "#define _O_BINARY 0x8000\n")
    src = _write(
        tmp_path / "main.c",
        '#include "fcntl.h"\n#include "system.h"\nint f(void) { return O_BINARY == 0; }\n',
    )

    declarers = {"_O_BINARY": frozenset({system_h})}
    alias_defs = {"O_BINARY": ((fcntl_h, "_O_BINARY"),)}
    signal = analyse_usage(
        src,
        fcntl_h,
        closure=[fcntl_h],
        index=IdentifierIndex(),
        declarers=declarers,
        alias_defs=alias_defs,
    )
    assert signal.verdict is UsageVerdict.IN_USE
    assert signal.provided_by == fcntl_h


def test_macro_qualified_definition_still_declares(tmp_path):
    """宏名当类型限定符的表定义也是声明——zlib trees.h / crc32.h 的形态。

    ``local const ct_data static_ltree[D_CODES] = {…}`` 里的 ``local`` 是宏、
    ``ct_data`` 是自定义类型。旧的对象声明正则只认关键字限定符，这批表在
    头文件和源文件里都「没有声明者」，四条真依赖被判成「另有供给 → 疑似
    未使用」，删除后 ``use of undeclared identifier 'static_ltree'``。
    """
    trees = _write(
        tmp_path / "trees.h",
        "#define local static\ntypedef struct ct_data_s { int f; } ct_data;\n"
        "local const ct_data static_ltree[10] = { {0, 0} };\n",
    )
    src = _write(
        tmp_path / "trees.c",
        '#include "trees.h"\nint pick(int i) { return static_ltree[i].f; }\n',
    )

    index = IdentifierIndex()
    assert "static_ltree" in index.header_provider_ids(trees)

    signal = analyse_usage(
        src,
        trees,
        closure=[trees],
        index=index,
        declarers={"static_ltree": frozenset({trees})},
        alias_defs={},
    )
    assert signal.verdict is UsageVerdict.IN_USE


def test_conditional_branch_definition_is_not_self_supply(tmp_path):
    """非激活条件分支里的定义不能当自供给——zlib crc32.c 的形态。

    ``#ifdef DYNAMIC_CRC_TABLE`` 分支里定义了 ``x2n_table``，本构建该分支
    **没有**被编译，名字实际来自被删的 ``crc32.h``。旧的位置判据看到
    「定义在使用之前」就把名字从证据里剔掉了，真依赖凭空消失，判成可删，
    删除后 ``use of undeclared identifier 'x2n_table'``。
    """
    crc_h = _write(tmp_path / "crc32.h", "local const int x2n_table[32] = {0};\n")
    src = _write(
        tmp_path / "crc32.c",
        '#include "crc32.h"\n'
        "#ifdef DYNAMIC_CRC_TABLE\nlocal const int x2n_table[32] = {0};\n#endif\n"
        "int use(void) { return x2n_table[0]; }\n",
    )

    index = IdentifierIndex()
    # 源码里那个定义在条件区间内 → 不算自供给，名字仍是证据
    assert "x2n_table" not in index.source_self_supplied_ids(src)

    signal = analyse_usage(
        src,
        crc_h,
        closure=[crc_h],
        index=index,
        declarers={"x2n_table": frozenset({crc_h})},
        alias_defs={},
    )
    assert signal.verdict is UsageVerdict.IN_USE


def test_angle_mention_does_not_match_relative(tmp_path):
    """尖括号包含不搜索 includer 所在目录——相对启发只对引号成立。

    复刻 Lua 的形态：UCRT 的 ``stdlib.h`` 里 ``#include <limits.h>``，同目录
    也有一个 limits.h，但编译器实际解析到 clang 内建目录的包装头。按相对
    启发解析会把边补到错的文件上，limits.h 被误判成「独占」，造出一条
    预估很高、实测少解析 0 个文件的空头承诺候选。
    """
    wrapper = _write(tmp_path / "wrapper" / "limits.h", "#define INT_MAX 1\n")
    impl = _write(tmp_path / "impl" / "limits.h", "#define INT_MAX 1\n")
    src = _write(tmp_path / "main.c", "#include <limits.h>\nint f(void);\n")
    stdlib = _write(tmp_path / "impl" / "stdlib.h", "#include <limits.h>\n")

    tu = TranslationUnit(
        source=src,
        include_events=[
            IncludeEvent(includer=src, included=wrapper, depth=0),
        ],
    )
    parsed = {src, wrapper, impl, stdlib}

    adjacency = _potential_adjacency(tu, IncludeMentions(), parsed)
    # 尖括号 mention 不做相对解析；wrapper/impl 同名无法定位 → 不猜。
    # 旧实现会把边补到同目录的 impl 上——那是编译器根本不会解析的文件。
    assert all("limits.h" not in str(t) for t in adjacency.get(stdlib, []))


def test_shadowed_same_name_candidate_is_not_the_resolution(tmp_path):
    """同名候选里「树里由另一个候选激活」的是遮蔽面——补边绕开它。

    clang 的包装 ``limits.h`` 与 UCRT 的实现 ``limits.h`` 成对出现；后者
    在树里只作为前者的孩子。``<limits.h>`` 的补全边应落在包装头上，
    否则系统头链条的到达路径会漏。
    """
    wrapper = _write(tmp_path / "sys1" / "limits.h", "#define INT_MAX 1\n")
    impl = _write(tmp_path / "sys2" / "limits.h", "#define INT_MAX 1\n")
    src = _write(tmp_path / "main.c", "#include <limits.h>\nint f(void);\n")

    tu = TranslationUnit(
        source=src,
        include_events=[
            IncludeEvent(includer=src, included=wrapper, depth=0),
            # 包装头把实现头带进来（include_next）
            IncludeEvent(includer=wrapper, included=impl, depth=1),
        ],
    )
    parsed = {src, wrapper, impl}

    # 关键断言：<limits.h> 的解析结果是 wrapper，而非被它带进来的 impl
    from collections import defaultdict

    from heavyheader.attrib.graph import _resolve_mention, _shadowed_same_name

    by_name = defaultdict(list)
    for p in parsed:
        by_name[p.name].append(p)
    shadowed = _shadowed_same_name(tu, by_name)
    assert shadowed == {impl}
    assert _resolve_mention("limits.h", src, by_name, True, shadowed) == [wrapper]
