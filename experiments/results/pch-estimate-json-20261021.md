# HeavyHeader 构建成本报告

- 项目根目录：`C:\Users\31879\WorkBuddy\2026-10-18-23-59-50\heavyheader\experiments\_work\json`
- 编译器：`g++`（gcc）
- 编译单元：304 成功 / 80 失败
- 头文件依赖边：100,537
- 全项目头文件解析量：**51,980,266 行**
- 唯一文件行数：475,787 行
- **Blowup Factor：109.3×**（平均每行被解析的次数；与 Header Hero 同定义，见 `docs/competitors.md`）
- 累计编译耗时：18.2 min

## 成本热点 Top 15

按「归因行数」排序，即 `头文件行数 × 引用它的编译单元数`——
它衡量的是这个头文件让整个项目多解析了多少行代码。

| 头文件 | 行数 | 被引用 TU | 归因行数 | 传递闭包 | 最大深度 |
|---|---:|---:|---:|---:|---:|
| `include\nlohmann\json.hpp` | 5,258 | 231 | 1,214,598 | 179 | 1 |
| `include\nlohmann\detail\input\binary_reader.hpp` | 3,009 | 231 | 695,079 | 8 | 2 |
| `include\nlohmann\thirdparty\hedley\hedley.hpp` | 2,045 | 231 | 472,395 | 0 | 7 |
| `include\nlohmann\detail\output\binary_writer.hpp` | 1,838 | 231 | 424,578 | 1 | 2 |
| `include\nlohmann\detail\input\lexer.hpp` | 1,633 | 231 | 377,223 | 0 | 3 |
| `include\nlohmann\detail\conversions\to_chars.hpp` | 1,118 | 231 | 258,258 | 0 | 3 |
| `include\nlohmann\detail\output\serializer.hpp` | 988 | 231 | 228,228 | 1 | 2 |
| `include\nlohmann\detail\json_pointer.hpp` | 988 | 231 | 228,228 | 0 | 2 |
| `include\nlohmann\detail\meta\type_traits.hpp` | 795 | 231 | 183,645 | 4 | 5 |
| `include\nlohmann\detail\iterators\iter_impl.hpp` | 751 | 231 | 173,481 | 0 | 2 |
| `include\nlohmann\detail\input\json_sax.hpp` | 727 | 231 | 167,937 | 0 | 3 |
| `include\nlohmann\detail\input\parser.hpp` | 519 | 231 | 119,889 | 0 | 2 |
| `include\nlohmann\detail\conversions\from_json.hpp` | 497 | 231 | 114,807 | 98 | 3 |
| `include\nlohmann\detail\input\input_adapters.hpp` | 492 | 231 | 113,652 | 4 | 3 |
| `include\nlohmann\detail\macro_scope.hpp` | 482 | 231 | 111,342 | 18 | 6 |

## 实验性：增量构建税（未校准，勿据此决策）

上一节回答「整个项目为它付了多少解析量」；这一节试答「**你改它一行，下一次增量构建要等多久**」。这是编辑循环里的实际痛感，也是现有竞品没有给出的视角——但它目前**只是原型**：受控实验（`experiments/incremental_tax.py`）显示预测比实测**偏高约 1.7×**，因为扫描的 `--full-compile` 会为每个单元额外链接一次，而真实增量构建只编译不链接。校准完成前，本表不得作为结论引用。

| 头文件 | 重编 TU | 预计等待 | 占单次全量构建 |
|---|---:|---:|---:|
| `include\nlohmann\json.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\input\binary_reader.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\thirdparty\hedley\hedley.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\output\binary_writer.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\input\lexer.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\conversions\to_chars.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\output\serializer.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\json_pointer.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\meta\type_traits.hpp` | 231 | 16.5 min | 91% |
| `include\nlohmann\detail\iterators\iter_impl.hpp` | 231 | 16.5 min | 91% |

> 口径：扫描期每个 TU 实测耗时之和（默认 `-fsyntax-only`，不含链接）。它是一个**预测**，预测误差与验证方法见 `docs/methodology.md` 的「增量构建税」一节。`0` 表示该单元没有可用计时，不是「瞬间完成」。

## 成本候选（按预计收益排序）

> **这张表说的是「贵」，不是「能删」。** 「独占闭包」指删掉这条 include 后，只有它会停止被解析的头文件集合——如果某个头文件还能从别的 include 到达，删掉当前这条并不省钱。这是本表与普通 lint 工具的区别。
>
> 「结论」列是明确判断（建议删除/保留/需人工判断），依据见下一节；「未实测」表示这一轮没有跑 `--probe`，此时不给结论——没测过就下判断是编造。

| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 判据 | 结论 |
|---|---|---:|---:|---:|---|---|---|
| `test\AFLDriverTest.cpp` | `c++\stdlib.h` | 16 | 4,598 | 61.1% | 疑似未使用 | — | 未实测 |
| `test\SimpleHashTest.cpp` | `c++\cstring` | 4 | 3,227 | 24.0% | 疑似未使用 | — | 未实测 |
| `test\ShrinkValueProfileTest.cpp` | `c++\cstdio` | 6 | 2,556 | 18.7% | 疑似未使用 | — | 未实测 |
| `test\ShrinkControlFlowTest.cpp` | `c++\cstdio` | 6 | 2,556 | 18.7% | 疑似未使用 | — | 未实测 |
| `test\ShrinkValueProfileTest.cpp` | `c++\cstdlib` | 12 | 2,325 | 17.0% | 疑似未使用 | — | 未实测 |
| `test\EmptyTest.cpp` | `c++\cstdlib` | 12 | 2,325 | 30.3% | 疑似未使用 | — | 未实测 |
| `test\ShrinkControlFlowTest.cpp` | `c++\cstdlib` | 12 | 2,325 | 17.0% | 疑似未使用 | — | 未实测 |
| `test\DSOTestMain.cpp` | `c++\cstdlib` | 12 | 2,325 | 17.3% | 疑似未使用 | — | 未实测 |
| `test\AccumulateAllocationsTest.cpp` | `c++\cstddef` | 2 | 2,877 | 35.0% | 疑似未使用 | — | 未实测 |
| `test\LeakTest.cpp` | `c++\cstddef` | 2 | 2,877 | 35.0% | 疑似未使用 | — | 未实测 |
| `macro_builder\main.cpp` | `c++\sstream` | 2 | 1,738 | 2.2% | 疑似未使用 | — | 未实测 |
| `test\LeakTimeoutTest.cpp` | `c++\cstddef` | 2 | 2,877 | 35.0% | 疑似未使用 | — | 未实测 |
| `test\TimeoutEmptyTest.cpp` | `c++\cstddef` | 2 | 2,877 | 35.0% | 疑似未使用 | — | 未实测 |
| `test\UninstrumentedTest.cpp` | `c++\cstddef` | 2 | 2,877 | 35.0% | 疑似未使用 | — | 未实测 |
| `Fuzzer\FuzzerMerge.cpp` | `c++\iterator` | 2 | 362 | 0.2% | 疑似未使用 | — | 未实测 |
| `test\OutOfMemorySingleLargeMallocTest.cpp` | `c++\cstring` | 3 | 549 | 0.7% | 疑似未使用 | — | 未实测 |
| `test\SimpleThreadedTest.cpp` | `c++\cstring` | 3 | 549 | 0.6% | 疑似未使用 | — | 未实测 |
| `Fuzzer\FuzzerIOWindows.cpp` | `c++\iterator` | 2 | 362 | 0.1% | 疑似未使用 | — | 未实测 |
| `Fuzzer\FuzzerIO.cpp` | `c++\iterator` | 2 | 362 | 0.3% | 疑似未使用 | — | 未实测 |
| `Fuzzer\FuzzerIOWindows.cpp` | `sys\stat.h` | 2 | 314 | 0.1% | 疑似未使用 | — | 未实测 |


### 待验证队列：669 条等 `verify` 裁定

顺序即 `heavyheader verify --top N` 的测量顺序：**疑似未使用**先测
（静态判定已经指向删除），**无法判定**次之（静态给不出结论，但反事实
实验能裁——Lua 语料上这一层 32/32 条实测可删），**在用**默认不测
（要测得加 `--ungated-sites`）。

- 疑似未使用 **48** 条 · 无法判定 **25** 条 · 在用 **596** 条

