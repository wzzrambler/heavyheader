# 声明级口径后的编译探测（2026-10-18，doctest）

> **口径注记（2026-10-21 合入时补）**：本归档产自**声明级口径的实验分支**（`header_ids` 走 `attrib/decls.declared_names`）。该口径**未合入判定主线**——它改的是命中集合本身，换上去之后 2026-10-19/20 三轮精度工作的数字都要重跑才成立，实测会让 3 个精度测试回归（见 `docs/methodology.md` §6）。因此这里的候选池与主线 G2 不是同一口径，两组数字**不可混用**；本归档只用于支撑「下游引用对能否编译没有预测力」这个否定性结论。


- 闸门候选 17 条：编译通过 9，失败 8。

| 改动位置 | include | 下游引用 | 探测结果 |
|---|---|---:|---|
| `prelude.h` | `algorithm` | 7 | compile-error |
| `prelude.h` | `unordered_set` | 9 | compiles |
| `prelude.h` | `vector` | 11 | compile-error |
| `prelude.h` | `set` | 5 | compile-error |
| `prelude.h` | `iomanip` | 11 | compile-error |
| `doctest_fwd.h` | `config.h` | 10 | compiles |
| `prelude.h` | `atomic` | 3 | compile-error |
| `config.h` | `version` | 2 | compiles |
| `prelude.h` | `map` | 1 | compile-error |
| `prelude.h` | `sstream` | 1 | compiles |
| `prelude.h` | `new` | 1 | compiles |
| `prelude.h` | `utility` | 2 | compiles |
| `prelude.h` | `cstdlib` | 2 | compiles |
| `prelude.h` | `iostream` | 5 | compile-error |
| `doctest_fwd.h` | `debugger.h` | 25 | compile-error |
| `doctest_fwd.h` | `decorators.h` | 24 | compiles |
| `doctest_fwd.h` | `version.h` | 0 | compiles |

