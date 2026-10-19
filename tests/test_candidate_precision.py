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
