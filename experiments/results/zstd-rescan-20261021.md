# HeavyHeader 构建成本报告

- 项目根目录：`C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib`
- 编译器：`C:/Program Files/LLVM/bin/clang.exe`（clang）
- 编译单元：39 成功 / 0 失败
- 头文件依赖边：7,366
- 全项目头文件解析量：**3,511,660 行**
- 唯一文件行数：174,207 行
- **Blowup Factor：20.2×**（平均每行被解析的次数；与 Header Hero 同定义，见 `docs/competitors.md`）
- 累计编译耗时：40.46 s

## 成本热点 Top 15

按「归因行数」排序，即 `头文件行数 × 引用它的编译单元数`——
它衡量的是这个头文件让整个项目多解析了多少行代码。

| 头文件 | 行数 | 被引用 TU | 归因行数 | 传递闭包 | 最大深度 |
|---|---:|---:|---:|---:|---:|
| `common\xxhash.h` | 7,020 | 20 | 140,400 | 10 | 3 |
| `zstd.h` | 3,089 | 22 | 67,958 | 8 | 4 |
| `compress\zstd_compress_internal.h` | 1,534 | 11 | 16,874 | 12 | 1 |
| `common\compiler.h` | 450 | 34 | 15,300 | 140 | 3 |
| `common\fse.h` | 640 | 22 | 14,080 | 13 | 4 |
| `common\mem.h` | 426 | 24 | 10,224 | 163 | 4 |
| `common\bitstream.h` | 457 | 22 | 10,054 | 12 | 4 |
| `compress\zstd_cwksp.h` | 748 | 11 | 8,228 | 11 | 2 |
| `common\zstd_internal.h` | 392 | 18 | 7,056 | 149 | 2 |
| `common\huf.h` | 286 | 20 | 5,720 | 14 | 3 |
| `common\error_private.h` | 168 | 33 | 5,544 | 11 | 3 |
| `common\portability_macros.h` | 158 | 34 | 5,372 | 0 | 4 |
| `common\cpu.h` | 249 | 18 | 4,482 | 130 | 3 |
| `common\bits.h` | 200 | 22 | 4,400 | 0 | 5 |
| `common\debug.h` | 116 | 36 | 4,176 | 0 | 5 |

## 实验性：增量构建税（未校准，勿据此决策）

上一节回答「整个项目为它付了多少解析量」；这一节试答「**你改它一行，下一次增量构建要等多久**」。这是编辑循环里的实际痛感，也是现有竞品没有给出的视角——但它目前**只是原型**：受控实验（`experiments/incremental_tax.py`）显示预测比实测**偏高约 1.7×**，因为扫描的 `--full-compile` 会为每个单元额外链接一次，而真实增量构建只编译不链接。校准完成前，本表不得作为结论引用。

| 头文件 | 重编 TU | 预计等待 | 占单次全量构建 |
|---|---:|---:|---:|
| `common\debug.h` | 36 | 38.62 s | 95% |
| `common\compiler.h` | 34 | 37.28 s | 92% |
| `common\portability_macros.h` | 34 | 37.28 s | 92% |
| `common\zstd_deps.h` | 34 | 37.28 s | 92% |
| `common\error_private.h` | 33 | 35.97 s | 89% |
| `zstd_errors.h` | 33 | 35.97 s | 89% |
| `common\mem.h` | 24 | 27.31 s | 68% |
| `common\fse.h` | 22 | 25.12 s | 62% |
| `common\bitstream.h` | 22 | 25.12 s | 62% |
| `common\bits.h` | 22 | 25.12 s | 62% |

> 口径：扫描期每个 TU 实测耗时之和（默认 `-fsyntax-only`，不含链接）。它是一个**预测**，预测误差与验证方法见 `docs/methodology.md` 的「增量构建税」一节。`0` 表示该单元没有可用计时，不是「瞬间完成」。

## 成本候选（按预计收益排序）

> **这张表说的是「贵」，不是「能删」。** 「独占闭包」指删掉这条 include 后，只有它会停止被解析的头文件集合——如果某个头文件还能从别的 include 到达，删掉当前这条并不省钱。这是本表与普通 lint 工具的区别。
>
> 「结论」列是明确判断（建议删除/保留/需人工判断），依据见下一节；「未实测」表示这一轮没有跑 `--probe`，此时不给结论——没测过就下判断是编造。

| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 判据 | 结论 |
|---|---|---:|---:|---:|---|---|---|
| `deprecated\zbuff_common.c` | `deprecated\zbuff.h` | 2 | 3,303 | 3.5% | 疑似未使用 | ZBUFF_getErrorName、ZBUFF_isError 由本文件自己定义 | 未实测 |
| `dictBuilder\divsufsort.c` | `dictBuilder\divsufsort.h` | 1 | 67 | 0.5% | 疑似未使用 | num_indexes 由本文件自己定义 | 未实测 |
| `compress\zstd_double_fast.c` | `compress\zstd_double_fast.h` | 1 | 50 | 0.1% | 疑似未使用 | ZSTD_REP_NUM、ZSTD_compressBlock_doubleFast 由本文件自己定义 | 未实测 |
| `compress\zstd_fast.c` | `compress\zstd_fast.h` | 1 | 38 | 0.0% | 疑似未使用 | ZSTD_REP_NUM、ZSTD_compressBlock_fast 由本文件自己定义 | 未实测 |
| `decompress\zstd_decompress_block.c` | `decompress\zstd_ddict.h` | 1 | 44 | 0.0% | 疑似未使用 | dctx 由本文件自己定义 | 未实测 |
| `dictBuilder\zdict.c` | `dictBuilder\divsufsort.h` | 1 | 67 | 0.1% | 疑似未使用 | — | 未实测 |
| `common\pool.c` | `common\..\common\allocations.h` | 134 | 80,440 | 82.4% | 无法判定 | 疑似副作用：_STATIC_ASSERT(sizeof(unsigned int) <= _ALLOCA_S_MARKER_SIZE)（宏调用展开成声明还是执行看不出来，需编译器汇编校验） | 未实测 |
| `common\xxhash.c` | `common\xxhash.h` | 29 | 18,605 | 100.0% | 无法判定 | 疑似副作用：__ANNOTATION(SAL_useHeader(void))（宏调用展开成声明还是执行看不出来，需编译器汇编校验） | 未实测 |
| `dictBuilder\divsufsort.c` | `ucrt\stdio.h` | 3 | 4,750 | 34.5% | 无法判定 | NULL 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `compress\zstd_compress_superblock.c` | `compress\zstd_compress_superblock.h` | 2 | 3,121 | 2.8% | 无法判定 | ZSTD_CCtx_params、ZSTD_TARGETCBLOCKSIZE_MIN 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `decompress\zstd_ddict.c` | `decompress\zstd_ddict.h` | 2 | 3,133 | 2.9% | 无法判定 | ZSTD_dct_auto、ZSTD_dct_fullDict 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `common\pool.c` | `common\debug.h` | 1 | 116 | 0.1% | 无法判定 | assert 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `compress\zstd_opt.c` | `compress\zstd_opt.h` | 1 | 80 | 0.1% | 无法判定 | ZSTD_updateTree、iend 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `dictBuilder\divsufsort.c` | `ucrt\assert.h` | 1 | 50 | 0.4% | 无法判定 | assert 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |
| `compress\zstd_compress_literals.c` | `compress\zstd_compress_literals.h` | 179 | 107,777 | 100.0% | 正在使用 | CTable、DEBUGLEVEL 由 huf.h 提供 | 未实测 |
| `compress\zstd_compress_sequences.c` | `compress\zstd_compress_sequences.h` | 176 | 105,455 | 100.0% | 正在使用 | BIT_CStream_t、BIT_addBits 由 bitstream.h 提供 | 未实测 |
| `common\error_private.c` | `common\error_private.h` | 163 | 91,506 | 100.0% | 正在使用 | ERR_enum 由 error_private.h 提供 | 未实测 |
| `deprecated\zbuff_compress.c` | `deprecated\..\common\error_private.h` | 148 | 86,962 | 91.7% | 正在使用 | FORWARD_IF_ERROR 由 error_private.h 提供 | 未实测 |
| `deprecated\zbuff_common.c` | `deprecated\..\common\error_private.h` | 148 | 86,962 | 91.7% | 正在使用 | ERR_getErrorName、ERR_isError 由 error_private.h 提供 | 未实测 |
| `common\zstd_common.c` | `common\zstd_internal.h` | 10 | 12,922 | 12.3% | 正在使用 | ZSTD_VERSION_NUMBER、ZSTD_VERSION_STRING 由 zstd.h 提供 | 未实测 |


### 待验证队列：90 条等 `verify` 裁定

顺序即 `heavyheader verify --top N` 的测量顺序：**疑似未使用**先测
（静态判定已经指向删除），**无法判定**次之（静态给不出结论，但反事实
实验能裁——Lua 语料上这一层 32/32 条实测可删），**在用**默认不测
（要测得加 `--ungated-sites`）。

- 疑似未使用 **6** 条 · 无法判定 **8** 条 · 在用 **76** 条