| 编译单元 | include | 预计省 (s) | 使用状态 | 判据 |
|---|---|---:|---|---|
| `test\AFLDriverTest.cpp` | `c++\stdlib.h` | 0.0305 | 疑似未使用 | — |
| `test\SimpleHashTest.cpp` | `c++\cstring` | 0.0216 | 疑似未使用 | — |
| `test\ShrinkValueProfileTest.cpp` | `c++\cstdio` | 0.0206 | 疑似未使用 | — |
| `test\ShrinkControlFlowTest.cpp` | `c++\cstdio` | 0.0187 | 疑似未使用 | — |
| `test\ShrinkValueProfileTest.cpp` | `c++\cstdlib` | 0.0187 | 疑似未使用 | — |
| `test\EmptyTest.cpp` | `c++\cstdlib` | 0.0182 | 疑似未使用 | — |
| `test\ShrinkControlFlowTest.cpp` | `c++\cstdlib` | 0.0170 | 疑似未使用 | — |
| `test\DSOTestMain.cpp` | `c++\cstdlib` | 0.0155 | 疑似未使用 | — |
| `test\AccumulateAllocationsTest.cpp` | `c++\cstddef` | 0.0140 | 疑似未使用 | — |
| `test\LeakTest.cpp` | `c++\cstddef` | 0.0140 | 疑似未使用 | — |

## 实测状态

**本轮没有做实测。** 上表全部是估算——按行数折算的预计收益，不是秒数，也不代表这些 include 删掉之后还能编译。

两个档位可选：`--probe-compile` 只验「删了还能不能编」（每候选一次编译）；`--probe` 在它之上再测耗时（两侧各 N 次取最小，判 improved 后还要复测）。

## 缺少 include guard 的头文件（必赚项）

这些头文件在**同一个编译单元内被重复解析**。对带 guard 的头文件来说这在物理上不可能发生，所以这是「缺少 guard」的确定性信号。补上 `#pragma once` 或 guard 宏永远安全，且收益立竿见影。

| 头文件 | 解析次数 | 涉及 TU | 多解析次数 |
|---|---:|---:|---:|
| `bits\version.h` | 12672 | 297 | 12375 |
| `bits\stdexcept_throw.h` | 1740 | 275 | 1465 |
| `include\stddef.h` | 1145 | 300 | 845 |
| `include\stddef.h` | 1145 | 300 | 845 |
| `c++\stdlib.h` | 1129 | 296 | 833 |
| `include\limits.h` | 599 | 296 | 303 |
| `include\_mingw.h` | 602 | 301 | 301 |
| `include\stdlib.h` | 592 | 296 | 296 |
| `include\stdio.h` | 588 | 294 | 294 |
| `include\string.h` | 556 | 278 | 278 |
| `include\corecrt_wconio.h` | 550 | 275 | 275 |
| `include\wchar.h` | 550 | 275 | 275 |
| `sys\timeb.h` | 532 | 266 | 266 |
| `include\rpc.h` | 66 | 2 | 64 |
| `include\rpcndr.h` | 64 | 2 | 62 |

## 模板实例化成本

全项目模板实例化合计 **3.2 min**，占总构建时间 **18.8%**。

> 每个编译单元的数字来自 `-ftime-report`，是精确的；下面「模板表面」一列是**文本启发式**（`template<` 声明数 × 引用它的 TU 数），用于提示成本可能来自哪里，不是实测归因。

**模板实例化占比超过 25% 的编译单元：**

