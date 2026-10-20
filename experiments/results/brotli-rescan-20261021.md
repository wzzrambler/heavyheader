# HeavyHeader 构建成本报告

- 项目根目录：`C:\Users\31879\AppData\Local\Temp\brotli-1.1.0`
- 编译器：`C:/Program Files/LLVM/bin/clang.exe`（clang）
- 编译单元：32 成功 / 0 失败
- 头文件依赖边：5,603
- 全项目头文件解析量：**2,948,219 行**
- 唯一文件行数：139,314 行
- **Blowup Factor：21.2×**（平均每行被解析的次数；与 Header Hero 同定义，见 `docs/competitors.md`）
- 累计编译耗时：33.26 s

## 成本热点 Top 15

按「归因行数」排序，即 `头文件行数 × 引用它的编译单元数`——
它衡量的是这个头文件让整个项目多解析了多少行代码。

| 头文件 | 行数 | 被引用 TU | 归因行数 | 传递闭包 | 最大深度 |
|---|---:|---:|---:|---:|---:|
| `c\enc\static_dict_lut.h` | 5,866 | 15 | 87,990 | 0 | 6 |
| `c\common\platform.h` | 541 | 30 | 16,230 | 131 | 2 |
| `c\include\brotli\port.h` | 305 | 32 | 9,760 | 0 | 2 |
| `c\include\brotli\encode.h` | 501 | 15 | 7,515 | 0 | 5 |
| `c\common\constants.h` | 201 | 22 | 4,422 | 132 | 3 |
| `c\enc\hash.h` | 728 | 4 | 2,912 | 12 | 1 |
| `c\include\brotli\types.h` | 83 | 32 | 2,656 | 10 | 3 |
| `c\enc\quality.h` | 202 | 13 | 2,626 | 0 | 3 |
| `c\enc\encoder_dict.h` | 157 | 15 | 2,355 | 5 | 5 |
| `c\enc\command.h` | 191 | 12 | 2,292 | 13 | 3 |
| `c\enc\memory.h` | 131 | 17 | 2,227 | 0 | 7 |
| `c\include\brotli\shared_dictionary.h` | 100 | 19 | 1,900 | 11 | 6 |
| `c\common\context.h` | 113 | 13 | 1,469 | 1 | 2 |
| `c\enc\hash_to_binary_tree_inc.h` | 330 | 4 | 1,320 | 0 | 2 |
| `c\dec\bit_reader.h` | 423 | 3 | 1,269 | 0 | 1 |

## 实验性：增量构建税（未校准，勿据此决策）

上一节回答「整个项目为它付了多少解析量」；这一节试答「**你改它一行，下一次增量构建要等多久**」。这是编辑循环里的实际痛感，也是现有竞品没有给出的视角——但它目前**只是原型**：受控实验（`experiments/incremental_tax.py`）显示预测比实测**偏高约 1.7×**，因为扫描的 `--full-compile` 会为每个单元额外链接一次，而真实增量构建只编译不链接。校准完成前，本表不得作为结论引用。

| 头文件 | 重编 TU | 预计等待 | 占单次全量构建 |
|---|---:|---:|---:|
| `c\include\brotli\port.h` | 32 | 33.26 s | 100% |
| `c\include\brotli\types.h` | 32 | 33.26 s | 100% |
| `c\common\platform.h` | 30 | 32.09 s | 96% |
| `c\common\constants.h` | 22 | 24.96 s | 75% |
| `c\common\dictionary.h` | 19 | 22.86 s | 69% |
| `c\include\brotli\shared_dictionary.h` | 19 | 22.32 s | 67% |
| `c\enc\memory.h` | 17 | 20.13 s | 61% |
| `c\enc\static_dict_lut.h` | 15 | 18.25 s | 55% |
| `c\enc\encoder_dict.h` | 15 | 18.25 s | 55% |
| `c\enc\compound_dictionary.h` | 15 | 18.25 s | 55% |

