"""实现片段（无 include guard 的头）的重复实例化站点。

这一组测试守的是一条容易被写错的边界：**同一个头被同一个单元包含多次时，
「删掉这一行」到底删掉了什么**。

* 片段（无 guard）：每一次包含都是一次独立的实例化（brotli 的 ``*_inc.h``
  靠 ``#define FN`` 再 ``#include`` 生成不同的符号）。删第 k 行**不等于**删掉
  这个头——另外几处照旧展开它。所以必须拆成 N 条站点候选，收益按「片段自身
  的一次解析」算，静态判据一律判不了（要展开 FN 才知道这一处生成了什么）。
* 接口头（带 guard）：删任何一行都不减少解析量（guard 让第二次起成为空操
  作），这不是优化机会——收益按 0 计，但仍要出现在列表里并说清理由，不能默默
  消失。
"""

from __future__ import annotations

import json

from heavyheader.attrib.graph import (
    HeaderSizes,
    build_header_stats,
    classify_headers,
    find_removal_candidates,
)
from heavyheader.models import CompilerFamily, IncludeEvent, ProjectScan, TranslationUnit
from heavyheader.report.jsonio import scan_from_dict, scan_to_dict

FRAGMENT = """int FN(A)(void);
int FN(B)(void);
"""

GUARDED = """#pragma once
int guarded_symbol(void);
"""


def _build(tmp_path):
    root = tmp_path
    fragment = root / "ops_inc.h"
    fragment.write_text(FRAGMENT, encoding="utf-8")
    guarded = root / "guarded.h"
    guarded.write_text(GUARDED, encoding="utf-8")
    source = root / "main.c"
    source.write_text(
        "#define FN(X) X ## One\n"
        '#include "ops_inc.h"\n'
        "#undef FN\n"
        "#define FN(X) X ## Two\n"
        '#include "ops_inc.h"\n'
        "#undef FN\n"
        "#ifdef FEATURE_A\n"
        '#include "guarded.h"\n'
        "#else\n"
        '#include "guarded.h"\n'
        "#endif\n"
        "int main(void) { return 0; }\n",
        encoding="utf-8",
    )
    edges = [
        ("main.c", "ops_inc.h", 0),
        ("main.c", "ops_inc.h", 0),
        ("main.c", "guarded.h", 0),
        ("main.c", "guarded.h", 0),
    ]
    tu = TranslationUnit(
        source=source,
        dependencies=[root / name for _, name, _ in edges],
        include_events=[
            IncludeEvent(includer=root / a, included=root / b, depth=d) for a, b, d in edges
        ],
    )
    scan = ProjectScan(
        root=root,
        compiler="clang",
        compiler_family=CompilerFamily.CLANG,
        standard="c11",
        tus=[tu],
    )
    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    scan.candidates = find_removal_candidates(
        scan, sizes, classify_headers(root, set(scan.header_stats))
    )
    return scan, root


def _for(candidates, name: str):
    return [c for c in candidates if c.header.name == name]


def test_fragment_expand_into_one_candidate_per_site(tmp_path):
    scan, root = _build(tmp_path)
    sites = _for(scan.candidates, "ops_inc.h")
    # 3 处包含 → 2 条站点候选（上面写了两次），每条都指向**具体的行**。
    assert len(sites) == 2
    assert [c.line for c in sites] == [2, 5]
    assert all(c.instantiation for c in sites)
    assert all(c.usage_verdict == "unknown" for c in sites)
    # 收益按片段自身的一次解析算（2 行），不是它下游的闭包。
    assert all(c.estimated_lines_saved == 2 for c in sites)
    assert all(c.exclusive_headers == [root / "ops_inc.h"] for c in sites)


def test_fragment_reason_says_experiments_must_decide(tmp_path):
    scan, _ = _build(tmp_path)
    first = _for(scan.candidates, "ops_inc.h")[0]
    assert "实例化" in first.usage_reason
    assert "实验" in first.usage_reason
    assert any("被包含 2 次" in note for note in first.notes)


def test_guarded_header_repeated_is_not_an_opportunity(tmp_path):
    scan, _ = _build(tmp_path)
    entries = _for(scan.candidates, "guarded.h")
    # 不是机会，但不能默默消失：留一条并说清为什么。
    assert len(entries) == 1
    entry = entries[0]
    assert entry.estimated_lines_saved == 0
    assert entry.estimated_seconds_saved == 0.0
    assert entry.instantiation is False
    assert "不是优化机会" in " ".join(entry.notes)
    assert "include guard" in entry.usage_reason


def test_instantiation_survives_json_roundtrip(tmp_path):
    scan, _ = _build(tmp_path)
    payload = json.loads(json.dumps(scan_to_dict(scan)))
    restored = scan_from_dict(payload)
    restored_sites = _for(restored.candidates, "ops_inc.h")
    assert len(restored_sites) == 2
    # 丢了它，重载出来的候选会退化成「删掉整个头」：既高估收益，又绕过强制闸门。
    assert all(c.instantiation for c in restored_sites)
    assert [c.line for c in restored_sites] == [2, 5]
