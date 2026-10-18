# G2 重做：头文件级候选 + 交替测量（2026-10-18，zlib）

- 工程：zlib，15 个 TU。
- 候选池：可行性闸门开 0 条 / 关 16 条。
- 协议：clang.exe，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| ungated(header,top-by-size) | 5 | 1 | 0 | **0%** |

## ungated(header,top-by-size) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `zlib.h` | `zconf.h` | 15 | ambiguous | None | None | None% |
| `zutil.h` | `zlib.h` | 9 | compile-error | None | None | None% |
| `zconf.h` | `limits.h` | 15 | no-change | 5443.7 | 5424.3 | 0.356% |
| `deflate.h` | `zutil.h` | 2 | compile-error | None | None | None% |
| `gzguts.h` | `zlib.h` | 5 | compile-error | None | None | None% |

