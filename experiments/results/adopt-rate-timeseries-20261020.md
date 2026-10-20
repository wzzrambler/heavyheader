# 可采纳率的时间序列（同口径，2026-10-20）

回答一个问题：改了这么多轮，**可采纳率到底上去了没有**。

## 口径（先钉死，否则比值可以随便翻倍）

- 语料：Lua 5.4.7，34 TU，222 条候选；
- 送测池 = **三真值已证实**（编得过 ∧ 真少解析了文件 ∧ 汇编未变）∩ **预估 ≥ 该单元 1% 噪声线**；
- 判定：每侧 3 次取最小值，improved 需复测一遍；
- 分母 = 送测条数（**不是** 222 条候选总数——其中 152 条删了编不过，不该进分母）。

复现：

```
python experiments/candidate_precision.py --project %TEMP%/upstream/lua-5.4.7 \
    --db %TEMP%/upstream/lua-5.4.7/compile_commands.json \
    --compiler "C:/Program Files/LLVM/bin/clang.exe" --tus 34 --check-codegen \
    --out experiments/results/candidate-precision-lua-20261020k.json

python experiments/timing_funnel.py \
    --precision-json experiments/results/candidate-precision-lua-20261020k.json \
    --project %TEMP%/upstream/lua-5.4.7 \
    --db %TEMP%/upstream/lua-5.4.7/compile_commands.json \
    --compiler "C:/Program Files/LLVM/bin/clang.exe" --tus 34 \
    --out experiments/results/timing-funnel-lua-20261020k.json
```

## 结果

| 代码状态 | 送测池 | 编译通过 | improved | 池内采纳率 |
|---|---:|---:|---:|---:|
| 锚定修复前（2026-10-19） | 13 | 13 | 1 | **7.7%** |
| 锚定修复后（`866779f`） | 20 | 20 | 5 | **25%** |
| 最新（`16e3640`） | 20 | 20 | 5 | **25%** |

真值集逐格相同（`candidate-precision-lua-20261019i.json` 与 `-20261020k.json`）：
69 有用 / 0 空头承诺 / 152 编不过 / 1 静默改程序。所以「已证实」这个集合本身
在 Lua 上没被后三轮改动移动过——可比性成立。

**抬升全部来自 §9.12 的预估锚定修复；后三轮在这个指标上一格没动。**

## 两条必须同时说清的注脚

1. **率随分母选法变。** 同一份最新代码，TU 从 34 收到 24 → 池 12 条、improved
   5 条 → **42%**。42% 与 25% 不是进步，只是池更严。
2. **improved 成员会摆动。** 两次 25% 中 5 条有 3 条重合（`lgc.c→stdio.h`、
   `lcode.c→stdlib.h`、`lmathlib.c→stdlib.h`），另两条每次不同——2–3% 的收益
   离噪声线不远。「25%」读作约四分之一。

## 端到端：每条有效优化的分析时间

| 阶段 | 耗时 |
|---|---:|
| 扫描 + 三真值验证（222 条） | 2 m 40 s |
| 计时漏斗（20 条） | 1 m 41 s |
| 合计 | **4 m 21 s → 5 条 ≈ 52 秒/条** |

锚定修复前是同样约 4 分钟换 1 条 → **约 4.4 分钟/条 → 约 52 秒/条**。

## 天花板

69 条已证实里只有 20 条预估过噪声线，其余 49 条在这台机器上**物理不可分辨**
（单条 1–2 ms，低于 2% 复测线）。批量编辑这条路已实测否定（§9.14：19 个单元
合计 −0.73%）；剩下的空间在更大的语料上（zstd 单条 −46.6% 那种量级）。
