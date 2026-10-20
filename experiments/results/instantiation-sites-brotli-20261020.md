# 实现片段的实例化站点：brotli 31 个站点实测（2026-10-20）

复现命令（每侧 3 次取最小值，复测 2 遍，计时串行）：

```
python experiments/verify_gate_probe.py --corpus %TEMP%/brotli-1.1.0 \
    --compiler "C:/Program Files/LLVM/bin/clang.exe" \
    --only instantiation --runs 3 --confirm 2 --jobs 1
# 对照组（关掉「实现片段强制过闸门」这条纪律）：再加 --no-force-instantiation
```

语料：brotli 1.1.0 `c/`，32 TU、2.95 M 行头文件解析量、串行基线 45.0 s。
全局 `--check-codegen` **关闭**——站点自己强制开闸门，测出 codegen-changed
就证明强制逻辑生效。

## 一、31 条全部实测，0 条可删

| 单元 | 片段 | 站点 | 结局 | 编译器给的理由（首条） |
|---|---|---:|---|---|
| backward_references.c | backward_references_inc.h | 19 | compile-error | `197:25: error: call to undeclared function 'CreateBackwardReferencesNH2'`（NH3/H4/H5/H6/H40/H41/H42/H54/H35/H55/H65 同）、`175:7 'CreateBackwardReferencesDH5'`（DH6/DH40/DH41/DH42/DH55/DH65 同） |
| bit_cost.c | bit_cost_inc.h | 3 | **codegen-changed** | 编译通过，但生成的代码与原来不同 |
| block_splitter.c | block_splitter_inc.h | 3 | compile-error | `145:5 'SplitByteVectorLiteral'`、`167:5 'SplitByteVectorCommand'`、`190:5 'SplitByteVectorDistance'` |
| brotli_bit_stream.c | block_encoder_inc.h | 3 | compile-error | `1021:3 'BuildAndStoreEntropyCodesLiteral'`、`1025:3 …Command`、`1029:3 …Distance` |
| metablock.c | metablock_inc.h | 3 | compile-error | `543:5 unknown type name 'BlockSplitterLiteral'`、`546:3 'BlockSplitterCommand'`、`547:3 'BlockSplitterDistance'` |

**improved 0 / 31。**

以前这 6 个（tu, 头）对在 `verify` 里是 `ambiguous`——定位层拒绝在 3–19 行同名
include 之间猜，因此**从未做过一次实验**。现在它们变成 31 条带行号的站点候选，
每条都被编译器裁决过。

值得一提：报错里的符号名（`CreateBackwardReferencesNH40`）是
`#define FN(X) EXPAND_CAT(X, HASHER())` 拼出来的，静态文本里根本不存在这个
字符串——这就是「静态判据给不出答案，只能实验」的具体原因。

## 二、对照组：关掉强制闸门

```
--no-force-instantiation   →   bit_cost.c 的 3 条从 codegen-changed 变成 no-change（−8 ms）
                               其余 28 条不变（仍是 compile-error）
                               improved 0 / 31
```

它拦下的不是「会被误报成可删」的假阳性（improved 两边都是 0），而是三条
**看起来无害的破坏性改动**：删掉一次实例化后编译照样通过、计时差 −8 ms（在
噪声内），只有汇编指纹显示目标文件少了一个函数定义。没有闸门时读者看到的是
「删了也没收益」，实际是「程序被改了」。

只做 `-c` 不链接的探针，对「少一个函数定义」是**静默**的——这就是这类候选
必须强制过闸门的原因。

## 三、五语料回归（推荐层）

数据：`experiments/results/instantiation-regression-{lua,doctest,zlib,zstd,brotli}-20261020.json`

| 语料 | 候选总数 | 疑似未使用 | 实例化站点 | guard 重复（按 0 计） |
|---|---:|---:|---:|---:|
| Lua 5.4.7 | 222 | 18 | 0 | 0 |
| doctest | 130 | 6 | 0 | 0 |
| zlib | 47 | 1 | 0 | 1（`minigzip.c → stdlib.h`） |
| zstd | 90 | 6 | 0 | 0 |
| brotli | 110 | 4 | 31 | 1（`brotli.c → utime.h`） |

四个语料上这条分支从未触发 → 推荐层与上一轮逐格相同。

## 四、顺带实测的一条：zstd `fse_compress.c → hist.h`

它在上一轮（实现片段修复）从「疑似未使用」变成「在用」，不是本轮改动（zstd
上本轮路径未触发）。实测为证：

```
python experiments/verify_gate_probe.py --corpus %TEMP%/zstd-1.5.6 \
    --compiler clang --only all --match "hist.h" --runs 3 --confirm 2
```

| 单元 | 结局 |
|---|---|
| fse_compress.c → hist.h | **no-change（−2 ms）** —— 编得过、没收益 |
| hist.c / huf_compress.c / zstd_compress.c / zstd_compress_superblock.c / zstd_opt.c → hist.h | compile-error（`HIST_WKSP_SIZE` / `HIST_count_simple` / `HIST_countFast_wksp` / `HIST_count_wksp`） |

即：这一格是「真能删但没收益」，推荐层少了一条空头承诺，不是回归。
