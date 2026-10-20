# HeavyHeader 实测验证（每侧 3 次，取最小值）

## quality.h ← compound_dictionary.c
   状态：improved — 基线 466 ms → 移除后 379 ms
   实测节省：87 ms (18.8%)

## enc/dictionary_hash.h ← backward_references.c
   状态：no-change — 差异在噪声范围内（+6 ms）

## enc/dictionary_hash.h ← dictionary_hash.c
   状态：no-change — 差异在噪声范围内（+3 ms）

## literal_cost.h ← literal_cost.c
   状态：no-change — 差异在噪声范围内（-3 ms）

## common/platform.h ← dictionary_hash.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-3l94405m\dictionary_hash.c:17:1: error: unknown type name 'BROTLI_INTERNAL'

## io.h ← brotli.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-opru67el\brotli.c:69:3: error: call to undeclared function '_sopen_s'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## enc/find_match_length.h ← compress_fragment.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-tr1wljx9\compress_fragment.c:566:30: error: call to undeclared function 'FindMatchLengthWithLimit'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## enc/find_match_length.h ← compress_fragment_two_pass.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-2azxbysk\compress_fragment_two_pass.c:325:38: error: call to undeclared function 'FindMatchLengthWithLimit'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## enc/find_match_length.h ← static_dict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-lq8jtkao\static_dict.c:38:10: error: call to undeclared function 'FindMatchLengthWithLimit'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## share.h ← brotli.c
   状态：no-change — 差异在噪声范围内（-0 ms）

## backward_references.h ← backward_references.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\backward_references_inc.h:13:5: error: unknown type name 'ContextLut'

## command.h ← command.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-s006s1hq\command.c:15:31: error: use of undeclared identifier 'BROTLI_NUM_INS_COPY_CODES'

## common/platform.h ← dictionary.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-rxrn24wm\dictionary.c:5910:3: error: call to undeclared function 'BROTLI_UNUSED'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## bit_cost.h ← bit_cost.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\brotli-1.1.0\c\enc\bit_cost_inc.h:122:13: error: call to undeclared function 'BitsEntropy'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## state.h ← state.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-6iom6c1w\state.c:20:36: error: unknown type name 'BrotliDecoderState'

## bit_reader.h ← bit_reader.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-z26rzwcx\bit_reader.c:30:26: error: unknown type name 'BrotliBitReader'

## enc/memory.h ← shared_dictionary.c
   状态：no-change — 差异在噪声范围内（-1 ms）

## common/platform.h ← platform.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-h7bo0mjq\platform.c:15:3: error: call to undeclared function 'BROTLI_UNUSED'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## common/constants.h ← constants.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-unwzv673\constants.c:9:7: error: unknown type name 'BrotliPrefixCodeRange'

## histogram.h ← histogram.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-s4gve7tq\histogram.c:48:39: error: unknown type name 'HistogramLiteral'

## fast_log.h ← fast_log.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-7b361jzz\fast_log.c:14:31: error: use of undeclared identifier 'BROTLI_LOG2_TABLE_SIZE'

## enc/memory.h ← memory.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-mdotfoh3\memory.c:32:5: error: unknown type name 'MemoryManager'

## utf8_util.h ← utf8_util.c
   状态：improved — 基线 373 ms → 移除后 200 ms
   实测节省：172 ms (46.2%)

## common/constants.h ← brotli.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-pw05zut3\brotli.c:758:7: error: use of undeclared identifier 'BROTLI_MAX_DISTANCE'

## backward_references_hq.h ← backward_references_hq.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-0wdx_ldq\backward_references_hq.c:44:28: error: unknown type name 'ZopfliNode'

## enc/brotli_bit_stream.h ← brotli_bit_stream.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-6fo528yb\brotli_bit_stream.c:952:55: error: unknown type name 'MetaBlockSplit'; did you mean 'BlockSplit'?

## enc/brotli_bit_stream.h ← compress_fragment.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-skz05qro\compress_fragment.c:112:3: error: call to undeclared function 'BrotliBuildAndStoreHuffmanTreeFast'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]

## context.h ← context.c
   状态：no-change — 差异在噪声范围内（+0 ms）

## transform.h ← transform.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-lbytlffx\transform.c:30:14: error: unknown type name 'uint16_t'

## static_dict.h ← static_dict.c
   状态：compile-error — 移除后编译失败：C:\Users\31879\AppData\Local\Temp\heavyheader-probe-fvignehq\static_dict.c:511:23: error: use of undeclared identifier 'BROTLI_MAX_STATIC_DICTIONARY_MATCH_LEN'

小结：2/30 个候选实测有效。
注意：编译通过只说明语法上可移除，不等于语义上未使用。精确的未使用判定需要 AST 级分析，见 ROADMAP。
