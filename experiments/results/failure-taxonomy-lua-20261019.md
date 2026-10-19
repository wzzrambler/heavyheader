# 候选失败原因分类

- 工程：`lua-5.4.7`（34 个编译单元）
- 候选总数 **222**：疑似未使用 20 / 无法判定 32 / 在用 170
- 探测 92 条（疑似未使用 + 无法判定 + in_use 镜像前 40），编译器调用 256 次

## A. 疑似未使用候选的失败原因（工具推送了的）

| 类别 | 条数 | 含义 |
|---|---:|---|
| **判定假阳性**（判可删、编译失败） | 1 | usage 判定漏判——消耗了编译预算的纯浪费 |
| **空头承诺**（编得过、0 解析收益） | 0 | 独占闭包预测错——删了不省任何解析 |
| **静默改程序**（汇编指纹变） | 0 | 编译通过且少解析，但程序被悄悄改了 |
| **已证实**（三条真值全过） | **19** | 编得过 + 真少解析 + 程序没变 |

## B. 被判「在用」的镜像样本实测（工具没有推送的）

| 类别 | 条数 | 含义 |
|---|---:|---|
| 判定正确（实测编译失败） | 26 | 真依赖——规则正确拦下，没浪费一次无效推荐 |
| 无收益（编得过、0 解析） | 0 | 删了也无害，但不构成机会 |
| **保守漏报（实测可删、程序没变）** | **45** | **被规则挡住的真实机会——零假阳性的代价** |
| 静默改程序（实测汇编变） | 1 | 删除会让程序变化，拦下是对的 |

## C. 核心指标：分析时间 → 已证实优化

- 分析耗时 **0.59 分钟**（扫描 15.3s + 基线 2.2s + 探测 5.2s + 闸门 12.5s，8 并行）
- **每 100 个编译单元找到 55.9 条已证实优化**（解析层）
- **每 1 条已证实优化消耗 0.03 分钟分析时间**
- 另有 **45 条保守漏报**：usage 判定「在用」但实测可删（占镜像样本的 62%）

## D. 疑似未使用候选逐条结局

| TU | 头 | 判定理由 | 结局 |
|---|---|---|---|
| llex.c | lparser.h | firstchar 由本文件自己定义 | parse_proven_timing_unmeasured |
| ltm.c | lvm.h | v2 由本文件自己定义 | parse_proven_timing_unmeasured |
| ldo.c | ltable.h | NULL、StkId 另有供给者 | parse_proven_timing_unmeasured |
| lgc.c | ldebug.h | Proto、StkId 另有供给者 | parse_proven_timing_unmeasured |
| lcorolib.c | lualib.h | luaopen_coroutine 由本文件自己定义 | parse_proven_timing_unmeasured |
| lcode.c | lstring.h | extra 由本文件自己定义 | parse_proven_timing_unmeasured |
| lstring.c | ldebug.h | TString、cast_int 另有供给者 | parse_proven_timing_unmeasured |
| lparser.c | ldebug.h | msg、what 由本文件自己定义 | parse_proven_timing_unmeasured |
| luac.c | lopnames.h | opnames 不由任何头提供（命令行宏） | verdict_false_positive |
| lundump.c | ldebug.h | x80 不由任何头提供（命令行宏） | parse_proven_timing_unmeasured |
| lbaselib.c | lualib.h | luaopen_base 由本文件自己定义 | parse_proven_timing_unmeasured |
| ldblib.c | lualib.h | luaopen_debug 由本文件自己定义 | parse_proven_timing_unmeasured |
| liolib.c | lualib.h | luaopen_io 由本文件自己定义 | parse_proven_timing_unmeasured |
| lmathlib.c | lualib.h | luaopen_math 由本文件自己定义 | parse_proven_timing_unmeasured |
| ltablib.c | lualib.h | luaopen_table 由本文件自己定义 | parse_proven_timing_unmeasured |
| lmathlib.c | lprefix.h | LUA_USE_C89 另有供给者 | parse_proven_timing_unmeasured |
| loslib.c | lualib.h | luaopen_os 由本文件自己定义 | parse_proven_timing_unmeasured |
| lstrlib.c | lualib.h | luaopen_string 由本文件自己定义 | parse_proven_timing_unmeasured |
| loslib.c | lprefix.h | LUA_USE_C89 另有供给者 | parse_proven_timing_unmeasured |
| lutf8lib.c | lualib.h | luaopen_utf8 由本文件自己定义 | parse_proven_timing_unmeasured |