- `examples\from_cbor.cpp`：6066.7%（1.82 s）
- `examples\basic_json__CompatibleType.cpp`：1754.5%（1.93 s）
- `Fuzzer\FuzzerUtilWindows.cpp`：511.1%（460 ms）
- `project\main.cpp`：43.1%（1.48 s）
- `examples\patch_inplace.cpp`：40.0%（1.71 s）
- `conan\example.cpp`：39.7%（2.02 s）
- `examples\swap__reference.cpp`：38.8%（1.63 s）
- `examples\operator__ValueType.cpp`：38.6%（1.73 s）
- `examples\patch.cpp`：38.5%（1.73 s）
- `examples\nlohmann_json_serialize_enum_2.cpp`：38.2%（1.76 s）
- `examples\to_json.cpp`：38.1%（1.66 s）
- `examples\get__ValueType_const.cpp`：37.7%（1.62 s）
- `examples\diff.cpp`：37.7%（1.79 s）
- `examples\object.cpp`：37.4%（1.58 s）
- `examples\parse__string__parser_callback_t.cpp`：37.2%（1.56 s）
- `examples\nlohmann_define_type_intrusive_macro.cpp`：37.1%（1.56 s）
- `examples\push_back__object_t__value.cpp`：36.9%（1.61 s）
- `examples\swap__string_t.cpp`：36.9%（1.63 s）
- `examples\operator_array__object_t_key_type.cpp`：36.8%（1.51 s）
- `examples\nlohmann_json_serialize_enum.cpp`：36.8%（1.58 s）
- `examples\operator_array__size_type.cpp`：36.7%（1.50 s）
- `examples\erase__size_type.cpp`：36.0%（1.61 s）
- `project\main.cpp`：35.9%（1.20 s）
- `project\main.cpp`：35.8%（1.14 s）
- `examples\rend.cpp`：35.8%（1.49 s）
- `examples\operator_array__keytype.c++17.cpp`：35.8%（1.55 s）
- `examples\from_msgpack.cpp`：35.8%（1.64 s）
- `examples\to_msgpack.cpp`：35.6%（1.19 s）
- `examples\insert__count.cpp`：35.6%（1.43 s）
- `examples\insert.cpp`：35.6%（1.45 s）
- `examples\is_string.cpp`：35.4%（1.45 s）
- `examples\operator_deserialize.cpp`：35.4%（1.49 s）
- `examples\insert__range.cpp`：35.3%（1.54 s）
- `examples\nlohmann_define_type_intrusive_only_serialize_macro.cpp`：35.1%（1.47 s）
- `examples\insert__ilist.cpp`：35.1%（1.54 s）
- `examples\swap__array_t.cpp`：35.1%（1.45 s）
- `examples\operator_array__size_type_const.cpp`：35.0%（1.43 s）
- `examples\json_lines.cpp`：34.9%（1.65 s）
- `project\main.cpp`：34.5%（1.15 s）
- `examples\merge_patch.cpp`：34.5%（1.42 s）
- `examples\operator_array__object_t_key_type_const.cpp`：34.2%（1.46 s）
- `examples\find__keytype.c++17.cpp`：34.1%（1.52 s）
- `examples\to_cbor.cpp`：34.0%（1.31 s）
- `examples\ordered_json.cpp`：34.0%（1.45 s）
- `project\Foo.cpp`：34.0%（1.08 s）
- `examples\operator_array__json_pointer_const.cpp`：33.8%（1.53 s）
- `examples\to_bjdata.cpp`：33.8%（1.29 s）
- `examples\front.cpp`：33.7%（1.26 s）
- `examples\json_base_class_t.cpp`：33.6%（1.48 s）
- `examples\README.cpp`：33.5%（1.77 s）
- `examples\emplace_back.cpp`：33.5%（1.44 s）
- `examples\operator_array__keytype_const.c++17.cpp`：33.4%（1.43 s）
- `examples\nlohmann_define_type_intrusive_only_serialize_explicit.cpp`：33.3%（1.58 s）
- `examples\at__object_t_key_type_const.cpp`：33.3%（1.66 s）
- `project\Bar.cpp`：33.2%（1.01 s）
- `examples\diagnostics_extended.cpp`：33.2%（1.41 s）
- `examples\nlohmann_define_type_non_intrusive_only_serialize_explicit.cpp`：33.2%（1.26 s）
- `examples\items.cpp`：33.0%（1.54 s）
- `examples\erase__object_t_key_type.cpp`：32.9%（1.55 s）
- `diag\diag_off.cpp`：32.9%（1.34 s）
- `examples\parse__iterator_pair.cpp`：32.8%（1.48 s）
- `examples\value__json_ptr.cpp`：32.8%（1.25 s）
- `examples\parse__array__parser_callback_t.cpp`：32.6%（1.41 s）
- `examples\get_allocator.cpp`：32.5%（1.22 s）
- `examples\swap__binary_t.cpp`：32.5%（1.32 s）
- `examples\is_number.cpp`：32.4%（1.50 s）
- `examples\cbor_tag_handler_t.cpp`：32.4%（1.49 s）
- `examples\is_boolean.cpp`：32.3%（1.39 s）
- `examples\value__object_t_key_type.cpp`：32.2%（1.35 s）
- `project\main.cpp`：32.1%（970 ms）
- `examples\is_object.cpp`：32.1%（1.19 s）
- `examples\nlohmann_json_namespace_begin.c++17.cpp`：32.1%（1.38 s）
- `examples\parse__pointers.cpp`：31.9%（1.33 s）
- `examples\is_primitive.cpp`：31.9%（1.30 s）
- `examples\from_json__non_default_constructible.cpp`：31.8%（1.18 s）
- `diag\diag_on.cpp`：31.1%（1.26 s）
- `examples\basic_json__list_init_t.cpp`：31.1%（1.51 s）
- `examples\erase__IteratorType.cpp`：31.0%（1.34 s）
- `examples\get_ptr.cpp`：30.8%（1.28 s）
- `examples\basic_json__basic_json.cpp`：30.8%（1.52 s）
- `examples\error_handler_t.cpp`：30.6%（1.26 s）
- `examples\clear.cpp`：30.6%（1.50 s）
- `examples\is_discarded.cpp`：30.6%（1.34 s）
- `examples\at__object_t_key_type.cpp`：30.5%（1.62 s）
- `examples\cbegin.cpp`：30.3%（1.44 s）
- `examples\number_integer_t.cpp`：30.3%（1.13 s）
- `examples\at__size_type_const.cpp`：30.2%（1.59 s）
- `examples\max_size.cpp`：30.2%（1.27 s）
- `examples\dump.cpp`：30.2%（1.39 s）
- `examples\number_float_t.cpp`：30.2%（1.13 s）
- `examples\object_t.cpp`：30.1%（1.18 s）
- `examples\json_pointer__to_string.cpp`：29.8%（1.33 s）
- `project\main.cpp`：29.6%（1.16 s）
- `examples\basic_json__moveconstructor.cpp`：29.6%（1.40 s）
- `examples\erase__keytype.c++17.cpp`：29.6%（1.28 s）
- `examples\back.cpp`：29.5%（1.54 s）
- `examples\byte_container_with_subtype__set_subtype.cpp`：29.4%（1.34 s）
- `examples\parse_error.cpp`：29.4%（1.04 s）
- `examples\std_hash.cpp`：29.3%（1.18 s）
- `examples\nlohmann_json_version.cpp`：29.2%（990 ms）
- `examples\cend.cpp`：29.2%（1.40 s）
- `examples\at__keytype_const.c++17.cpp`：29.0%（1.52 s）
- `src\fuzzer-parse_json.cpp`：29.0%（1.18 s）
- `examples\exception.cpp`：28.9%（1.19 s）
- `examples\basic_json__value_t.cpp`：28.9%（1.46 s）
- `examples\at__json_pointer.cpp`：28.8%（1.36 s）
- `examples\object_comparator_t.cpp`：28.7%（1.11 s）
- `examples\count__object_t_key_type.cpp`：28.7%（1.28 s）
- `examples\empty.cpp`：28.6%（1.22 s）
- `examples\byte_container_with_subtype__byte_container_with_subtype.cpp`：28.2%（1.39 s）
- `examples\contains__object_t_key_type.cpp`：28.1%（1.11 s）
- `examples\at__keytype.c++17.cpp`：28.1%（1.38 s）
- `examples\basic_json__nullptr_t.cpp`：28.1%（1.29 s）
- `examples\byte_container_with_subtype__clear_subtype.cpp`：28.0%（1.17 s）
- `examples\is_number_unsigned.cpp`：27.7%（1.02 s）
- `examples\json_pointer__pop_back.cpp`：27.7%（1.04 s）
- `examples\array_t.cpp`：27.4%（1.29 s）
- `examples\get_ref.cpp`：27.4%（950 ms）
- `examples\contains__json_pointer.cpp`：27.3%（1.24 s）
- `examples\json_pointer__back.cpp`：27.2%（1.27 s）
- `examples\json_pointer__operator_add_binary.cpp`：27.2%（1.18 s）
- `examples\contains__keytype.c++17.cpp`：27.1%（1.24 s）
- `examples\get_binary.cpp`：27.1%（920 ms）
- `examples\get__PointerType.cpp`：27.0%（990 ms）
- `examples\is_number_integer.cpp`：27.0%（1.08 s）
- `examples\count__keytype.c++17.cpp`：26.9%（1.20 s）
- `examples\begin.cpp`：26.5%（1.15 s）
- `examples\json_pointer__operator_add.cpp`：26.4%（1.09 s）
- `examples\nlohmann_json_namespace.cpp`：26.3%（1.08 s）
- `examples\boolean_t.cpp`：26.3%（1.16 s）
- `examples\json_pointer__parent_pointer.cpp`：25.6%（980 ms）
- `examples\json_pointer__push_back.cpp`：25.5%（980 ms）
- `examples\byte_container_with_subtype__subtype.cpp`：25.4%（1.10 s）

