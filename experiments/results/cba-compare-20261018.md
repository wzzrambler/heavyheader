# G1 同任务对照：ClangBuildAnalyzer vs HeavyHeader（2026-10-18）

## 协议

- 同一批 **25** 个编译单元，来自 `experiments/_work/json` 的 `compile_commands.json`（取前 N 条）。
- 同一个编译器：`clang++.exe` clang version 23.1.3 (https://github.com/llvm/llvm-project 0d261d1ca552c95a8f007e061c787ac7132fbcbc)
  （HeavyHeader 侧记录的编译器为 `C:\Program Files\LLVM\bin\clang++.exe`，两边一致）
- 同一组参数：`--target=x86_64-w64-windows-gnu --gcc-toolchain=C:/mingw64`（MinGW target，避免链到不可用的 MSVC 头）。
- 候选筛选规则两边**完全相同**：路径是否位于项目根下；系统头两边都不计入。
- CBA 的默认配置会破坏对照公平性，本轮显式改了 `ClangBuildAnalyzer.ini`：
  `onlyRootHeaders=false`（默认只列直接被 TU 包含的头，会排除传递包含的头）、
  `header=500`（默认只输出 10 行，去掉系统头后可能只剩三四行）。
  两处默认值都在源码 `src/Analysis.cpp` 的 `Config` 里核实过。
- 指标仍不同（这是工具定位差异，不是口径错误）：CBA 是编译时间，HeavyHeader 是归因行数。因此比的是「指向同一批热点头文件」与「顺序一致性」。

## 采集质量

- CBA 侧编译失败：**0** / 25
- HeavyHeader 侧失败单元：**0** / 25
- CBA 解析到头文件行数：195 行，其中项目内 35 行。

## Top-10 对照（仅项目内头文件）

| # | ClangBuildAnalyzer（按编译时间） | HeavyHeader（按归因行数） |
|---|---|---|
| 1 | json.hpp (10328 ms) | doctest.h (177,650 行) |
| 2 | adl_serializer.hpp (2717 ms) | json.hpp (131,450 行) |
| 3 | from_json.hpp (2589 ms) | binary_reader.hpp (75,225 行) |
| 4 | binary_reader.hpp (1208 ms) | hedley.hpp (51,125 行) |
| 5 | doctest_compatibility.h (822 ms) | binary_writer.hpp (45,950 行) |
| 6 | serializer.hpp (816 ms) | lexer.hpp (40,825 行) |
| 7 | input_adapters.hpp (808 ms) | to_chars.hpp (27,950 行) |
| 8 | exceptions.hpp (667 ms) | json_pointer.hpp (24,700 行) |
| 9 | type_traits.hpp (268 ms) | serializer.hpp (24,700 行) |
| 10 | doctest.h (265 ms) | type_traits.hpp (19,875 行) |

**集合重合 5/10**（Jaccard 0.333）：json.hpp, binary_reader.hpp, serializer.hpp, type_traits.hpp, doctest.h

**共同项顺序一致性 Kendall τ = 0.20**（+1 完全同序，0 无关，−1 完全反序）

## 参照：按 CBA 发布口径（含子头）比较

- 两边都报告的项目头文件：**35** 个，集合重合 5/10，
  Kendall τ = 0.20，Spearman ρ = 0.42。
- 这一组用的是 CBA 的**发布口径**：`Source` 区间嵌套，所以它的每头文件时间
  含子头（`json.hpp` 的 382 ms 区间里包着 `adl_serializer.hpp` 的 113 ms）。
  与我们的「自身行数」不同量，因此**不作为主结论**，只作参照。

## 主结果：还原成「自身耗时」后的同构比较

把 CBA 的原始 trace 按「区间长度 − 直接子区间」还原成自身耗时，
与 `自身行数 × 包含次数` 同构后再排名（推导在 `trace_self_times()`，可复核）。

| # | CBA 自身耗时 Top-10 | HeavyHeader 归因行数 Top-10 |
|---|---|---|
| 1 | json.hpp (1411.6 ms) | doctest.h |
| 2 | binary_reader.hpp (278.8 ms) | json.hpp |
| 3 | doctest.h (217.4 ms) | binary_reader.hpp |
| 4 | from_json.hpp (160.3 ms) | hedley.hpp |
| 5 | type_traits.hpp (157.6 ms) | binary_writer.hpp |
| 6 | serializer.hpp (152.3 ms) | lexer.hpp |
| 7 | exceptions.hpp (112.2 ms) | to_chars.hpp |
| 8 | binary_writer.hpp (101.9 ms) | json_pointer.hpp |
| 9 | to_chars.hpp (82.0 ms) | serializer.hpp |
| 10 | input_adapters.hpp (79.4 ms) | type_traits.hpp |

- 集合重合 **7/10**：json.hpp, binary_reader.hpp, doctest.h, type_traits.hpp, serializer.hpp, binary_writer.hpp, to_chars.hpp
- 共同项顺序一致性 Kendall τ = **0.33**
- 全部 35 个共同项目头的秩相关 Spearman ρ = **0.68**

## 为什么行数排不准：每千行解析成本差两个数量级

| 头文件 | 自身耗时 | 包含次数 | 归因行数 | 每千行耗时 | CBA 名次 | HH 名次 |
|---|---:|---:|---:|---:|---:|---:|
| `json.hpp` | 1411.6 ms | 25 | 131450 | 10.7 | 1 | 2 |
| `binary_reader.hpp` | 278.8 ms | 25 | 75225 | 3.7 | 2 | 3 |
| `doctest.h` | 217.4 ms | 25 | 177650 | 1.2 | 3 | 1 |
| `from_json.hpp` | 160.3 ms | 25 | 12425 | 12.9 | 4 | 14 |
| `type_traits.hpp` | 157.6 ms | 25 | 19875 | 7.9 | 5 | 10 |
| `serializer.hpp` | 152.3 ms | 25 | 24700 | 6.2 | 6 | 9 |
| `exceptions.hpp` | 112.2 ms | 25 | 6425 | 17.5 | 7 | 19 |
| `binary_writer.hpp` | 101.9 ms | 25 | 45950 | 2.2 | 8 | 5 |
| `to_chars.hpp` | 82.0 ms | 25 | 27950 | 2.9 | 9 | 7 |
| `input_adapters.hpp` | 79.4 ms | 25 | 12300 | 6.5 | 10 | 15 |
| `to_json.hpp` | 72.6 ms | 25 | 11150 | 6.5 | 11 | 17 |
| `macro_scope.hpp` | 60.2 ms | 25 | 12050 | 5.0 | 12 | 16 |

在归因行数 ≥ 5,000 的 20 个头上，最便宜的 `hedley.hpp` 每千行约 0.3 ms，最贵的 `exceptions.hpp` 约 17.5 ms，相差 **60×**。（更小的头归一化后极不稳定，故设此门槛。）因此行数只能做候选筛选，**不能当成本结论**；成本结论必须来自实测。

## CBA Top-10 里被排除的系统头

- `iterator`（1418 ms，包含 25 次）
- `streambuf_iterator.h`（1381 ms，包含 25 次）
- `streambuf`（1348 ms，包含 25 次）
- `algorithm`（1136 ms，包含 25 次）
- `ios_base.h`（1133 ms，包含 25 次）
- `valarray`（930 ms，包含 25 次）

## 判读边界

- 时间序与行数序本来就不必相同：模板实例化重的头可能行数不多但耗时极高；
  反之一个巨大的纯声明头行数多而解析便宜。两者不一致**不构成**任一方错误。
- 本对照能支持的结论只有一句：在同一任务（找出最该治理的头文件）上，
  两个工具给出的前 10 名是否指向同一批文件、顺序是否一致。
- 它**不能**支持「谁更准」或「谁更快」——那需要以实际删改后的构建变化为准。
