# HeavyHeader 构建成本报告

- 项目根目录：`C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json`
- 编译器：`g++`（gcc）
- 语言标准：`c++17`
- 编译单元：25 成功 / 0 失败
- 头文件依赖边：9,294
- 全项目头文件解析量：**4,866,676 行**
- 唯一文件行数：213,458 行
- **Blowup Factor：22.8×**（平均每行被解析的次数；与 Header Hero 同定义，见 `docs/competitors.md`）
- 累计编译耗时：1.5 min

## 成本热点 Top 15

按「归因行数」排序，即 `头文件行数 × 引用它的编译单元数`——
它衡量的是这个头文件让整个项目多解析了多少行代码。

| 头文件 | 行数 | 被引用 TU | 归因行数 | 传递闭包 | 最大深度 |
|---|---:|---:|---:|---:|---:|
| `include\nlohmann\json.hpp` | 8,177 | 24 | 196,248 | 191 | 2 |
| `include\nlohmann\detail\input\binary_reader.hpp` | 4,471 | 24 | 107,304 | 13 | 1 |
| `include\nlohmann\detail\output\binary_writer.hpp` | 3,280 | 24 | 78,720 | 1 | 1 |
| `include\nlohmann\detail\input\lexer.hpp` | 2,356 | 24 | 56,544 | 9 | 3 |
| `include\nlohmann\thirdparty\hedley\hedley.hpp` | 2,046 | 24 | 49,104 | 0 | 6 |
| `include\nlohmann\detail\output\serializer.hpp` | 1,738 | 24 | 41,712 | 5 | 1 |
| `include\nlohmann\detail\json_pointer.hpp` | 1,561 | 24 | 37,464 | 0 | 1 |
| `include\nlohmann\detail\conversions\to_chars.hpp` | 1,493 | 24 | 35,832 | 4 | 2 |
| `include\nlohmann\detail\input\json_sax.hpp` | 1,187 | 24 | 28,488 | 10 | 2 |
| `include\nlohmann\detail\meta\type_traits.hpp` | 1,135 | 24 | 27,240 | 8 | 4 |
| `include\nlohmann\detail\input\number_parse.hpp` | 1,054 | 24 | 25,296 | 6 | 4 |
| `include\nlohmann\detail\input\input_adapters.hpp` | 925 | 24 | 22,200 | 0 | 2 |
| `include\nlohmann\detail\macro_scope.hpp` | 897 | 24 | 21,528 | 16 | 5 |
| `include\nlohmann\detail\iterators\iter_impl.hpp` | 708 | 24 | 16,992 | 0 | 1 |
| `include\nlohmann\detail\conversions\from_json.hpp` | 673 | 24 | 16,152 | 79 | 2 |

## 实验性：增量构建税（未校准，勿据此决策）

上一节回答「整个项目为它付了多少解析量」；这一节试答「**你改它一行，下一次增量构建要等多久**」。这是编辑循环里的实际痛感，也是现有竞品没有给出的视角——但它目前**只是原型**：受控实验（`experiments/incremental_tax.py`）显示预测比实测**偏高约 1.7×**，因为扫描的 `--full-compile` 会为每个单元额外链接一次，而真实增量构建只编译不链接。校准完成前，本表不得作为结论引用。

| 头文件 | 重编 TU | 预计等待 | 占单次全量构建 |
|---|---:|---:|---:|
| `include\nlohmann\json.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\input\binary_reader.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\output\binary_writer.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\input\lexer.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\thirdparty\hedley\hedley.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\output\serializer.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\json_pointer.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\conversions\to_chars.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\input\json_sax.hpp` | 24 | 1.5 min | 99% |
| `include\nlohmann\detail\meta\type_traits.hpp` | 24 | 1.5 min | 99% |

> 口径：扫描期每个 TU 实测耗时之和（默认 `-fsyntax-only`，不含链接）。它是一个**预测**，预测误差与验证方法见 `docs/methodology.md` 的「增量构建税」一节。`0` 表示该单元没有可用计时，不是「瞬间完成」。

## 成本候选（按预计收益排序）