| 头文件 | template< 声明数 | 被引用 TU | 模板表面 |
|---|---:|---:|---:|
| `include\nlohmann\detail\meta\type_traits.hpp` | 113 | 231 | 26103 |
| `include\nlohmann\json.hpp` | 109 | 231 | 25179 |
| `include\nlohmann\detail\conversions\to_json.hpp` | 48 | 231 | 11088 |
| `include\nlohmann\detail\conversions\from_json.hpp` | 32 | 231 | 7392 |
| `include\nlohmann\detail\json_pointer.hpp` | 28 | 231 | 6468 |
| `include\nlohmann\detail\string_concat.hpp` | 22 | 231 | 5082 |
| `include\nlohmann\detail\input\input_adapters.hpp` | 18 | 231 | 4158 |
| `include\nlohmann\detail\meta\detected.hpp` | 18 | 231 | 4158 |
| `include\nlohmann\detail\meta\cpp_future.hpp` | 17 | 231 | 3927 |
| `include\nlohmann\detail\meta\is_sax.hpp` | 15 | 231 | 3465 |

模板表面大、又被大量 TU 引用的头文件，是模板实例化成本的主要嫌疑对象。这是**调查线索，不是结论**——编译器只报告阶段总量，不做按头文件的归因。系统头文件（如 `<type_traits>`）的模板声明数通常更高，但用户改不了，故未列入。

