# G2 重做：头文件级候选 + 交替测量（2026-10-19，zlib-develop）

- 工程：zlib-develop，15 个 TU。
- 候选池：可行性闸门开 1 条 / 关 16 条。
- 协议：clang.exe，交替 min-of-3，聚合噪声线 1%，improved 需复测。

| 臂 | 候选 | 编译通过 | improved | 采纳率 |
|---|---:|---:|---:|---:|
| ungated(header,top-by-size) | 16 | 6 | 1 | **6%** |

## ungated(header,top-by-size) 逐条

| 改动位置 | 头文件 | 受影响 TU | 结论 | 基线 ms | 移除后 ms | 变化 |
|---|---|---:|---|---:|---:|---:|
| `zlib.h` | `zconf.h` | 15 | ambiguous | None | None | None% |
| `zconf.h` | `stddef.h` | 15 | ambiguous | None | None | None% |
| `zutil.h` | `stdlib.h` | 9 | improved | 1307.6 | 1273.0 | 2.652% |
| `zutil.h` | `zlib.h` | 9 | compile-error | None | None | None% |
| `zconf.h` | `limits.h` | 15 | no-change | 2153.4 | 2156.5 | -0.143% |
| `gzguts.h` | `zlib.h` | 5 | compile-error | None | None | None% |
| `deflate.h` | `zutil.h` | 2 | compile-error | None | None | None% |
| `gzguts.h` | `stdio.h` | 5 | compile-error | None | None | None% |
| `gzguts.h` | `stdlib.h` | 5 | compile-error | None | None | None% |
| `zutil.h` | `string.h` | 9 | compile-error | None | None | None% |
| `zconf.h` | `stdarg.h` | 15 | no-change | 2168.2 | 2165.4 | 0.131% |
| `gzguts.h` | `fcntl.h` | 5 | compile-error | None | None | None% |
| `zconf.h` | `types.h` | 15 | no-change | 2174.2 | 2173.1 | 0.052% |
| `gzguts.h` | `string.h` | 5 | no-change | 738.2 | 739.7 | -0.21% |
| `gzguts.h` | `stat.h` | 5 | compile-error | None | None | None% |
| `gzguts.h` | `errno.h` | 5 | no-change | 740.0 | 736.2 | 0.513% |