> **这张表说的是「贵」，不是「能删」。** 「独占闭包」指删掉这条 include 后，只有它会停止被解析的头文件集合——如果某个头文件还能从别的 include 到达，删掉当前这条并不省钱。这是本表与普通 lint 工具的区别。
>
> 「结论」列是明确判断（建议删除/保留/需人工判断），依据见下一节；「未实测」表示这一轮没有跑 `--probe`，此时不给结论——没测过就下判断是编造。

| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 判据 | 结论 |
|---|---|---:|---:|---:|---|---|---|
| `cmake\detect_libcpp_version.cpp` | `c++\cstdio` | 11 | 4,335 | 65.1% | 无法判定 | printf 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `cmake\detect_libcpp_version.cpp` | `c++\ciso646` | 1 | 54 | 0.8% | 无法判定 | 这个头只做转发或只提供宏（没有函数/类型声明），它的作用取决于后续头怎么用这些宏 | 未实测 |
| `docs\mkdocs\docs\examples\at__json_pointer.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\at__size_type.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\at__object_t_key_type.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\at__json_pointer_const.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\at__keytype_const.c++17.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\at__keytype.c++17.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\array.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\README.cpp` | `include\nlohmann\json.hpp` | 153 | 119,284 | 59.0% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\basic_json__copyassignment.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\at__object_t_key_type_const.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\at__size_type_const.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\basic_json__InputIt_InputIt.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\as_base_class.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\basic_json__basic_json.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\back.cpp` | `include\nlohmann\json.hpp` | 169 | 128,268 | 63.5% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\accept__string.cpp` | `include\nlohmann\json.hpp` | 153 | 119,284 | 59.0% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\array_t.cpp` | `include\nlohmann\json.hpp` | 153 | 119,284 | 59.0% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |
| `docs\mkdocs\docs\examples\accept__iterator_pair.cpp` | `include\nlohmann\json.hpp` | 164 | 121,014 | 59.9% | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s | 未实测 |


### 待验证队列：53 条等 `verify` 裁定

顺序即 `heavyheader verify --top N` 的测量顺序：**疑似未使用**先测
（静态判定已经指向删除），**无法判定**次之（静态给不出结论，但反事实
实验能裁——Lua 语料上这一层 32/32 条实测可删），**在用**默认不测
（要测得加 `--ungated-sites`）。

- 疑似未使用 **0** 条 · 无法判定 **2** 条 · 在用 **51** 条

| 编译单元 | include | 预计省 (s) | 使用状态 | 判据 |
|---|---|---:|---|---|
| `cmake\detect_libcpp_version.cpp` | `c++\cstdio` | 0.0390 | 无法判定 | printf 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 |
| `cmake\detect_libcpp_version.cpp` | `c++\ciso646` | 0.0005 | 无法判定 | 这个头只做转发或只提供宏（没有函数/类型声明），它的作用取决于后续头怎么用这些宏 |
| `docs\mkdocs\docs\examples\at__json_pointer.cpp` | `include\nlohmann\json.hpp` | 2.7609 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |
| `docs\mkdocs\docs\examples\at__size_type.cpp` | `include\nlohmann\json.hpp` | 2.7535 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |
| `docs\mkdocs\docs\examples\at__object_t_key_type.cpp` | `include\nlohmann\json.hpp` | 2.6946 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |
| `docs\mkdocs\docs\examples\at__json_pointer_const.cpp` | `include\nlohmann\json.hpp` | 2.6814 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |
| `docs\mkdocs\docs\examples\at__keytype_const.c++17.cpp` | `include\nlohmann\json.hpp` | 2.6731 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |
| `docs\mkdocs\docs\examples\at__keytype.c++17.cpp` | `include\nlohmann\json.hpp` | 2.6642 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |
| `docs\mkdocs\docs\examples\array.cpp` | `include\nlohmann\json.hpp` | 2.6239 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |
| `docs\mkdocs\docs\examples\README.cpp` | `include\nlohmann\json.hpp` | 2.5867 | 正在使用 | 副作用：template<typename _Tp, size_t _Nm>     inline constexpr size_t tuple_s |

## 实测状态

**本轮没有做实测。** 上表全部是估算——按行数折算的预计收益，不是秒数，也不代表这些 include 删掉之后还能编译。

两个档位可选：`--probe-compile` 只验「删了还能不能编」（每候选一次编译）；`--probe` 在它之上再测耗时（两侧各 N 次取最小，判 improved 后还要复测）。

## 缺少 include guard 的头文件（必赚项）

这些头文件在**同一个编译单元内被重复解析**。对带 guard 的头文件来说这在物理上不可能发生，所以这是「缺少 guard」的确定性信号。补上 `#pragma once` 或 guard 宏永远安全，且收益立竿见影。

