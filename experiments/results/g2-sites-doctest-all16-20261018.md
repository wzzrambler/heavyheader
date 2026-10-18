# G2 重做：头文件级候选 + 交替测量（2026-10-18，doctest）

- 工程：doctest，24 个 TU。
- 候选池：可行性闸门开 16 条 / 关 66 条。
- 协议：clang++.exe，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| feasible(header,gated) | 16 | 8 | 1 | **6%** |

## feasible(header,gated) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `prelude.h` | `algorithm` | 24 | compile-error | None | None | None% |
| `prelude.h` | `unordered_set` | 24 | improved | 20745.9 | 20121.8 | 3.008% |
| `prelude.h` | `vector` | 24 | compile-error | None | None | None% |
| `prelude.h` | `set` | 24 | compile-error | None | None | None% |
| `prelude.h` | `iomanip` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `config.h` | 24 | no-change | 20731.6 | 20841.1 | -0.528% |
| `prelude.h` | `atomic` | 24 | compile-error | None | None | None% |
| `prelude.h` | `map` | 24 | compile-error | None | None | None% |
| `prelude.h` | `sstream` | 24 | no-change | 21100.1 | 21122.1 | -0.104% |
| `prelude.h` | `new` | 24 | no-change | 20981.2 | 21010.6 | -0.14% |
| `prelude.h` | `utility` | 24 | no-change | 20784.5 | 20722.6 | 0.298% |
| `prelude.h` | `cstdlib` | 24 | no-change | 20709.9 | 20718.9 | -0.044% |
| `prelude.h` | `iostream` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `debugger.h` | 24 | compile-error | None | None | None% |
| `doctest_fwd.h` | `decorators.h` | 24 | no-change | 20802.1 | 20841.7 | -0.19% |
| `doctest_fwd.h` | `version.h` | 24 | no-change | 20851.3 | 20858.0 | -0.032% |

