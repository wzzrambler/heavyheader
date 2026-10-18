# 闸门三档对照：编译通过率与成本（2026-10-18，doctest）

- 工程：doctest，24 个 TU，编译器 `C:/Program Files/LLVM/bin/clang++.exe`。
- 协议：只做编译探测（每条候选删一次、编一次），**不测时间**。

| 闸门策略 | 候选 | 编译通过 | 编译失败 | 通过率 | 耗时 | 每条耗时 |
|---|---:|---:|---:|---:|---:|---:|
| strict | 16 | 8 | 8 | **50%** | 217s | 13.6s |
| unknown | 19 | 10 | 9 | **53%** | 263s | 13.8s |
| off | 66 | 31 | 35 | **47%** | 810s | 12.3s |

> 「编译通过」只证明删掉这行还能编，**不代表省时**。省时要完整测量，
> 成本约为本表的一个数量级，见 `g2-sites-doctest-all16`。

## strict 通过编译的候选

| 改动位置 | include | 受影响 TU |
|---|---|---:|
| `prelude.h` | `unordered_set` | 24 |
| `doctest_fwd.h` | `config.h` | 24 |
| `prelude.h` | `sstream` | 24 |
| `prelude.h` | `new` | 24 |
| `prelude.h` | `utility` | 24 |
| `prelude.h` | `cstdlib` | 24 |
| `doctest_fwd.h` | `decorators.h` | 24 |
| `doctest_fwd.h` | `version.h` | 24 |

## unknown 通过编译的候选

| 改动位置 | include | 受影响 TU |
|---|---|---:|
| `prelude.h` | `unordered_set` | 24 |
| `doctest_fwd.h` | `config.h` | 24 |
| `prelude.h` | `sstream` | 24 |
| `prelude.h` | `new` | 24 |
| `prelude.h` | `utility` | 24 |
| `prelude.h` | `cstdlib` | 24 |
| `doctest_fwd.h` | `decorators.h` | 24 |
| `doctest_fwd.h` | `version.h` | 24 |
| `prelude.h` | `climits` | 24 |
| `doctest_fwd.h` | `compiler.h` | 24 |

## off 通过编译的候选

| 改动位置 | include | 受影响 TU |
|---|---|---:|
| `prelude.h` | `unordered_set` | 24 |
| `doctest_fwd.h` | `config.h` | 24 |
| `prelude.h` | `sstream` | 24 |
| `prelude.h` | `new` | 24 |
| `prelude.h` | `utility` | 24 |
| `prelude.h` | `cstdlib` | 24 |
| `doctest_fwd.h` | `decorators.h` | 24 |
| `doctest_fwd.h` | `version.h` | 24 |
| `prelude.h` | `climits` | 24 |
| `doctest_fwd.h` | `compiler.h` | 24 |
| `config.h` | `version` | 24 |
| `prelude.h` | `ctime` | 24 |
| `prelude.h` | `cstdio` | 24 |
| `doctest_fwd.h` | `string.h` | 24 |
| `doctest_fwd.h` | `result.h` | 24 |
| `doctest_fwd.h` | `type_traits.h` | 24 |
| `doctest_fwd.h` | `type.h` | 24 |
| `doctest_fwd.h` | `comparator.h` | 24 |
| `doctest_fwd.h` | `test_case.h` | 24 |
| `doctest_fwd.h` | `context_scope.h` | 24 |
| `doctest_fwd.h` | `data.h` | 24 |
| `doctest_fwd.h` | `message.h` | 24 |
| `doctest_fwd.h` | `test_suite.h` | 24 |
| `doctest_fwd.h` | `utility.h` | 24 |
| `doctest_fwd.h` | `context.h` | 24 |
| `doctest_fwd.h` | `subcase.h` | 24 |
| `doctest_fwd.h` | `utility.h` | 24 |
| `doctest_fwd.h` | `contains.h` | 24 |
| `doctest_fwd.h` | `generator.h` | 24 |
| `config.h` | `platform.h` | 24 |
| `debug_output_window.h` | `context_state.h` | 2 |

