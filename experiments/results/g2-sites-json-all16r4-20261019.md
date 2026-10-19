# G2 重做：头文件级候选 + 交替测量（2026-10-19，json）

- 工程：json，25 个 TU。
- 候选池：可行性闸门开 0 条 / 关 58 条。
- 协议：clang++.exe，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| ungated(header,top-by-size) | 16 | 8 | 0 | **0%** |

## ungated(header,top-by-size) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `adl_serializer.hpp` | `from_json.hpp` | 25 | compile-error | None | None | None% |
| `json.hpp` | `adl_serializer.hpp` | 25 | compile-error | None | None | None% |
| `from_json.hpp` | `valarray` | 25 | no-change | 23036.3 | 22993.8 | 0.184% |
| `json.hpp` | `functional` | 25 | no-change | 23050.1 | 23066.2 | -0.07% |
| `std_fs.hpp` | `filesystem` | 25 | compile-error | None | None | None% |
| `from_json.hpp` | `std_fs.hpp` | 25 | compile-error | None | None | None% |
| `json.hpp` | `memory` | 25 | no-change | 22964.0 | 22991.5 | -0.119% |
| `from_json.hpp` | `exceptions.hpp` | 25 | compile-error | None | None | None% |
| `value_t.hpp` | `macro_scope.hpp` | 25 | compile-error | None | None | None% |
| `json.hpp` | `binary_reader.hpp` | 25 | no-change | 22922.9 | 22939.4 | -0.072% |
| `from_json.hpp` | `map` | 25 | no-change | 22862.5 | 22871.8 | -0.041% |
| `exceptions.hpp` | `value_t.hpp` | 25 | compile-error | None | None | None% |
| `binary_reader.hpp` | `input_adapters.hpp` | 25 | no-change | 23029.3 | 23052.3 | -0.1% |
| `input_adapters.hpp` | `numeric` | 25 | no-change | 22951.9 | 22959.1 | -0.032% |
| `string_concat.hpp` | `cstring` | 25 | compile-error | None | None | None% |
| `macro_scope.hpp` | `version` | 25 | no-change | 22975.1 | 22974.5 | 0.003% |

