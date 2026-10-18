# G2 重做：头文件级候选 + 交替测量（2026-10-18，doctest）

- 工程：doctest，24 个 TU。
- 候选池：可行性闸门开 16 条 / 关 66 条。
- 协议：clang，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| feasible(header,gated) | 5 | 1 | 1 | **20%** |
| ungated(header,top-by-size) | 5 | 0 | 0 | **0%** |

## feasible(header,gated) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `prelude.h` | `algorithm` | 24 | compile-error | None | None | None% |
| `prelude.h` | `unordered_set` | 24 | improved | 20555.9 | 19937.1 | 3.01% |
| `prelude.h` | `vector` | 24 | compile-error | None | None | None% |
| `prelude.h` | `set` | 24 | compile-error | None | None | None% |
| `prelude.h` | `iomanip` | 24 | compile-error | None | None | None% |

## ungated(header,top-by-size) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `prelude.h` | `algorithm` | 24 | compile-error | None | None | None% |
| `prelude.h` | `mutex` | 24 | compile-error | None | None | None% |
| `prelude.h` | `windows.h` | 24 | compile-error | None | None | None% |
| `prelude.h` | `fstream` | 24 | compile-error | None | None | None% |
| `prelude.h` | `cmath` | 24 | compile-error | None | None | None% |

