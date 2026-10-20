# HeavyHeader 构建成本报告

- 项目根目录：`C:\Users\31879\WorkBuddy\2026-10-18-23-59-50\heavyheader\benchmarks\cases\demo`
- 编译器：`g++`（gcc）
- 语言标准：`c++17`
- 编译单元：2 成功 / 0 失败
- 头文件依赖边：425
- 全项目头文件解析量：**206,106 行**
- 唯一文件行数：140,093 行
- **Blowup Factor：1.5×**（平均每行被解析的次数；与 Header Hero 同定义，见 `docs/competitors.md`）
- 累计编译耗时：3.66 s

## 成本热点 Top 15

按「归因行数」排序，即 `头文件行数 × 引用它的编译单元数`——
它衡量的是这个头文件让整个项目多解析了多少行代码。

| 头文件 | 行数 | 被引用 TU | 归因行数 | 传递闭包 | 最大深度 |
|---|---:|---:|---:|---:|---:|
| `include\core.h` | 6 | 2 | 12 | 115 | 0 |
| `include\heavy.h` | 12 | 1 | 12 | 94 | 0 |
| `include\bloated.h` | 8 | 1 | 8 | 37 | 1 |
| `include\unguarded.h` | 2 | 2 | 4 | 0 | 1 |
| `include\mid.h` | 4 | 1 | 4 | 1 | 0 |
| `include\lonely.h` | 4 | 1 | 4 | 38 | 0 |

## 实验性：增量构建税（未校准，勿据此决策）

上一节回答「整个项目为它付了多少解析量」；这一节试答「**你改它一行，下一次增量构建要等多久**」。这是编辑循环里的实际痛感，也是现有竞品没有给出的视角——但它目前**只是原型**：受控实验（`experiments/incremental_tax.py`）显示预测比实测**偏高约 1.7×**，因为扫描的 `--full-compile` 会为每个单元额外链接一次，而真实增量构建只编译不链接。校准完成前，本表不得作为结论引用。

| 头文件 | 重编 TU | 预计等待 | 占单次全量构建 |
|---|---:|---:|---:|
| `include\core.h` | 2 | 3.66 s | 100% |
| `include\unguarded.h` | 2 | 3.66 s | 100% |
| `include\heavy.h` | 1 | 2.37 s | 65% |
| `include\bloated.h` | 1 | 2.37 s | 65% |
| `include\mid.h` | 1 | 2.37 s | 65% |
| `include\lonely.h` | 1 | 2.37 s | 65% |

> 口径：扫描期每个 TU 实测耗时之和（默认 `-fsyntax-only`，不含链接）。它是一个**预测**，预测误差与验证方法见 `docs/methodology.md` 的「增量构建税」一节。`0` 表示该单元没有可用计时，不是「瞬间完成」。

## 成本候选（按预计收益排序）

> **这张表说的是「贵」，不是「能删」。** 「独占闭包」指删掉这条 include 后，只有它会停止被解析的头文件集合——如果某个头文件还能从别的 include 到达，删掉当前这条并不省钱。这是本表与普通 lint 工具的区别。
>
> 「结论」列是明确判断（建议删除/保留/需人工判断），依据见下一节；「未实测」表示这一轮没有跑 `--probe`，此时不给结论——没测过就下判断是编造。

| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 判据 | 结论 |
|---|---|---:|---:|---:|---|---|---|
| `app\util.cpp` | `include\core.h` | 116 | 66,020 | 100.0% | 正在使用 | 副作用：template<typename _Tp>     concept __unsigned_integer = __is_unsigned_ | 未实测 |
| `app\main.cpp` | `include\lonely.h` | 36 | 22,316 | 15.9% | 正在使用 | 副作用：template<typename _Bi_iter, typename _Ch_traits, typename _Ch_alloc>   | 未实测 |
| `app\main.cpp` | `include\core.h` | 1 | 6 | 0.0% | 正在使用 | Core 由 core.h 提供 | 未实测 |
| `app\main.cpp` | `include\mid.h` | 1 | 4 | 0.0% | 正在使用 | mid 由 mid.h 提供 | 未实测 |
| `app\util.cpp` | `include\unguarded.h` | 1 | 2 | 0.0% | 正在使用 | unguarded 由 unguarded.h 提供 | 未实测 |


