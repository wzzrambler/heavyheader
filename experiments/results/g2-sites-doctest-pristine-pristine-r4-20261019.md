# G2 重做：头文件级候选 + 交替测量（2026-10-19，doctest-pristine）

- 工程：doctest-pristine，24 个 TU。
- 候选池：可行性闸门开 13 条 / 关 66 条。
- 协议：clang++.exe，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| feasible(header,gated) | 13 | 8 | 1 | **8%** |

## feasible(header,gated) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `prelude.h` | `unordered_set` | 24 | improved | 19277.6 | 18891.8 | 2.002% |
| `prelude.h` | `utility` | 24 | no-change | 19363.3 | 19259.3 | 0.537% |
| `prelude.h` | `iomanip` | 24 | compile-error | None | None | None% |
| `prelude.h` | `atomic` | 24 | compile-error | None | None | None% |
| `prelude.h` | `map` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `config.h` | 24 | no-change | 19288.2 | 19320.4 | -0.167% |
| `prelude.h` | `limits` | 24 | no-change | 19399.7 | 19408.8 | -0.047% |
| `prelude.h` | `sstream` | 24 | no-change | 19324.7 | 19392.0 | -0.348% |
| `prelude.h` | `new` | 24 | no-change | 19373.5 | 19368.0 | 0.029% |
| `prelude.h` | `cstdlib` | 24 | no-change | 19336.2 | 19333.1 | 0.016% |
| `prelude.h` | `iostream` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `debugger.h` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `version.h` | 24 | no-change | 19480.3 | 19556.1 | -0.389% |