| 头文件 | 解析次数 | 涉及 TU | 多解析次数 |
|---|---:|---:|---:|
| `bits\version.h` | 1083 | 24 | 1059 |
| `bits\stdexcept_throw.h` | 169 | 24 | 145 |
| `c++\stdlib.h` | 96 | 24 | 72 |
| `include\stddef.h` | 96 | 24 | 72 |
| `include\stddef.h` | 96 | 24 | 72 |
| `include\_mingw.h` | 50 | 25 | 25 |
| `include\stdio.h` | 50 | 25 | 25 |
| `include\nlohmann\json.hpp` | 48 | 24 | 24 |
| `include\emmintrin.h` | 48 | 24 | 24 |
| `include\limits.h` | 48 | 24 | 24 |
| `include\corecrt_wconio.h` | 48 | 24 | 24 |
| `include\stdlib.h` | 48 | 24 | 24 |
| `include\string.h` | 48 | 24 | 24 |
| `sys\timeb.h` | 48 | 24 | 24 |
| `include\wchar.h` | 48 | 24 | 24 |

## 模板实例化成本

全项目模板实例化合计 **16.93 s**，占总构建时间 **23.3%**。

> 每个编译单元的数字来自 `-ftime-report`，是精确的；下面「模板表面」一列是**文本启发式**（`template<` 声明数 × 引用它的 TU 数），用于提示成本可能来自哪里，不是实测归因。

**模板实例化占比超过 25% 的编译单元：**

- `docs\mkdocs\docs\examples\as_base_class.cpp`：45.2%（1.45 s）
- `docs\mkdocs\docs\examples\basic_json__InputIt_InputIt.cpp`：44.4%（1.43 s）
- `docs\mkdocs\docs\examples\at__size_type_const.cpp`：44.2%（1.44 s）
- `docs\mkdocs\docs\examples\basic_json__basic_json.cpp`：43.6%（1.37 s）
- `docs\mkdocs\docs\examples\back.cpp`：42.1%（1.28 s）
- `docs\mkdocs\docs\examples\basic_json__size_type_basic_json.cpp`：41.8%（640 ms）
- `docs\mkdocs\docs\examples\at__object_t_key_type_const.cpp`：40.4%（1.32 s）
- `docs\mkdocs\docs\examples\array_t.cpp`：38.0%（1.08 s）
- `docs\mkdocs\docs\examples\accept__iterator_pair.cpp`：37.3%（1.03 s）
- `docs\mkdocs\docs\examples\accept__string.cpp`：37.0%（1.05 s）
- `docs\mkdocs\docs\examples\README.cpp`：36.5%（1.60 s）
- `docs\mkdocs\docs\examples\array.cpp`：32.9%（1.36 s）
- `docs\mkdocs\docs\examples\basic_json__list_init_t.cpp`：31.9%（640 ms）
- `docs\mkdocs\docs\examples\basic_json__nullptr_t.cpp`：31.3%（620 ms）
- `docs\mkdocs\docs\examples\basic_json__moveconstructor.cpp`：30.8%（620 ms）

| 头文件 | template< 声明数 | 被引用 TU | 模板表面 |
|---|---:|---:|---:|
| `include\nlohmann\json.hpp` | 173 | 24 | 4152 |
| `include\nlohmann\detail\meta\type_traits.hpp` | 164 | 24 | 3936 |
| `include\nlohmann\detail\conversions\to_json.hpp` | 73 | 24 | 1752 |
| `include\nlohmann\detail\conversions\from_json.hpp` | 52 | 24 | 1248 |
| `include\nlohmann\detail\input\input_adapters.hpp` | 36 | 24 | 864 |
| `include\nlohmann\detail\json_pointer.hpp` | 33 | 24 | 792 |
| `include\nlohmann\detail\exceptions.hpp` | 26 | 24 | 624 |
| `include\nlohmann\detail\output\binary_writer.hpp` | 23 | 24 | 552 |
| `include\nlohmann\detail\string_concat.hpp` | 22 | 24 | 528 |
| `include\nlohmann\ordered_map.hpp` | 20 | 24 | 480 |

