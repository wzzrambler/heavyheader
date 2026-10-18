# 闸门漏掉了吗：闸门关时能编过的候选实测（2026-10-18，json）

- 工程：json，25 个 TU。
- 候选池：闸门放行 0 条 / 闸门关 70 条。
- 闸门关时能编过 9 条，其中 **9 条不在闸门放行集合里**（闸门放行集合里有 0 条能编过）。
- 初筛协议：单遍测量（`runs=1`），阈值 2.0%；过阈值者按正式协议复测（`runs=3` + 复测 2 遍）。

## 闸门没放行、但能编过的候选（单遍初筛）

| 改动位置 | include | 受影响 TU | 单遍读数 | 基线 ms | 移除后 ms | 结论 |
|---|---|---:|---:|---:|---:|---|
| `serializer.hpp` | `iomanip` | 25 | 1.246% | 27604.4 | 27260.4 | improved |
| `json.hpp` | `binary_reader.hpp` | 25 | 0.593% | 24877.0 | 24729.3 | no-change |
| `json.hpp` | `memory` | 25 | -0.096% | 24887.7 | 24911.5 | no-change |
| `doctest_compatibility.h` | `iosfwd` | 25 | -0.198% | 27158.8 | 27212.4 | no-change |
| `binary_reader.hpp` | `input_adapters.hpp` | 25 | -0.256% | 24854.1 | 24917.7 | no-change |
| `json.hpp` | `vector` | 25 | -0.266% | 27281.3 | 27353.8 | no-change |
| `from_json.hpp` | `map` | 25 | -0.316% | 24994.5 | 25073.4 | no-change |
| `json.hpp` | `algorithm` | 25 | -0.465% | 27230.9 | 27357.4 | no-change |
| `json.hpp` | `functional` | 25 | -0.489% | 27470.7 | 27605.0 | no-change |

## 正式复测结果

没有候选的单遍读数达到阈值，因此没有进入复测。

> 单遍读数**不能**作为结论引用：聚合 A/A 实测显示单遍噪声可达 2.005%。
> 只有通过复测的条目才算 improved。