| 编译单元 | include | 预计省 (s) | 使用状态 | 判据 |
|---|---|---:|---|---|
| `deprecated\zbuff_common.c` | `deprecated\zbuff.h` | 0.0127 | 疑似未使用 | ZBUFF_getErrorName、ZBUFF_isError 由本文件自己定义 |
| `dictBuilder\divsufsort.c` | `dictBuilder\divsufsort.h` | 0.0004 | 疑似未使用 | num_indexes 由本文件自己定义 |
| `compress\zstd_double_fast.c` | `compress\zstd_double_fast.h` | 0.0003 | 疑似未使用 | ZSTD_REP_NUM、ZSTD_compressBlock_doubleFast 由本文件自己定义 |
| `compress\zstd_fast.c` | `compress\zstd_fast.h` | 0.0002 | 疑似未使用 | ZSTD_REP_NUM、ZSTD_compressBlock_fast 由本文件自己定义 |
| `decompress\zstd_decompress_block.c` | `decompress\zstd_ddict.h` | 0.0002 | 疑似未使用 | dctx 由本文件自己定义 |
| `dictBuilder\zdict.c` | `dictBuilder\divsufsort.h` | 0.0002 | 疑似未使用 | — |
| `common\pool.c` | `common\..\common\allocations.h` | 0.4414 | 无法判定 | 疑似副作用：_STATIC_ASSERT(sizeof(unsigned int) <= _ALLOCA_S_MARKER_SIZE)（宏调用展开成声明还是执行看不出来，需编译器汇编校验） |
| `common\xxhash.c` | `common\xxhash.h` | 0.0666 | 无法判定 | 疑似副作用：__ANNOTATION(SAL_useHeader(void))（宏调用展开成声明还是执行看不出来，需编译器汇编校验） |
| `dictBuilder\divsufsort.c` | `ucrt\stdio.h` | 0.0252 | 无法判定 | NULL 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 |
| `compress\zstd_compress_superblock.c` | `compress\zstd_compress_superblock.h` | 0.0161 | 无法判定 | ZSTD_CCtx_params、ZSTD_TARGETCBLOCKSIZE_MIN 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 |

## 实测状态

**本轮没有做实测。** 上表全部是估算——按行数折算的预计收益，不是秒数，也不代表这些 include 删掉之后还能编译。

两个档位可选：`--probe-compile` 只验「删了还能不能编」（每候选一次编译）；`--probe` 在它之上再测耗时（两侧各 N 次取最小，判 improved 后还要复测）。

## 缺少 include guard 的头文件（必赚项）

这些头文件在**同一个编译单元内被重复解析**。对带 guard 的头文件来说这在物理上不可能发生，所以这是「缺少 guard」的确定性信号。补上 `#pragma once` 或 guard 宏永远安全，且收益立竿见影。

| 头文件 | 解析次数 | 涉及 TU | 多解析次数 |
|---|---:|---:|---:|
| `include\__stddef_offsetof.h` | 247 | 37 | 210 |
| `include\__stddef_ptrdiff_t.h` | 247 | 37 | 210 |
| `include\__stddef_size_t.h` | 247 | 37 | 210 |
| `include\__stddef_wchar_t.h` | 247 | 37 | 210 |
| `include\stddef.h` | 247 | 37 | 210 |
| `common\zstd_deps.h` | 141 | 34 | 107 |
| `include\intrin.h` | 95 | 34 | 61 |
| `include\fma4intrin.h` | 68 | 34 | 34 |
| `include\xmmintrin.h` | 68 | 34 | 34 |
| `zstd.h` | 56 | 22 | 34 |
| `common\fse.h` | 51 | 22 | 29 |
| `common\huf.h` | 26 | 20 | 6 |
| `common\xxhash.h` | 23 | 20 | 3 |
| `common\allocations.h` | 16 | 14 | 2 |
| `zdict.h` | 5 | 3 | 2 |

## 扫描警告

- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\compress\..\common\zstd_deps.h: parsed 72x across 13 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\decompress\..\common\zstd_deps.h: parsed 27x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\compress\..\common\fse.h: parsed 27x across 12 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\compress\..\common\..\zstd.h: parsed 22x across 10 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\dictBuilder\..\common\zstd_deps.h: parsed 14x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\common\zstd_deps.h: parsed 15x across 5 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\decompress\..\common\fse.h: parsed 12x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\decompress\..\common\huf.h: parsed 8x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\dictBuilder\..\common\fse.h: parsed 7x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\compress\..\zstd.h: parsed 7x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\deprecated\..\zstd.h: parsed 6x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\common\fse.h: parsed 5x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\compress\..\common\allocations.h: parsed 11x across 9 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\decompress\..\common\..\zstd.h: parsed 6x across 4 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\decompress\..\zstd.h: parsed 5x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\dictBuilder\..\common\..\zstd.h: parsed 5x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\dictBuilder\..\zdict.h: parsed 5x across 3 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\compress\..\common\huf.h: parsed 12x across 11 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\compress\..\common\xxhash.h: parsed 11x across 10 TU(s) -- missing include guard?
- C:\Users\31879\AppData\Local\Temp\zstd-1.5.6\lib\decompress\..\common\xxhash.h: parsed 5x across 4 TU(s) -- missing include guard?

---

由 [HeavyHeader](https://github.com/wzzrambler/heavyheader) 生成 · 指标定义见 `docs/methodology.md`