## 预编译头（PCH）收益估算

建议将下列 5 个头文件纳入 PCH。它们的传递闭包共 **204,269 行**，其中 **231/304** 个编译单元已全部包含它们（覆盖率 76%）。

| 头文件 | 行数 | 被引用 TU |
|---|---:|---:|
| `include\nlohmann\json.hpp` | 5,258 | 231 |
| `include\nlohmann\adl_serializer.hpp` | 55 | 231 |
| `include\nlohmann\detail\conversions\from_json.hpp` | 497 | 231 |
| `include\nlohmann\detail\meta\std_fs.hpp` | 29 | 231 |
| `include\nlohmann\detail\conversions\to_json.hpp` | 446 | 231 |

**估算净收益：46,981,870 行（占全项目头文件解析量 90.4%）——值得做。**

> 这是估算，不是承诺。四条限制：
> - 基于行数代理，不是秒数。模板重型头文件的实际成本会被低估。
> - 上限估计：真实 PCH 必须是每个使用它的 TU 的第一个 include，且各 TU 间字节一致。include 顺序不一致的项目实际收益会更低。
> - 未计入 PCH 失效：改动 PCH 中任何一个头文件都会让所有 TU 的 PCH 作废，因此 PCH 只应放稳定的头文件（第三方库、极少改动的内部基础库）。
> - 本工具不生成 PCH，只做估算。

## 编译失败的单元

