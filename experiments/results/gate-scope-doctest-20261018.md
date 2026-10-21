# 闸门的统计口径：只看 owner 还是看使用方（2026-10-18，doctest）

> **口径注记（2026-10-21 合入时补）**：本归档产自**声明级口径的实验分支**（`header_ids` 走 `attrib/decls.declared_names`）。该口径**未合入判定主线**——它改的是命中集合本身，换上去之后 2026-10-19/20 三轮精度工作的数字都要重跑才成立，实测会让 3 个精度测试回归（见 `docs/methodology.md` §6）。因此这里的候选池与主线 G2 不是同一口径，两组数字**不可混用**；本归档只用于支撑「下游引用对能否编译没有预测力」这个否定性结论。


- 工程：doctest，24 个 TU，可枚举 include 行 66 条。
- 放行条数：owner 口径 16 / 使用方(IN_USE) 2 / 使用方(+UNKNOWN) 1。

| 改动位置 | 头文件 | 行 | 受影响 TU | 累计行 | owner 判定 | 使用方人数 | 使用方口径 | 说明 |
|---|---|---:|---:|---:|---|---:|---|---|
| `doctest\parts\private\prelude.h` | `doctest_fwd.h` | - | 24 | 97632 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `ctime` | - | 24 | 66936 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `cmath` | - | 24 | 784968 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `climits` | - | 24 | 1584 | unknown | 24 | 放行 | owner 拦下，使用方没人用（现状偏保守） |
| `doctest\parts\private\prelude.h` | `new` | - | 24 | 10608 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `cstdio` | - | 24 | 61344 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `cstdlib` | - | 24 | 6912 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `cstring` | - | 24 | 13176 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `utility` | - | 24 | 10392 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `fstream` | - | 24 | 997056 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `sstream` | - | 24 | 41712 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `iostream` | - | 24 | 2160 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `algorithm` | - | 24 | 216456 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `iomanip` | - | 24 | 134832 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `vector` | - | 24 | 174096 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `atomic` | - | 24 | 125928 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `mutex` | - | 24 | 277032 | unknown | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `set` | - | 24 | 152088 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `map` | - | 24 | 78552 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `unordered_set` | - | 24 | 177024 | plausibly_unused | 24 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\private\prelude.h` | `cfloat` | - | 24 | 15024 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `windows.h` | - | 24 | 2944992 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\private\prelude.h` | `io.h` | - | 24 | 11568 | in_use | 24 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `version.h` | - | 24 | 672 | plausibly_unused | 25 | 放行 | 两口径都放行 |
| `doctest\parts\doctest_fwd.h` | `compiler.h` | - | 24 | 1176 | unknown | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `warnings.h` | - | 24 | 4632 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `config.h` | - | 24 | 126192 | plausibly_unused | 25 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\doctest_fwd.h` | `utility.h` | - | 24 | 1344 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `debugger.h` | - | 24 | 1368 | plausibly_unused | 25 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\doctest_fwd.h` | `fwd.h` | - | 24 | 1416 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `type_traits.h` | - | 24 | 2616 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `utility.h` | - | 24 | 816 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `string.h` | - | 24 | 10896 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `contains.h` | - | 24 | 720 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `approx.h` | - | 24 | 2232 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `is_nan.h` | - | 24 | 912 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `options.h` | - | 24 | 1608 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `type.h` | - | 24 | 2544 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `data.h` | - | 24 | 1728 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `comparator.h` | - | 24 | 2280 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `result.h` | - | 24 | 2832 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `expression.h` | - | 24 | 4200 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `color.h` | - | 24 | 840 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `subcase.h` | - | 24 | 1056 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `test_suite.h` | - | 24 | 1368 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `test_case.h` | - | 24 | 1920 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `decorators.h` | - | 24 | 888 | plausibly_unused | 25 | 拦下 | **放行是错的**（使用方在用） |
| `doctest\parts\doctest_fwd.h` | `exception_translator.h` | - | 24 | 1704 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `context_scope.h` | - | 24 | 1872 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `message.h` | - | 24 | 1680 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `path.h` | - | 24 | 384 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `exceptions.h` | - | 24 | 720 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `context.h` | - | 24 | 1320 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `handler.h` | - | 24 | 2232 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `reporter.h` | - | 24 | 3144 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `generator.h` | - | 24 | 672 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\doctest_fwd.h` | `macros.h` | - | 24 | 24480 | in_use | 25 | 拦下 | 两口径都拦 |
| `doctest\parts\public\config.h` | `platform.h` | - | 24 | 648 | in_use | 26 | 拦下 | 两口径都拦 |
| `doctest\parts\public\config.h` | `version` | - | 24 | 120600 | in_use | 26 | 拦下 | 两口径都拦 |
| `doctest\parts\private\context_state.h` | `timer.h` | - | 13 | 546 | in_use | 15 | 拦下 | 两口径都拦 |
| `doctest\parts\private\context_state.h` | `atomic.h` | - | 13 | 1560 | in_use | 15 | 拦下 | 两口径都拦 |
| `doctest\parts\private\context_state.h` | `traversal.h` | - | 13 | 702 | in_use | 15 | 拦下 | 两口径都拦 |
| `doctest\parts\private\reporter.h` | `context_state.h` | - | 6 | 1620 | in_use | 6 | 拦下 | 两口径都拦 |
| `doctest\parts\private\reporters\debug_output_window.h` | `console.h` | - | 2 | 258 | in_use | 2 | 拦下 | 两口径都拦 |
| `doctest\parts\private\reporters\debug_output_window.h` | `context_state.h` | - | 2 | 540 | in_use | 2 | 拦下 | 两口径都拦 |
| `doctest\parts\private\reporters\console.h` | `common.h` | - | 3 | 72 | in_use | 4 | 拦下 | 两口径都拦 |