> 口径：扫描期每个 TU 实测耗时之和（默认 `-fsyntax-only`，不含链接）。它是一个**预测**，预测误差与验证方法见 `docs/methodology.md` 的「增量构建税」一节。`0` 表示该单元没有可用计时，不是「瞬间完成」。

## 成本候选（按预计收益排序）

> **这张表说的是「贵」，不是「能删」。** 「独占闭包」指删掉这条 include 后，只有它会停止被解析的头文件集合——如果某个头文件还能从别的 include 到达，删掉当前这条并不省钱。这是本表与普通 lint 工具的区别。
>
> 「结论」列是明确判断（建议删除/保留/需人工判断），依据见下一节；「未实测」表示这一轮没有跑 `--probe`，此时不给结论——没测过就下判断是编造。

| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 判据 | 结论 |
|---|---|---:|---:|---:|---|---|---|
| `c\enc\compound_dictionary.c` | `c\enc\quality.h` | 6 | 6,837 | 6.9% | 疑似未使用 | bucket_bits、compound 由本文件自己定义 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\dictionary_hash.h` | 1 | 25 | 0.0% | 疑似未使用 | — | 未实测 |
| `c\enc\dictionary_hash.c` | `c\enc\dictionary_hash.h` | 1 | 25 | 0.0% | 疑似未使用 | kStaticDictionaryHashLengths、kStaticDictionaryHashWords 由本文件自己定义 | 未实测 |
| `c\enc\literal_cost.c` | `c\enc\literal_cost.h` | 1 | 32 | 0.0% | 疑似未使用 | BrotliEstimateBitCostsForLiterals、cost 由本文件自己定义 | 未实测 |
| `c\enc\dictionary_hash.c` | `c\enc\..\common\platform.h` | 146 | 86,900 | 94.1% | 无法判定 | BROTLI_INTERNAL 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `c\enc\block_splitter.c` | `c\enc\block_splitter_inc.h` | 1 | 481 | 0.5% | 无法判定 | 实现片段（block_splitter_inc.h 无 include guard）的第 1/3 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\block_splitter.c` | `c\enc\block_splitter_inc.h` | 1 | 481 | 0.5% | 无法判定 | 实现片段（block_splitter_inc.h 无 include guard）的第 2/3 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\block_splitter.c` | `c\enc\block_splitter_inc.h` | 1 | 481 | 0.5% | 无法判定 | 实现片段（block_splitter_inc.h 无 include guard）的第 3/3 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\tools\brotli.c` | `ucrt\io.h` | 3 | 855 | 0.8% | 无法判定 | _isatty、_setmode 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 1/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 2/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 3/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 4/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 5/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 6/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 7/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 8/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 9/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 10/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 1 | 189 | 0.2% | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 11/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 | 未实测 |


### 待验证队列：110 条等 `verify` 裁定

顺序即 `heavyheader verify --top N` 的测量顺序：**疑似未使用**先测
（静态判定已经指向删除），**无法判定**次之（静态给不出结论，但反事实
实验能裁——Lua 语料上这一层 32/32 条实测可删），**在用**默认不测
（要测得加 `--ungated-sites`）。

- 疑似未使用 **4** 条 · 无法判定 **37** 条 · 在用 **69** 条