模板表面大、又被大量 TU 引用的头文件，是模板实例化成本的主要嫌疑对象。这是**调查线索，不是结论**——编译器只报告阶段总量，不做按头文件的归因。系统头文件（如 `<type_traits>`）的模板声明数通常更高，但用户改不了，故未列入。

## 预编译头（PCH）收益估算

建议将下列 5 个头文件纳入 PCH。它们的传递闭包共 **139,180 行**，其中 **24/25** 个编译单元已全部包含它们（覆盖率 96%）。

| 头文件 | 行数 | 被引用 TU |
|---|---:|---:|
| `include\nlohmann\json.hpp` | 8,177 | 24 |
| `include\nlohmann\json_literals.hpp` | 78 | 24 |
| `include\nlohmann\adl_serializer.hpp` | 55 | 24 |
| `include\nlohmann\detail\conversions\from_json.hpp` | 673 | 24 |
| `include\nlohmann\detail\meta\std_fs.hpp` | 29 | 24 |

**估算净收益：3,201,140 行（占全项目头文件解析量 65.8%）——值得做。**

> 这是估算，不是承诺。四条限制：
> - 基于行数代理，不是秒数。模板重型头文件的实际成本会被低估。
> - 上限估计：真实 PCH 必须是每个使用它的 TU 的第一个 include，且各 TU 间字节一致。include 顺序不一致的项目实际收益会更低。
> - 未计入 PCH 失效：改动 PCH 中任何一个头文件都会让所有 TU 的 PCH 作废，因此 PCH 只应放稳定的头文件（第三方库、极少改动的内部基础库）。
> - 本工具不生成 PCH，只做估算。

## 扫描警告

- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\include\nlohmann\json.hpp: parsed 48x across 24 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\README.cpp: discarded impossible timing (phase lang. deferred=18446744070.53s (exceeds measured wall 4.38s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\README.cpp: discarded impossible timing (parser (global)=18446744069.66s (exceeds measured wall 4.38s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\README.cpp: discarded impossible timing (TOTAL=18446744072.93s (exceeds measured wall 4.38s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\README.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\array.cpp: discarded impossible timing (phase lang. deferred=18446744070.39s (exceeds measured wall 4.13s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\array.cpp: discarded impossible timing (parser (global)=18446744069.65s (exceeds measured wall 4.13s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\array.cpp: discarded impossible timing (TOTAL=18446744072.69s (exceeds measured wall 4.13s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\array.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer.cpp: discarded impossible timing (phase lang. deferred=18446744070.50s (exceeds measured wall 4.35s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer.cpp: discarded impossible timing (|overload resolution=18446744070.37s (exceeds measured wall 4.35s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer.cpp: discarded impossible timing (template instantiation=18446744070.96s (exceeds measured wall 4.35s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer.cpp: discarded impossible timing (TOTAL=18446744072.92s (exceeds measured wall 4.35s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer_const.cpp: discarded impossible timing (phase lang. deferred=18446744070.47s (exceeds measured wall 4.22s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer_const.cpp: discarded impossible timing (|overload resolution=18446744070.26s (exceeds measured wall 4.22s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer_const.cpp: discarded impossible timing (template instantiation=18446744070.85s (exceeds measured wall 4.22s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer_const.cpp: discarded impossible timing (TOTAL=18446744072.72s (exceeds measured wall 4.22s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__json_pointer_const.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- C:\Users\31879\AppData\Local\Temp\heavyheader-bench\nlohmann-json\docs\mkdocs\docs\examples\at__keytype.c++17.cpp: discarded impossible timing (phase lang. deferred=18446744070.52s (exceeds measured wall 4.20s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable

---

由 [HeavyHeader](https://github.com/wzzrambler/heavyheader) 生成 · 指标定义见 `docs/methodology.md`
