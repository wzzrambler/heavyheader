# G2 重做：头文件级候选 + 交替测量（2026-10-19，doctest-master）

- 工程：doctest-master，24 个 TU。
- 候选池：可行性闸门开 12 条 / 关 65 条。
- 协议：clang++.exe，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| feasible(header,gated) | 12 | 7 | 0 | **0%** |

## feasible(header,gated) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `prelude.h` | `utility` | 24 | no-change | 19194.1 | 19169.2 | 0.13% |
| `prelude.h` | `iomanip` | 24 | compile-error | None | None | None% |
| `prelude.h` | `atomic` | 24 | compile-error | None | None | None% |
| `prelude.h` | `map` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `config.h` | 24 | no-change | 19124.8 | 19165.1 | -0.21% |
| `prelude.h` | `limits` | 24 | no-change | 19159.8 | 19172.0 | -0.064% |
| `prelude.h` | `sstream` | 24 | no-change | 19159.3 | 19135.9 | 0.122% |
| `prelude.h` | `new` | 24 | no-change | 18508.0 | 18526.1 | -0.098% |
| `prelude.h` | `cstdlib` | 24 | no-change | 18472.1 | 18456.0 | 0.087% |
| `prelude.h` | `iostream` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `debugger.h` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `version.h` | 24 | no-change | 18461.0 | 18487.2 | -0.142% |

