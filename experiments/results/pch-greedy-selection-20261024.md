# PCH 选择的两个缺陷与修正：按净收益贪心 + 集合层面的维护税（2026-10-24）

## 0. 为什么有这一轮

上一轮给 PCH 建议补了「维护税」（`rebuild_header` / `rebuild_units` /
`rebuild_seconds`），本轮原本想把税纳入候选头的排序。做之前先在真实扫描数据上
验证税能不能算出来，结果发现上一轮的实现和新设计各有一个实质缺陷。

两个缺陷都是**用真实数据量出来的**，不是读代码看出来的。

## 1. 缺陷 A：按候选头自身算税恒等于 0

第一版设计给每个候选头算一个税，排序键改成
`reach(h) - (reach(h) - min(reach(c) for c in closure(h)))`。
在 `pch-estimate-json25-20261021.json` 上实测：**54 对（头, 闭包头），
违反「闭包头触达 ≥ 候选头触达」的有 0 对**，即税恒为 0。

原因是数据结构保证的，不是巧合：`HeaderStats.tu_reach` 的定义是
「直接或间接包含它的 TU 数」（`models.py` 原文）。候选头 h 被 k 个 TU 传递
包含，则 h 闭包里的每个头 c 也被这 k 个 TU 传递包含，于是 `reach(c) >= reach(h)`，
税必然 ≤ 0。

**结论**：税不是单个候选头的属性。一个永远不可能非零的排序项比没有更糟
（它让人以为已经考虑了税），所以删除，并在代码注释里写明原因。

## 2. 缺陷 B：top-k 切片在部分覆盖的项目上选错

用一个 10 单元的合成语料实测（`experiments/_work/fragile-demo`）：
10 个单元全都包含 `common.h`（1,503 行），其中 5 个额外包含
`special.h`（1,503 行）。

| 选法 | 闭包行数 | eligible | 净节省行数 | 覆盖率 |
|---|---:|---:|---:|---:|
| 只选 `common.h` | 1,503 | 10 | **13,527** | 100% |
| 选 `common.h` + `special.h`（旧行为） | 3,006 | 5 | 12,024 | 50% |

旧实现按 `(reach, 闭包行数)` 取前 `top_k` 个，把 `special.h` 也放进去：
闭包翻倍、eligible 腰斩，**净节省反而下降 1,503 行**，覆盖率从 100% 掉到 50%，
同时还引入一个 2.0 倍的维护税（见下）。

## 3. 修正一：按净收益增量贪心

`estimate_pch` 不再取排序后的前 `top_k` 个，而是贪心：每次加入能让
`净节省行数` 增加最多的候选头，直到没有正增益或达到 `top_k`。

原来的 `(reach, 闭包行数)` 排序降级为**遍历顺序与平局判定**，目标函数是
净节省本身。这样做的好处是维护税**自动**进入目标函数——把只服务部分单元的
头拉进闭包，正是会让 eligible 下降、从而让净增益消失的那一类。

两个语料上的实测对比：

| 语料 | 旧（top-k） | 新（贪心） |
|---|---|---|
| json 63 TU | 5 个头，净收益 9,919,690 行（91.2%） | 2 个头，净收益 10,362,556 行（**95.3%**） |
| fragile-demo 10 TU | 2 个头，12,024 行，覆盖率 50% | 1 个头，13,527 行，覆盖率 **100%** |

json 上选中 `json.hpp` 与 `doctest_compatibility.h`（后者把 doctest 系列拉进
闭包，eligible 仍为 63，净收益因此提高）。候选头从 5 个减到 2 个也意味着
PCH 更小、对 include 顺序的约束更少。

## 4. 修正二：税改成集合层面，并且真的能量出来

税的正确度量是「`.gch` 失效的影响面」与「无 PCH 时改同一个头的影响面」之比：

- `.gch` 失效后，**每一个使用 PCH 的单元**都要重新生成 .gch 再编译自己，
  与是哪个头触发失效无关 → 影响面 = 包含至少一个候选头的单元数。
- 无 PCH 时，改一个头只影响它自己触达的单元。

据此新增 `fragile_headers`：闭包里触达**小于**使用 PCH 的单元数的项目头，
按放大倍数降序，报 `(头, 它自己的触达数, 放大倍数)`。这些头只被一部分单元
使用，却因为被固化进 .gch 而让全体使用 PCH 的单元陪着重编。

`rebuild_units` 的口径同步修正：此前按「被改头自己触达的单元」计，在部分
覆盖的项目上会把税说小一个量级；现在按所有使用 PCH 的单元计。

两个语料上的结果：

- **json 63 TU**：清单为空。所有候选头都是 63/63 全触达，没有「只服务部分
  单元」的头。这是该语料的真实形状，不是没算出来。
- **fragile-demo**：贪心拒绝 `special.h` 之后清单也为空——它不再进闭包。
  该场景由 `tests/test_pch.py` 的构造覆盖（净收益口径下贪心仍可能选中会
  制造脆弱头的组合，此时清单必须列出来）。

## 5. 复现

```bash
# json 63 TU
python -c "import sys; sys.path.insert(0,'experiments'); from build import write_compile_commands; \
from pathlib import Path; r=Path('experiments/_work/json').resolve(); \
write_compile_commands(r, r/'build'/'compile_commands.json')"
heavyheader scan <json-abs> --compile-commands <json-abs>/build/compile_commands.json \
  --pch --pch-top 5 --jobs 8 --output pch.md

# fragile-demo 10 TU（部分覆盖）
heavyheader scan experiments/_work/fragile-demo --pch --pch-top 5 --jobs 8 --output fragile.md
```

注意：`write_compile_commands` 必须传**绝对路径**，否则相对路径与 cwd 叠加、
所有单元编译失败（63/63 全红）。

## 6. 局限

1. 净收益用**行数**代理，不是秒数。模板重型头文件会被低估，因此贪心的目标
   函数在模板重型项目上可能选错。
2. 只比较「加入 / 不加入」，是贪心不是全局最优；候选头之间的闭包高度重叠时
   结果依赖遍历顺序（已用 `(reach, 闭包行数)` 做确定性排序压制）。
3. `fragile_headers` 只报风险，不自动重选集合。规避办法写在报告里
   （让 PCH 只含全工程公共依赖的头），是否采纳由使用者决定。
4. 单机、单编译器（g++ 16.2.0 MinGW-w64）。