- `config\custom.cpp`：experiments\_work\json\tests\abi\config\custom.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `config\default.cpp`：experiments\_work\json\tests\abi\config\default.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `config\noversion.cpp`：experiments\_work\json\tests\abi\config\noversion.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `diag\diag.cpp`：experiments\_work\json\tests\abi\diag\diag.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `inline_ns\use_current.cpp`：experiments\_work\json\tests\abi\inline_ns\use_current.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `inline_ns\use_v3_10_5.cpp`：experiments\_work\json\tests\abi\inline_ns\use_v3_10_5.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `abi\main.cpp`：experiments\_work\json\tests\abi\main.cpp:11:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\benchmarks.cpp`：experiments\_work\json\tests\benchmarks\src\benchmarks.cpp:9:10: fatal error: benchmark/benchmark.h: No such file or directory
- `src\unit-32bit.cpp`：experiments\_work\json\tests\src\unit-32bit.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-algorithms.cpp`：experiments\_work\json\tests\src\unit-algorithms.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-allocator.cpp`：experiments\_work\json\tests\src\unit-allocator.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-alt-string.cpp`：experiments\_work\json\tests\src\unit-alt-string.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-assert_macro.cpp`：experiments\_work\json\tests\src\unit-assert_macro.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-binary_formats.cpp`：experiments\_work\json\tests\src\unit-binary_formats.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-bjdata.cpp`：experiments\_work\json\tests\src\unit-bjdata.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-bson.cpp`：experiments\_work\json\tests\src\unit-bson.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-byte_container_with_subtype.cpp`：experiments\_work\json\tests\src\unit-byte_container_with_subtype.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-capacity.cpp`：experiments\_work\json\tests\src\unit-capacity.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-cbor.cpp`：experiments\_work\json\tests\src\unit-cbor.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-class_const_iterator.cpp`：experiments\_work\json\tests\src\unit-class_const_iterator.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-class_iterator.cpp`：experiments\_work\json\tests\src\unit-class_iterator.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-class_lexer.cpp`：experiments\_work\json\tests\src\unit-class_lexer.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-class_parser.cpp`：experiments\_work\json\tests\src\unit-class_parser.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-comparison.cpp`：experiments\_work\json\tests\src\unit-comparison.cpp:16:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-concepts.cpp`：experiments\_work\json\tests\src\unit-concepts.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-constructor1.cpp`：experiments\_work\json\tests\src\unit-constructor1.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-constructor2.cpp`：experiments\_work\json\tests\src\unit-constructor2.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-convenience.cpp`：experiments\_work\json\tests\src\unit-convenience.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-conversions.cpp`：experiments\_work\json\tests\src\unit-conversions.cpp:17:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-custom-base-class.cpp`：experiments\_work\json\tests\src\unit-custom-base-class.cpp:14:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-deserialization.cpp`：experiments\_work\json\tests\src\unit-deserialization.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-diagnostics.cpp`：experiments\_work\json\tests\src\unit-diagnostics.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-disabled_exceptions.cpp`：experiments\_work\json\tests\src\unit-disabled_exceptions.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-element_access1.cpp`：experiments\_work\json\tests\src\unit-element_access1.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-element_access2.cpp`：experiments\_work\json\tests\src\unit-element_access2.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-hash.cpp`：experiments\_work\json\tests\src\unit-hash.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-inspection.cpp`：experiments\_work\json\tests\src\unit-inspection.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-items.cpp`：experiments\_work\json\tests\src\unit-items.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-iterators1.cpp`：experiments\_work\json\tests\src\unit-iterators1.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-iterators2.cpp`：experiments\_work\json\tests\src\unit-iterators2.cpp:16:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-json_patch.cpp`：experiments\_work\json\tests\src\unit-json_patch.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-json_pointer.cpp`：experiments\_work\json\tests\src\unit-json_pointer.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-large_json.cpp`：experiments\_work\json\tests\src\unit-large_json.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-merge_patch.cpp`：experiments\_work\json\tests\src\unit-merge_patch.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-meta.cpp`：experiments\_work\json\tests\src\unit-meta.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-modifiers.cpp`：experiments\_work\json\tests\src\unit-modifiers.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-msgpack.cpp`：experiments\_work\json\tests\src\unit-msgpack.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-no-mem-leak-on-adl-serialize.cpp`：experiments\_work\json\tests\src\unit-no-mem-leak-on-adl-serialize.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-noexcept.cpp`：experiments\_work\json\tests\src\unit-noexcept.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-ordered_json.cpp`：experiments\_work\json\tests\src\unit-ordered_json.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-ordered_map.cpp`：experiments\_work\json\tests\src\unit-ordered_map.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-pointer_access.cpp`：experiments\_work\json\tests\src\unit-pointer_access.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-readme.cpp`：experiments\_work\json\tests\src\unit-readme.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-reference_access.cpp`：experiments\_work\json\tests\src\unit-reference_access.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-regression1.cpp`：experiments\_work\json\tests\src\unit-regression1.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-regression2.cpp`：experiments\_work\json\tests\src\unit-regression2.cpp:16:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-serialization.cpp`：experiments\_work\json\tests\src\unit-serialization.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-testsuites.cpp`：experiments\_work\json\tests\src\unit-testsuites.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-to_chars.cpp`：experiments\_work\json\tests\src\unit-to_chars.cpp:13:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-type_traits.cpp`：experiments\_work\json\tests\src\unit-type_traits.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-ubjson.cpp`：experiments\_work\json\tests\src\unit-ubjson.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-udl.cpp`：experiments\_work\json\tests\src\unit-udl.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-udt.cpp`：experiments\_work\json\tests\src\unit-udt.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-udt_macro.cpp`：experiments\_work\json\tests\src\unit-udt_macro.cpp:11:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-unicode1.cpp`：experiments\_work\json\tests\src\unit-unicode1.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-unicode2.cpp`：experiments\_work\json\tests\src\unit-unicode2.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-unicode3.cpp`：experiments\_work\json\tests\src\unit-unicode3.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-unicode4.cpp`：experiments\_work\json\tests\src\unit-unicode4.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-unicode5.cpp`：experiments\_work\json\tests\src\unit-unicode5.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-user_defined_input.cpp`：experiments\_work\json\tests\src\unit-user_defined_input.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-windows_h.cpp`：experiments\_work\json\tests\src\unit-windows_h.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit-wstring.cpp`：experiments\_work\json\tests\src\unit-wstring.cpp:9:10: fatal error: doctest_compatibility.h: No such file or directory
- `src\unit.cpp`：experiments\_work\json\tests\src\unit.cpp:10:10: fatal error: doctest_compatibility.h: No such file or directory
- `Fuzzer\FuzzerSHA1.cpp`：experiments\_work\json\tests\thirdparty\Fuzzer\FuzzerSHA1.cpp:42:11: fatal error: endian.h: No such file or directory
- `Fuzzer\FuzzerTracePC.cpp`：experiments\_work\json\tests\thirdparty\Fuzzer\FuzzerTracePC.cpp:23:10: fatal error: sanitizer/coverage_interface.h: No such file or directory
- `afl\afl_driver.cpp`：experiments\_work\json\tests\thirdparty\Fuzzer\afl\afl_driver.cpp:60:10: fatal error: sys/resource.h: No such file or directory
- `test\CounterTest.cpp`：experiments\_work\json\tests\thirdparty\Fuzzer\test\CounterTest.cpp:8:45: error: 'uint8_t' does not name a type
- `test\CustomCrossOverTest.cpp`：experiments\_work\json\tests\thirdparty\Fuzzer\test\CustomCrossOverTest.cpp:13:10: fatal error: FuzzerInterface.h: No such file or directory
- `test\CustomMutatorTest.cpp`：experiments\_work\json\tests\thirdparty\Fuzzer\test\CustomMutatorTest.cpp:11:10: fatal error: FuzzerInterface.h: No such file or directory
- `test\FuzzerUnittest.cpp`：experiments\_work\json\tests\thirdparty\Fuzzer\test\FuzzerUnittest.cpp:8:10: fatal error: FuzzerCorpus.h: No such file or directory

