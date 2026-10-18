"""边级边际成本计算的单元测试。

重点验证语义而不是数字：独占闭包为空时必须消失（删了不省），共享路径不能
重复计入，跨单元同一条边要相加。
"""

from __future__ import annotations

from heavyheader.attrib.edgecost import aggregate_edge_costs, unit_edge_costs


def test_edge_closure_is_only_what_disappears():
    """链 a.cpp -> b.h -> c.h：删 b->c 只省 c.h，不省 b.h 自己。"""
    edges = [("a.cpp", "b.h"), ("b.h", "c.h")]
    rows = unit_edge_costs("a.cpp", edges, {"b.h": 0.5, "c.h": 2.0}, {"b.h": 10, "c.h": 100})
    by_edge = {(p, c): (s, n) for p, c, s, n in rows}
    assert by_edge[("b.h", "c.h")] == (2.0, 100)
    # a.cpp -> b.h 的闭包是 {b.h, c.h}
    assert by_edge[("a.cpp", "b.h")] == (2.5, 110)


def test_shared_path_edge_has_empty_closure_and_is_dropped():
    """a.cpp 同时直达 c.h：删 b->c 后 c.h 仍被解析，这条边的边际为零。"""
    edges = [("a.cpp", "b.h"), ("a.cpp", "c.h"), ("b.h", "c.h")]
    rows = unit_edge_costs("a.cpp", edges, {"c.h": 2.0}, {"c.h": 100})
    assert ("b.h", "c.h") not in {(p, c) for p, c, _s, _n in rows}


def test_diamond_shares_only_the_exclusive_part():
    """菱形：a->b->d, a->c->d。删 b->d 不省（d 仍从 c 到达）。"""
    edges = [("a.cpp", "b.h"), ("b.h", "d.h"), ("a.cpp", "c.h"), ("c.h", "d.h")]
    rows = unit_edge_costs("a.cpp", edges, {"d.h": 1.0}, {"d.h": 50})
    keys = {(p, c) for p, c, _s, _n in rows}
    assert ("b.h", "d.h") not in keys
    assert ("c.h", "d.h") not in keys
    # 但删 a.cpp -> b.h 会省 b.h（d 仍从 c 到）
    by_edge = {(p, c): (s, n) for p, c, s, n in rows}
    assert by_edge[("a.cpp", "b.h")] == (0.0, 0)


def test_missing_self_time_counts_as_zero_but_lines_still_counted():
    """trace 里没有该文件的 Source 区间（例如只做预处理）时不该崩，行数照算。"""
    rows = unit_edge_costs("a.cpp", [("a.cpp", "x.h")], {}, {"x.h": 42})
    assert rows == [("a.cpp", "x.h", 0.0, 42)]


def test_aggregate_sums_across_units_and_sorts_by_seconds():
    unit_a = [("p.h", "big.h", 1.0, 1000), ("p.h", "small.h", 0.1, 10)]
    unit_b = [("p.h", "big.h", 2.0, 2000)]
    costs = aggregate_edge_costs([unit_a, unit_b])
    assert [c.child for c in costs] == ["big.h", "small.h"]
    big = costs[0]
    assert big.marginal_seconds == 3.0
    assert big.marginal_lines == 3000
    assert big.tus == 2
    assert costs[1].tus == 1


def test_aggregate_of_nothing_is_empty():
    assert aggregate_edge_costs([]) == []


def test_potential_graph_kills_the_false_saving(tmp_path):
    """-H 只记录首次包含：删掉 a.h 里的 cmath，b.h 还会把 cmath 拉进来。

    这正是 doctest 上抓到的假阳性（预测 432ms、实测 ~0）：有效树看不到
    b.h 的 #include <cmath>（被 include guard 抑制），补全潜在供给边后
    闭包为空，预测归零。
    """
    cmath = tmp_path / "cmath"
    a_h = tmp_path / "a.h"
    b_h = tmp_path / "b.h"
    root_cpp = tmp_path / "root.cpp"
    cmath.write_text("// expensive\n", encoding="utf-8")
    a_h.write_text("#include <cmath>\n", encoding="utf-8")
    b_h.write_text("#include <cmath>\n", encoding="utf-8")
    root_cpp.write_text('#include "a.h"\n#include "b.h"\n', encoding="utf-8")

    # -H 的真实形态：cmath 只在第一次出现，挂在 a.h 下
    edges = [("root.cpp", "a.h"), ("root.cpp", "b.h"), ("a.h", "cmath")]
    files = {"root.cpp": root_cpp, "a.h": a_h, "b.h": b_h, "cmath": cmath}
    self_times = {"cmath": 1.0}
    lines = {"cmath": 500}

    optimistic = unit_edge_costs("root.cpp", edges, self_times, lines)
    assert ("a.h", "cmath") in {(p, c) for p, c, _s, _n in optimistic}

    honest = unit_edge_costs("root.cpp", edges, self_times, lines, files=files)
    assert ("a.h", "cmath") not in {(p, c) for p, c, _s, _n in honest}


def test_potential_graph_keeps_true_savings(tmp_path):
    """只有一条供给路径时，补全后闭包仍然非空——修 bug 不能把真收益也抹掉。"""
    lonely = tmp_path / "lonely.h"
    a_h = tmp_path / "a.h"
    root_cpp = tmp_path / "root.cpp"
    lonely.write_text("int f();\n", encoding="utf-8")
    a_h.write_text('#include "lonely.h"\n', encoding="utf-8")
    root_cpp.write_text('#include "a.h"\n', encoding="utf-8")

    edges = [("root.cpp", "a.h"), ("a.h", "lonely.h")]
    files = {"root.cpp": root_cpp, "a.h": a_h, "lonely.h": lonely}
    rows = unit_edge_costs("root.cpp", edges, {"lonely.h": 0.25}, {"lonely.h": 30}, files=files)
    by_edge = {(p, c): (s, n) for p, c, s, n in rows}
    assert by_edge[("a.h", "lonely.h")] == (0.25, 30)


def test_potential_graph_ignores_files_outside_the_tree(tmp_path):
    """树外的文件不参与：文本里出现的名字若不在本次解析集合中，不造边。"""
    a_h = tmp_path / "a.h"
    a_h.write_text("#include <unparsed.h>\n", encoding="utf-8")
    root_cpp = tmp_path / "root.cpp"
    root_cpp.write_text('#include "a.h"\n', encoding="utf-8")
    files = {"root.cpp": root_cpp, "a.h": a_h}
    rows = unit_edge_costs(
        "root.cpp", [("root.cpp", "a.h")], {"a.h": 0.1}, {"a.h": 5}, files=files
    )
    assert rows == [("root.cpp", "a.h", 0.1, 5)]
