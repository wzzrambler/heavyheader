# G2 重做：头文件级候选 + 交替测量（2026-10-18，json）

- 工程：json，25 个 TU。
- 候选池：可行性闸门开 0 条 / 关 70 条。
- 协议：clang，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| ungated(header,top-by-size) | 5 | 2 | 0 | **0%** |

## ungated(header,top-by-size) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `adl_serializer.hpp` | `from_json.hpp` | 25 | compile-error | None | None | None% |
| `binary_reader.hpp` | `input_adapters.hpp` | 25 | no-change | 27383.1 | 27328.1 | 0.201% |
| `input_adapters.hpp` | `istream` | 25 | compile-error | None | None | None% |
| `json.hpp` | `adl_serializer.hpp` | 25 | compile-error | None | None | None% |
| `from_json.hpp` | `map` | 25 | no-change | 27200.0 | 27187.0 | 0.048% |