## 扫描警告

- experiments\_work\json\tests\thirdparty\Fuzzer\FuzzerExtFunctions.def: parsed 12x across 10 TU(s) -- missing include guard?
- experiments\_work\json\tests\thirdparty\Fuzzer\FuzzerFlags.def: parsed 2x across 1 TU(s) -- missing include guard?
- experiments\_work\json\docs\examples\README.cpp: discarded impossible timing (phase parsing=18446744072.33s (exceeds measured wall 5.28s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\README.cpp: discarded impossible timing (parser inl. meth. body=18446744069.83s (exceeds measured wall 5.28s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\README.cpp: discarded impossible timing (TOTAL=18446744073.56s (exceeds measured wall 5.28s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\README.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\accept__string.cpp: discarded impossible timing (phase parsing=18446744071.82s (exceeds measured wall 4.65s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\accept__string.cpp: discarded impossible timing (|name lookup=18446744069.62s (exceeds measured wall 4.65s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\accept__string.cpp: discarded impossible timing (parser inl. meth. body=18446744069.73s (exceeds measured wall 4.65s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\accept__string.cpp: discarded impossible timing (TOTAL=18446744072.75s (exceeds measured wall 4.65s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\accept__string.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array.cpp: discarded impossible timing (phase parsing=18446744072.38s (exceeds measured wall 5.17s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array.cpp: discarded impossible timing (template instantiation=18446744070.92s (exceeds measured wall 5.17s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array.cpp: discarded impossible timing (TOTAL=18446744073.39s (exceeds measured wall 5.17s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array_t.cpp: discarded impossible timing (phase parsing=18446744071.84s (exceeds measured wall 4.70s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array_t.cpp: discarded impossible timing (parser inl. meth. body=18446744069.83s (exceeds measured wall 4.70s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array_t.cpp: discarded impossible timing (TOTAL=18446744072.98s (exceeds measured wall 4.70s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\array_t.cpp: discarded impossible timing (TOTAL 行不可用，改用驱动实测墙钟（编译器自报计时损坏）) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable
- experiments\_work\json\docs\examples\at__json_pointer.cpp: discarded impossible timing (phase parsing=18446744072.12s (exceeds measured wall 4.71s)) -- the compiler's -ftime-report timer is unreliable here; remaining timings are still usable

---

由 [HeavyHeader](https://github.com/wzzrambler/heavyheader) 生成 · 指标定义见 `docs/methodology.md`