## E. 保守漏报逐条（被判「在用」、实测可删）

| TU | 头 | 判定理由 | 少解析文件 | 预估秒 |
|---|---|---|---:|---:|
| lcorolib.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.2101 |
| lcode.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.186 |
| ldblib.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.1738 |
| lbaselib.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.1723 |
| liolib.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.144 |
| lmathlib.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.1415 |
| lstrlib.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.1093 |
| lutf8lib.c | stdlib.h | 副作用：_CRT_INSECURE_DEPRECATE_GLOBALS(_get | 9 | 0.1021 |
| lapi.c | string.h | 副作用：__DEFINE_CPP_OVERLOAD_SECURE_FUNC_0_ | 6 | 0.1448 |
| llex.c | string.h | 副作用：__DEFINE_CPP_OVERLOAD_SECURE_FUNC_0_ | 6 | 0.1419 |
| ltm.c | string.h | 副作用：__DEFINE_CPP_OVERLOAD_SECURE_FUNC_0_ | 6 | 0.121 |
| lgc.c | stdio.h | 副作用：_Success_(return != 0)         _ACRT | 3 | 0.403 |
| lvm.c | stdio.h | 副作用：_Success_(return != 0)         _ACRT | 3 | 0.1676 |
| lctype.c | lprefix.h | — | 1 | 0.0075 |
| lopcodes.c | lprefix.h | — | 1 | 0.0072 |
| lfunc.c | lprefix.h | — | 1 | 0.006 |
| lmem.c | lprefix.h | — | 1 | 0.0054 |
| ldump.c | lprefix.h | — | 1 | 0.0049 |
| lcorolib.c | lprefix.h | — | 1 | 0.0041 |
| ldo.c | lprefix.h | — | 1 | 0.0039 |
| lapi.c | lprefix.h | — | 1 | 0.0038 |
| lgc.c | lprefix.h | — | 1 | 0.0038 |
| llex.c | lprefix.h | — | 1 | 0.0038 |
| lauxlib.c | lprefix.h | — | 1 | 0.0037 |
| lcode.c | lprefix.h | — | 1 | 0.0036 |
| ldebug.c | lprefix.h | — | 1 | 0.0036 |
| linit.c | lprefix.h | — | 1 | 0.0035 |
| lbaselib.c | lprefix.h | — | 1 | 0.0034 |
| ldblib.c | lprefix.h | — | 1 | 0.0034 |
| ltable.c | lprefix.h | — | 1 | 0.0034 |
| lstring.c | lprefix.h | — | 1 | 0.0032 |
| ltm.c | lprefix.h | — | 1 | 0.0032 |
| lobject.c | lprefix.h | — | 1 | 0.0031 |
| lparser.c | lprefix.h | — | 1 | 0.0031 |
| lstate.c | lprefix.h | — | 1 | 0.003 |
| lundump.c | lprefix.h | — | 1 | 0.0029 |
| liolib.c | lprefix.h | — | 1 | 0.0028 |
| ltablib.c | lprefix.h | — | 1 | 0.0027 |
| lzio.c | lprefix.h | — | 1 | 0.0026 |
| lstrlib.c | lprefix.h | — | 1 | 0.0021 |
| lutf8lib.c | lprefix.h | — | 1 | 0.002 |
| luac.c | lprefix.h | — | 1 | 0.0018 |
| lvm.c | lprefix.h | — | 1 | 0.0016 |
| loadlib.c | lprefix.h | — | 1 | 0.0002 |
| lua.c | lprefix.h | — | 1 | 0.0002 |
