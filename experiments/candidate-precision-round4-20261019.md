# 候选识别精度 · 第四轮：空头承诺清零（2026-10-19）

## 动机

第三轮收在「零假阳性」之后，剩下的短板是排序质量：Lua 第 2 名是一条空头承诺
（`lcode.c -> limits.h`，预估省一大笔、实测少解析 **0** 个文件）。空头承诺浪费
P@5 的名额，还会误导用户去改一条没有收益的 include 行。

## 根因：mention 补边解析错了文件

手工复现（clang `-H`）确认：删掉 lcode.c 的 `#include <limits.h>` 后，
limits.h **仍然被解析**——新路径是

```
lcode.c → <stdlib.h> → UCRT stdlib.h → (guard 抑制，不在 -H 树里) → clang limits.h
```

这条边只能靠 `IncludeMentions` 从文本补回来。但补边解析栽在两个细节上：

1. **相对路径启发用错了地方**。`_resolve_mention` 对所有 mention 先试
   「includer 同目录有没有同名文件」。UCRT 目录里恰好也有一个 limits.h——
   于是边被补到 UCRT 的 limits.h 上。但 **尖括号包含根本不搜索 includer 所在
   目录**，编译器实际解析到 clang 内建目录的包装头（它的搜索顺序在前）。
   补到错的文件上，等于没补。
2. **同名候选无遮蔽判定**。clang 的包装 `limits.h` 与 UCRT 的实现 `limits.h`
   成对出现，后者在 `-H` 树里只作为前者的孩子存在。这个父子关系就是现成的
   答案：激活点是另一个同名候选给的 → 它是遮蔽面，不是解析结果。

## 修法（`attrib/graph.py`）

* `IncludeMentions.of` 记录 include 的**形态**（`<>` vs `""`）；
* **引号**包含保留相对解析（这是 C 标准语义）；**尖括号**包含跳过相对解析；
* 新增 `_shadowed_same_name`：从 `-H` 树里找出「被另一个同名候选激活」的文件，
  解析 mention 时先剔除；剔除后唯一即命中，仍多义则宁可不补（保守方向不变）。

实现过程中还抓到一个自伤 bug：改用 `INCLUDE_RE` 后把 `Pattern.finditer(text,
re.MULTILINE)` 当成了带 flags 的调用——`Pattern.finditer` 的第二个参数是
**搜索起始偏移**，不是 flags，等于把全文件的 mention 只剩第一行。回归测试
（guard 补边）当场拦住。

## 结果（`*-20261019i.json`，基线 = 第三轮 `h`）

| | doctest | zlib | Lua |
|---|---:|---:|---:|
| 空头承诺 | 1 | 0 | **1 → 0** |
| 候选数 | 38 | 40 | 233 → **222** |
| plausibly_unused 正确率 | 3/3 | 1/1 | 19/20（95%） |
| P@5 | 60% | 20% | 80% → **100%** |
| 首条有用 | 第 1 | 第 1 | 第 1 |

Lua 的 11 条被错误判成独占的系统头候选（stdlib.h/limits.h/float.h 的包装对）
全部消失——它们本来就一个文件都省不下。doctest 与 zlib 数字与第三轮持平：
它们的前几名已被真依赖占满，排序在该语料上结构性到顶。

## 复现

```bash
python experiments/candidate_precision.py \
  --project <lua-5.4.7> --db <compile_commands.json> \
  --compiler <clang> --tus 0 --jobs 8 --check-codegen \
  --out experiments/results/candidate-precision-lua-20261019i.json
```

数据：`experiments/results/candidate-precision-{doctest,zlib,lua}-20261019i.json`
（本轮）、`…-20261019h.json`（第三轮）。
