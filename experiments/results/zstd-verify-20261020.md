# HeavyHeader 实测验证（每侧 3 次，取最小值）

## deprecated/zbuff.h ← zbuff_common.c
   状态：no-change — 基线 375 ms → 移除后 389 ms

## hist.h ← fse_compress.c
   状态：no-change — 差异在噪声范围内（-1 ms）

## dictBuilder/divsufsort.h ← divsufsort.c
   状态：no-change — 差异在噪声范围内（-0 ms）

## zstd_double_fast.h ← zstd_double_fast.c
   状态：no-change — 基线 400 ms → 移除后 414 ms

## zstd_fast.h ← zstd_fast.c
   状态：no-change — 差异在噪声范围内（-3 ms）

## dictBuilder/divsufsort.h ← zdict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-r64ql8lz\zdict.c:506:39: error: call to undeclared function 'divsufsort'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## decompress/zstd_ddict.h ← zstd_decompress_block.c
   状态：no-change — 基线 418 ms → 移除后 408 ms；复测未通过，各遍 percent_saved = 2.55% / -0.36%（阈值 2%）

## common/xxhash.h ← xxhash.c
   状态：codegen-changed — 移除后编译通过，但生成的代码与原来不同（重载解析或条件编译发生变化）

## stdio.h ← divsufsort.c
   状态：improved — 基线 225 ms → 移除后 215 ms
   实测节省：10 ms (4.4%)

## zstd_compress_superblock.h ← zstd_compress_superblock.c
   状态：no-change — 差异在噪声范围内（-1 ms）

## decompress/zstd_ddict.h ← zstd_ddict.c
   状态：no-change — 差异在噪声范围内（-6 ms）

## common/debug.h ← pool.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-y3i2as8o\pool.c:340:5: error: call to undeclared function 'assert'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_opt.h ← zstd_opt.c
   状态：no-change — 基线 391 ms → 移除后 411 ms

## assert.h ← divsufsort.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-_slzad0m\divsufsort.c:418:7: error: call to undeclared function 'assert'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_compress_sequences.h ← zstd_compress_sequences.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-nz2i3jy4\zstd_compress_sequences.c:46:43: error: unknown type name 'FSE_CTable'

## zstd_compress_literals.h ← zstd_compress_literals.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-8g6q327_\zstd_compress_literals.c:41:5: error: use of undeclared identifier 'BYTE'

## common/error_private.h ← error_private.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-797rbo7n\error_private.c:15:32: error: unknown type name 'ERR_enum'

## allocations.h ← pool.c
   状态：improved — 基线 384 ms → 移除后 213 ms
   实测节省：171 ms (44.5%)

## common/error_private.h ← zbuff_common.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-r78p3cl_\zbuff_common.c:23:51: error: call to undeclared function 'ERR_isError'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## common/error_private.h ← zbuff_compress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-l6_bm_mj\zbuff_compress.c:77:5: error: call to undeclared function 'FORWARD_IF_ERROR'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## common/zstd_internal.h ← zstd_common.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-efefgwm7\zstd_common.c:24:44: error: use of undeclared identifier 'ZSTD_VERSION_NUMBER'

## common/zstd_internal.h ← huf_decompress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-gmdd14wc\huf_decompress.c:407:36: error: call to undeclared function 'MIN'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## common/zstd_internal.h ← cover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-xn68oa8p\cover.c:477:20: error: call to undeclared function 'MIN'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_decompress_internal.h ← zstd_ddict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-flodfbj0\zstd_ddict.c:40:5: error: unknown type name 'ZSTD_entropyDTables_t'

## common/xxhash.h ← zstd_v07.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-m477cj_d\zstd_v07.c:2891:5: error: unknown type name 'XXH64_state_t'

## compress/zstd_compress_internal.h ← fastcover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-tv86iioi\fastcover.c:94:12: error: call to undeclared function 'ZSTD_hash6Ptr'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## compress/zstd_compress_internal.h ← zdict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-wz6l6bc0\zdict.c:114:9: error: use of undeclared identifier 'ZSTD_compressedBlockState_t'; did you mean 'ZSTD_compressBlock'?

## pool.h ← pool.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-0uh41g6z\pool.c:324:8: error: unknown type name 'POOL_ctx'

## stdlib.h ← divsufsort.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-qe6ou655\divsufsort.c:1858:21: error: call to undeclared library function 'malloc' with type 'void *(__size_t)' (aka 'void *(unsigned long long)'); ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_ldm.h ← zstd_ldm.c
   状态：no-change — 差异在噪声范围内（-4 ms）

## deprecated/zbuff.h ← zbuff_compress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-b1q2zhfw\zbuff_compress.c:54:1: error: unknown type name 'ZBUFF_CCtx'

## common/debug.h ← debug.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-di862z8y\debug.c:29:20: error: use of undeclared identifier 'DEBUGLEVEL'

## common/fse.h ← fse_decompress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-4awov0p7\fse_decompress.c:47:4: error: "FSE_FUNCTION_EXTENSION must be defined"

## threading.h ← threading.c
   状态：no-change — 差异在噪声范围内（+2 ms）

## common/fse.h ← fse_compress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-22h4556s\fse_compress.c:49:4: error: "FSE_FUNCTION_EXTENSION must be defined"

## ucrt/time.h ← fastcover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-zuty9o58\fastcover.c:71:14: error: unknown type name 'clock_t'

## ucrt/time.h ← cover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-mck_wgg4\cover.c:75:14: error: unknown type name 'clock_t'

## ucrt/time.h ← zdict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-dawlgvff\zdict.c:81:8: error: unknown type name 'clock_t'

## mem.h ← hist.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-rapztiui\hist.c:32:11: error: unknown type name 'BYTE'

## zstd_v05.h ← zstd_v05.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-tvez379z\zstd_v05.c:270:46: error: unknown type name 'ZSTDv05_DCtx'

小结：2/40 个候选实测有效。
注意：编译通过只说明语法上可移除，不等于语义上未使用。精确的未使用判定需要 AST 级分析，见 ROADMAP。