| 编译单元 | include | 预计省 (s) | 使用状态 | 判据 |
|---|---|---:|---|---|
| `c\enc\compound_dictionary.c` | `c\enc\quality.h` | 0.0498 | 疑似未使用 | bucket_bits、compound 由本文件自己定义 |
| `c\enc\backward_references.c` | `c\enc\dictionary_hash.h` | 0.0002 | 疑似未使用 | — |
| `c\enc\dictionary_hash.c` | `c\enc\dictionary_hash.h` | 0.0001 | 疑似未使用 | kStaticDictionaryHashLengths、kStaticDictionaryHashWords 由本文件自己定义 |
| `c\enc\literal_cost.c` | `c\enc\literal_cost.h` | 0.0001 | 疑似未使用 | BrotliEstimateBitCostsForLiterals、cost 由本文件自己定义 |
| `c\enc\dictionary_hash.c` | `c\enc\..\common\platform.h` | 0.4669 | 无法判定 | BROTLI_INTERNAL 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 |
| `c\enc\block_splitter.c` | `c\enc\block_splitter_inc.h` | 0.0037 | 无法判定 | 实现片段（block_splitter_inc.h 无 include guard）的第 1/3 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 |
| `c\enc\block_splitter.c` | `c\enc\block_splitter_inc.h` | 0.0037 | 无法判定 | 实现片段（block_splitter_inc.h 无 include guard）的第 2/3 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 |
| `c\enc\block_splitter.c` | `c\enc\block_splitter_inc.h` | 0.0037 | 无法判定 | 实现片段（block_splitter_inc.h 无 include guard）的第 3/3 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 |
| `c\tools\brotli.c` | `ucrt\io.h` | 0.0025 | 无法判定 | _isatty、_setmode 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 |
| `c\enc\backward_references.c` | `c\enc\backward_references_inc.h` | 0.0015 | 无法判定 | 实现片段（backward_references_inc.h 无 include guard）的第 1/19 次实例化：每次包含生成一组不同的符号，静态判据分不清这一处生成的是哪一组，必须由实验裁决 |

## 实测状态

**本轮没有做实测。** 上表全部是估算——按行数折算的预计收益，不是秒数，也不代表这些 include 删掉之后还能编译。

两个档位可选：`--probe-compile` 只验「删了还能不能编」（每候选一次编译）；`--probe` 在它之上再测耗时（两侧各 N 次取最小，判 improved 后还要复测）。

## 缺少 include guard 的头文件（必赚项）

这些头文件在**同一个编译单元内被重复解析**。对带 guard 的头文件来说这在物理上不可能发生，所以这是「缺少 guard」的确定性信号。补上 `#pragma once` 或 guard 宏永远安全，且收益立竿见影。

| 头文件 | 解析次数 | 涉及 TU | 多解析次数 |
|---|---:|---:|---:|
| `include\__stddef_offsetof.h` | 62 | 32 | 30 |
| `include\__stddef_ptrdiff_t.h` | 62 | 32 | 30 |
| `include\__stddef_size_t.h` | 62 | 32 | 30 |
| `include\__stddef_wchar_t.h` | 62 | 32 | 30 |
| `include\fma4intrin.h` | 60 | 30 | 30 |
| `include\stddef.h` | 62 | 32 | 30 |
| `include\xmmintrin.h` | 60 | 30 | 30 |
| `c\enc\backward_references_inc.h` | 19 | 1 | 18 |
| `c\enc\histogram_inc.h` | 27 | 9 | 18 |
| `c\enc\hash_longest_match_quickly_inc.h` | 16 | 4 | 12 |
| `c\enc\cluster_inc.h` | 12 | 3 | 9 |
| `c\enc\hash_composite_inc.h` | 12 | 4 | 8 |
| `c\enc\hash_forgetful_chain_inc.h` | 12 | 4 | 8 |
| `c\enc\hash_rolling_inc.h` | 8 | 4 | 4 |
| `c\enc\bit_cost_inc.h` | 3 | 1 | 2 |

## 扫描警告

- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\backward_references_inc.h: parsed 19x across 1 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\histogram_inc.h: parsed 27x across 9 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\hash_longest_match_quickly_inc.h: parsed 16x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\cluster_inc.h: parsed 12x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\hash_forgetful_chain_inc.h: parsed 12x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\hash_composite_inc.h: parsed 12x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\hash_rolling_inc.h: parsed 8x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\bit_cost_inc.h: parsed 3x across 1 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\block_splitter_inc.h: parsed 3x across 1 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\block_encoder_inc.h: parsed 3x across 1 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\metablock_inc.h: parsed 3x across 1 TU(s) -- missing include guard?

---

由 [HeavyHeader](https://github.com/wzzrambler/heavyheader) 生成 · 指标定义见 `docs/methodology.md`
