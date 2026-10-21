# 闸门的统计口径：只看 owner 还是看使用方（2026-10-18，zlib）

> **口径注记（2026-10-21 合入时补）**：本归档产自**声明级口径的实验分支**（`header_ids` 走 `attrib/decls.declared_names`）。该口径**未合入判定主线**——它改的是命中集合本身，换上去之后 2026-10-19/20 三轮精度工作的数字都要重跑才成立，实测会让 3 个精度测试回归（见 `docs/methodology.md` §6）。因此这里的候选池与主线 G2 不是同一口径，两组数字**不可混用**；本归档只用于支撑「下游引用对能否编译没有预测力」这个否定性结论。


- 工程：zlib，15 个 TU，可枚举 include 行 16 条。
- 放行条数：owner 口径 4 / 使用方(IN_USE) 1 / 使用方(+UNKNOWN) 1。

| 改动位置 | 头文件 | 行 | 受影响 TU | 累计行 | owner 判定 | 使用方人数 | 使用方口径 | 说明 |
|---|---|---:|---:|---:|---|---:|---|---|
| `zutil.h` | `zlib.h` | - | 9 | 46341 | in_use | 10 | 拦下 | 两口径都拦 |
| `zutil.h` | `string.h` | - | 9 | 3762 | in_use | 10 | 拦下 | 两口径都拦 |
| `zutil.h` | `stdlib.h` | - | 9 | 10638 | plausibly_unused | 10 | 拦下 | **放行是错的**（使用方在用） |
| `zlib.h` | `zconf.h` | - | 15 | 48645 | in_use | 18 | 拦下 | 两口径都拦 |
| `zconf.h` | `stddef.h` | - | 15 | 4380 | plausibly_unused | 19 | 拦下 | **放行是错的**（使用方在用） |
| `zconf.h` | `limits.h` | - | 15 | 31410 | in_use | 19 | 拦下 | 两口径都拦 |
| `zconf.h` | `types.h` | - | 15 | 2205 | in_use | 19 | 放行 | owner 拦下，使用方没人用（现状偏保守） |
| `zconf.h` | `stdarg.h` | - | 15 | 2385 | plausibly_unused | 19 | 拦下 | **放行是错的**（使用方在用） |
| `deflate.h` | `zutil.h` | - | 2 | 14466 | in_use | 2 | 拦下 | 两口径都拦 |
| `gzguts.h` | `stdio.h` | - | 5 | 11640 | in_use | 5 | 拦下 | 两口径都拦 |
| `gzguts.h` | `zlib.h` | - | 5 | 16435 | in_use | 5 | 拦下 | 两口径都拦 |
| `gzguts.h` | `string.h` | - | 5 | 2090 | in_use | 5 | 拦下 | 两口径都拦 |
| `gzguts.h` | `stdlib.h` | - | 5 | 5910 | in_use | 5 | 拦下 | 两口径都拦 |
| `gzguts.h` | `fcntl.h` | - | 5 | 2670 | in_use | 5 | 拦下 | 两口径都拦 |
| `gzguts.h` | `stat.h` | - | 5 | 1425 | plausibly_unused | 5 | 拦下 | **放行是错的**（使用方在用） |
| `gzguts.h` | `errno.h` | - | 5 | 1205 | in_use | 5 | 拦下 | 两口径都拦 |

