# 闸门漏掉了吗：闸门关时能编过的候选实测（2026-10-18，doctest）

- 工程：doctest，24 个 TU。
- 候选池：闸门放行 16 条 / 闸门关 66 条。
- 闸门关时能编过 31 条，其中 **23 条不在闸门放行集合里**（闸门放行集合里有 8 条能编过）。
- 初筛协议：单遍测量（`runs=1`），阈值 2.0%；过阈值者按正式协议复测（`runs=3` + 复测 2 遍）。

## 闸门没放行、但能编过的候选（单遍初筛）

| 改动位置 | include | 受影响 TU | 单遍读数 | 基线 ms | 移除后 ms | 结论 |
|---|---|---:|---:|---:|---:|---|
| `debug_output_window.h` | `context_state.h` | 2 | 3.738% | 1835.1 | 1766.6 | improved |
| `doctest_fwd.h` | `contains.h` | 24 | 0.741% | 20834.8 | 20680.3 | no-change |
| `doctest_fwd.h` | `result.h` | 24 | 0.717% | 20751.3 | 20602.4 | no-change |
| `doctest_fwd.h` | `compiler.h` | 24 | 0.713% | 20785.1 | 20637.0 | no-change |
| `prelude.h` | `climits` | 24 | 0.488% | 20776.7 | 20675.3 | no-change |
| `doctest_fwd.h` | `test_case.h` | 24 | 0.446% | 20787.0 | 20694.3 | no-change |
| `prelude.h` | `cstdio` | 24 | 0.379% | 20727.8 | 20649.3 | no-change |
| `doctest_fwd.h` | `subcase.h` | 24 | 0.342% | 20770.1 | 20699.0 | no-change |
| `doctest_fwd.h` | `context_scope.h` | 24 | 0.240% | 20862.0 | 20811.9 | no-change |
| `prelude.h` | `ctime` | 24 | 0.211% | 20642.3 | 20598.8 | no-change |
| `doctest_fwd.h` | `type.h` | 24 | 0.173% | 20633.3 | 20597.7 | no-change |
| `config.h` | `version` | 24 | 0.119% | 20784.6 | 20759.9 | no-change |
| `doctest_fwd.h` | `message.h` | 24 | -0.032% | 20691.5 | 20698.2 | no-change |
| `doctest_fwd.h` | `utility.h` | 24 | -0.090% | 20722.3 | 20740.9 | no-change |
| `doctest_fwd.h` | `generator.h` | 24 | -0.128% | 20674.3 | 20700.8 | no-change |
| `config.h` | `platform.h` | 24 | -0.234% | 20659.8 | 20708.2 | no-change |
| `doctest_fwd.h` | `comparator.h` | 24 | -0.247% | 20644.7 | 20695.6 | no-change |
| `doctest_fwd.h` | `test_suite.h` | 24 | -0.335% | 20617.4 | 20686.5 | no-change |
| `doctest_fwd.h` | `utility.h` | 24 | -0.430% | 20664.0 | 20752.9 | no-change |
| `doctest_fwd.h` | `type_traits.h` | 24 | -0.463% | 20611.1 | 20706.6 | no-change |
| `doctest_fwd.h` | `data.h` | 24 | -0.712% | 20572.1 | 20718.7 | no-change |
| `doctest_fwd.h` | `context.h` | 24 | -0.803% | 20644.2 | 20810.0 | no-change |
| `doctest_fwd.h` | `string.h` | 24 | -0.827% | 20524.9 | 20694.6 | no-change |

## 正式复测结果

| 改动位置 | include | 结论 | 读数 |
|---|---|---|---:|
| `debug_output_window.h` | `context_state.h` | no-change | -0.548% |

> 单遍读数**不能**作为结论引用：聚合 A/A 实测显示单遍噪声可达 2.005%。
> 只有通过复测的条目才算 improved。

