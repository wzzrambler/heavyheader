# G1 同任务对照：ClangBuildAnalyzer vs HeavyHeader（2026-10-18）

## 协议

- 同一批 **24** 个编译单元，来自 `experiments/_work/json` 的 `compile_commands.json`（取前 N 条）。
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

- CBA 侧编译失败：**0** / 24
- HeavyHeader 侧失败单元：**0** / 24
- CBA 解析到头文件行数：264 行，其中项目内 22 行。

## Top-10 对照（仅项目内头文件）

| # | ClangBuildAnalyzer（按编译时间） | HeavyHeader（按归因行数） |
|---|---|---|
| 1 | prelude.h (17761 ms) | macros.h (24,480 行) |
| 2 | doctest_fwd.h (713 ms) | string.h (10,896 行) |
| 3 | config.h (103 ms) | config.h (4,944 行) |
| 4 | context_state.h (99 ms) | warnings.h (4,632 行) |
| 5 | string.h (49 ms) | expression.h (4,200 行) |
| 6 | traversal.h (37 ms) | prelude.h (3,408 行) |
| 7 | expression.h (22 ms) | reporter.h (3,144 行) |
| 8 | reporter.h (16 ms) | result.h (2,832 行) |
| 9 | result.h (15 ms) | type_traits.h (2,616 行) |
| 10 | test_case.h (13 ms) | type.h (2,544 行) |

**集合重合 6/10**（Jaccard 0.429）：prelude.h, config.h, string.h, expression.h, reporter.h, result.h

**共同项顺序一致性 Kendall τ = 0.47**（+1 完全同序，0 无关，−1 完全反序）

## 参照：按 CBA 发布口径（含子头）比较

- 两边都报告的项目头文件：**20** 个，集合重合 6/10，
  Kendall τ = 0.47，Spearman ρ = 0.48。
- 这一组用的是 CBA 的**发布口径**：`Source` 区间嵌套，所以它的每头文件时间
  含子头（`json.hpp` 的 382 ms 区间里包着 `adl_serializer.hpp` 的 113 ms）。
  与我们的「自身行数」不同量，因此**不作为主结论**，只作参照。

## 主结果：还原成「自身耗时」后的同构比较

把 CBA 的原始 trace 按「区间长度 − 直接子区间」还原成自身耗时，
与 `自身行数 × 包含次数` 同构后再排名（推导在 `trace_self_times()`，可复核）。

| # | CBA 自身耗时 Top-10 | HeavyHeader 归因行数 Top-10 |
|---|---|---|
| 1 | doctest_fwd.h (493.0 ms) | macros.h |
| 2 | prelude.h (288.5 ms) | string.h |
| 3 | string.h (77.7 ms) | config.h |
| 4 | context_state.h (56.4 ms) | warnings.h |
| 5 | traversal.h (37.4 ms) | expression.h |
| 6 | expression.h (22.9 ms) | prelude.h |
| 7 | config.h (20.1 ms) | reporter.h |
| 8 | result.h (15.9 ms) | result.h |
| 9 | test_case.h (14.0 ms) | type_traits.h |
| 10 | handler.h (7.2 ms) | type.h |

- 集合重合 **5/10**：prelude.h, string.h, expression.h, config.h, result.h
- 共同项顺序一致性 Kendall τ = **0.20**
- 全部 19 个共同项目头的秩相关 Spearman ρ = **0.57**

## 为什么行数排不准：每千行解析成本差两个数量级

| 头文件 | 自身耗时 | 包含次数 | 归因行数 | 每千行耗时 | CBA 名次 | HH 名次 |
|---|---:|---:|---:|---:|---:|---:|
| `doctest_fwd.h` | 493.0 ms | 24 | 1992 | 247.5 | 1 | 14 |
| `prelude.h` | 288.5 ms | 24 | 3408 | 84.7 | 2 | 6 |
| `string.h` | 77.7 ms | 48 | 10896 | 7.1 | 3 | 2 |
| `context_state.h` | 56.4 ms | 13 | 702 | 80.3 | 4 | 35 |
| `traversal.h` | 37.4 ms | 13 | 702 | 53.3 | 5 | 36 |
| `expression.h` | 22.9 ms | 24 | 4200 | 5.5 | 6 | 5 |
| `config.h` | 20.1 ms | 24 | 4944 | 4.1 | 7 | 3 |
| `result.h` | 15.9 ms | 24 | 2832 | 5.6 | 8 | 8 |
| `test_case.h` | 14.0 ms | 24 | 23 | 608.7 | 9 | 55 |
| `handler.h` | 7.2 ms | 13 | 112 | 64.3 | 10 | 45 |
| `atomic.h` | 6.2 ms | 11 | 1560 | 4.0 | 11 | 21 |
| `data.h` | 5.9 ms | 11 | 1728 | 3.4 | 12 | 17 |

密度样本不足，无法给出换算率区间。

## CBA Top-10 里被排除的系统头

- `windows.h`（7581 ms，包含 24 次）
- `windef.h`（5560 ms，包含 24 次）
- `minwindef.h`（5321 ms，包含 24 次）
- `winnt.h`（5254 ms，包含 24 次）
- `x86intrin.h`（4699 ms，包含 24 次）
- `immintrin.h`（4464 ms，包含 24 次）
- `fstream`（3277 ms，包含 24 次）
- `istream`（2951 ms，包含 24 次）
- `ios`（2628 ms，包含 24 次）

## 判读边界

- 时间序与行数序本来就不必相同：模板实例化重的头可能行数不多但耗时极高；
  反之一个巨大的纯声明头行数多而解析便宜。两者不一致**不构成**任一方错误。
- 本对照能支持的结论只有一句：在同一任务（找出最该治理的头文件）上，
  两个工具给出的前 10 名是否指向同一批文件、顺序是否一致。
- 它**不能**支持「谁更准」或「谁更快」——那需要以实际删改后的构建变化为准。