### 待验证队列：5 条等 `verify` 裁定

顺序即 `heavyheader verify --top N` 的测量顺序：**疑似未使用**先测
（静态判定已经指向删除），**无法判定**次之（静态给不出结论，但反事实
实验能裁——Lua 语料上这一层 32/32 条实测可删），**在用**默认不测
（要测得加 `--ungated-sites`）。

- 疑似未使用 **0** 条 · 无法判定 **0** 条 · 在用 **5** 条

| 编译单元 | include | 预计省 (s) | 使用状态 | 判据 |
|---|---|---:|---|---|
| `app\util.cpp` | `include\core.h` | 0.4300 | 正在使用 | 副作用：template<typename _Tp>     concept __unsigned_integer = __is_unsigned_ |
| `app\main.cpp` | `include\lonely.h` | 0.2230 | 正在使用 | 副作用：template<typename _Bi_iter, typename _Ch_traits, typename _Ch_alloc>   |
| `app\main.cpp` | `include\core.h` | 0.0001 | 正在使用 | Core 由 core.h 提供 |
| `app\main.cpp` | `include\mid.h` | 0.0000 | 正在使用 | mid 由 mid.h 提供 |
| `app\util.cpp` | `include\unguarded.h` | 0.0000 | 正在使用 | unguarded 由 unguarded.h 提供 |

## 实测状态

**本轮没有做实测。** 上表全部是估算——按行数折算的预计收益，不是秒数，也不代表这些 include 删掉之后还能编译。

两个档位可选：`--probe-compile` 只验「删了还能不能编」（每候选一次编译）；`--probe` 在它之上再测耗时（两侧各 N 次取最小，判 improved 后还要复测）。

## 缺少 include guard 的头文件（必赚项）

这些头文件在**同一个编译单元内被重复解析**。对带 guard 的头文件来说这在物理上不可能发生，所以这是「缺少 guard」的确定性信号。补上 `#pragma once` 或 guard 宏永远安全，且收益立竿见影。

| 头文件 | 解析次数 | 涉及 TU | 多解析次数 |
|---|---:|---:|---:|
| `bits\version.h` | 49 | 2 | 47 |
| `bits\stdexcept_throw.h` | 11 | 2 | 9 |
| `c++\stdlib.h` | 5 | 2 | 3 |
| `include\stddef.h` | 5 | 2 | 3 |
| `include\stddef.h` | 5 | 2 | 3 |
| `include\limits.h` | 4 | 2 | 2 |
| `include\_mingw.h` | 4 | 2 | 2 |
| `include\corecrt_wconio.h` | 4 | 2 | 2 |
| `include\stdio.h` | 4 | 2 | 2 |
| `include\stdlib.h` | 4 | 2 | 2 |
| `include\wchar.h` | 4 | 2 | 2 |
| `include\unguarded.h` | 3 | 2 | 1 |
| `sys\timeb.h` | 2 | 1 | 1 |

## 模板实例化成本

全项目模板实例化合计 **330 ms**，占总构建时间 **18.0%**。

> 每个编译单元的数字来自 `-ftime-report`，是精确的；下面「模板表面」一列是**文本启发式**（`template<` 声明数 × 引用它的 TU 数），用于提示成本可能来自哪里，不是实测归因。

## 扫描警告

- C:\Users\31879\WorkBuddy\2026-10-18-23-59-50\heavyheader\benchmarks\cases\demo\include\unguarded.h: parsed 3x across 2 TU(s) -- missing include guard?

---

由 [HeavyHeader](https://github.com/wzzrambler/heavyheader) 生成 · 指标定义见 `docs/methodology.md`
