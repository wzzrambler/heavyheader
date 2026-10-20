# HeavyHeader 实测验证（每侧 3 次，取最小值）

## deprecated/zbuff.h ← zbuff_common.c
   状态：no-change — 差异在噪声范围内（-2 ms）

## hist.h ← fse_compress.c
   状态：no-change — 差异在噪声范围内（-2 ms）

## zstd_double_fast.h ← zstd_double_fast.c
   状态：no-change — 基线 391 ms → 移除后 406 ms

## dictBuilder/divsufsort.h ← divsufsort.c
   状态：no-change — 差异在噪声范围内（-1 ms）

## zstd_fast.h ← zstd_fast.c
   状态：no-change — 基线 396 ms → 移除后 411 ms

## dictBuilder/divsufsort.h ← zdict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-8w3w5xxf\zdict.c:506:39: error: call to undeclared function 'divsufsort'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## decompress/zstd_ddict.h ← zstd_decompress_block.c
   状态：no-change — 差异在噪声范围内（-2 ms）

## allocations.h ← pool.c
   状态：improved — 基线 384 ms → 移除后 205 ms
   实测节省：179 ms (46.5%)

## common/xxhash.h ← xxhash.c
   状态：codegen-changed — 移除后编译通过，但生成的代码与原来不同（重载解析或条件编译发生变化）

## stdio.h ← divsufsort.c
   状态：improved — 基线 223 ms → 移除后 212 ms
   实测节省：11 ms (4.8%)

## zstd_compress_superblock.h ← zstd_compress_superblock.c
   状态：no-change — 差异在噪声范围内（+3 ms）

## decompress/zstd_ddict.h ← zstd_ddict.c
   状态：no-change — 差异在噪声范围内（+1 ms）

## common/debug.h ← pool.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-fic75x5g\pool.c:340:5: error: call to undeclared function 'assert'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_opt.h ← zstd_opt.c
   状态：no-change — 基线 395 ms → 移除后 408 ms

## assert.h ← divsufsort.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-8v_2kgj9\divsufsort.c:418:7: error: call to undeclared function 'assert'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_compress_sequences.h ← zstd_compress_sequences.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-0mrg6cof\zstd_compress_sequences.c:46:43: error: unknown type name 'FSE_CTable'

## zstd_compress_literals.h ← zstd_compress_literals.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-3ua8erbg\zstd_compress_literals.c:41:5: error: use of undeclared identifier 'BYTE'

## common/error_private.h ← error_private.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-694nh7kc\error_private.c:15:32: error: unknown type name 'ERR_enum'

## common/error_private.h ← zbuff_compress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-22ulo1et\zbuff_compress.c:77:5: error: call to undeclared function 'FORWARD_IF_ERROR'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## common/error_private.h ← zbuff_common.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-l1a95pbv\zbuff_common.c:23:51: error: call to undeclared function 'ERR_isError'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## common/zstd_internal.h ← zstd_common.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-zlvmoabo\zstd_common.c:24:44: error: use of undeclared identifier 'ZSTD_VERSION_NUMBER'

## common/zstd_internal.h ← huf_decompress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-m0s2t92p\huf_decompress.c:407:36: error: call to undeclared function 'MIN'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## common/zstd_internal.h ← cover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-4zlatsje\cover.c:477:20: error: call to undeclared function 'MIN'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_decompress_internal.h ← zstd_ddict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-hf_9fpk9\zstd_ddict.c:40:5: error: unknown type name 'ZSTD_entropyDTables_t'

## common/xxhash.h ← zstd_v07.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-0ku_k3ra\zstd_v07.c:2891:5: error: unknown type name 'XXH64_state_t'

## compress/zstd_compress_internal.h ← zdict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-ls_9f8o4\zdict.c:114:9: error: use of undeclared identifier 'ZSTD_compressedBlockState_t'; did you mean 'ZSTD_compressBlock'?

## compress/zstd_compress_internal.h ← fastcover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-zk03goxn\fastcover.c:94:12: error: call to undeclared function 'ZSTD_hash6Ptr'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## zstd_ldm.h ← zstd_ldm.c
   状态：no-change — 基线 403 ms → 移除后 421 ms

## pool.h ← pool.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-kt0b2kl9\pool.c:324:8: error: unknown type name 'POOL_ctx'

## stdlib.h ← divsufsort.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-dw6omdyx\divsufsort.c:1858:21: error: call to undeclared library function 'malloc' with type 'void *(__size_t)' (aka 'void *(unsigned long long)'); ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## deprecated/zbuff.h ← zbuff_compress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-qmgcgfhn\zbuff_compress.c:54:1: error: unknown type name 'ZBUFF_CCtx'

## common/debug.h ← debug.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-63o1pna1\debug.c:29:20: error: use of undeclared identifier 'DEBUGLEVEL'

## threading.h ← threading.c
   状态：no-change — 差异在噪声范围内（-2 ms）

## common/fse.h ← fse_compress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-mpjkbzsk\fse_compress.c:49:4: error: "FSE_FUNCTION_EXTENSION must be defined"

## common/fse.h ← fse_decompress.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-8krsrovd\fse_decompress.c:47:4: error: "FSE_FUNCTION_EXTENSION must be defined"

## ucrt/time.h ← cover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-b7jldiwh\cover.c:75:14: error: unknown type name 'clock_t'

## ucrt/time.h ← zdict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-y0qr5myd\zdict.c:81:8: error: unknown type name 'clock_t'

## ucrt/time.h ← fastcover.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-0anqavqs\fastcover.c:71:14: error: unknown type name 'clock_t'

## mem.h ← hist.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-dd0z2ixd\hist.c:32:11: error: unknown type name 'BYTE'

## zstd_v05.h ← zstd_v05.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-3ob62lue\zstd_v05.c:270:46: error: unknown type name 'ZSTDv05_DCtx'

小结：2/40 个候选实测有效。
注意：编译通过只说明语法上可移除，不等于语义上未使用。精确的未使用判定需要 AST 级分析，见 ROADMAP。
