# 边级成本归因：精度实验（2026-10-19）

三个实验、一个机制修正、一个负面结果。全部脚本与原始数据在仓库内，可复跑。

## 0. 结论先行

1. **墙钟无法验证边级排序**（负面结果）：doctest 的 24 个编译单元上，单独删掉
   prelude.h 里的一条标准头 include，节省量普遍低于测量噪声（24 TU 尺度上约
   ±250ms）。用这批数据比较「行数排序 vs 毫秒排序」，两者与实测节省的秩相关
   都是负的（ρ = −0.19 / −0.21），**不是模型不行，是这把尺子量不出来**。
2. **结构验证有效，并抓到一个真 bug**：`-H` 只记录每个头**首次生效**的包含点，
   因此「独占闭包」系统性地高估删除收益。doctest 上 25 条可删边合计虚报
   **45 个「会消失但其实不会消失」的文件**，20 条 prelude.h 出边里 **7 条**
   承诺了根本不存在的收益（合计 1.35s，占承诺总量的 14%）。
3. **修法成立**：把语义从「有效树闭包」换成「**潜在供给闭包**」——扫描本次解析到
   的每个文件的 `#include` 行补全供给边，再做可达性。同一批数据上假阳性文件
   **45 → 0**，完全一致率 **18/25 → 24/25**；唯一不一致的一条是保守漏报
   （文本层看不到条件编译，多算了一条永不生效的供给边）。

## 1. 为什么先做墙钟实验，又为什么否掉它

设计（CBE 协议）：候选 = prelude.h 的 36 条 include；可删性 = 删掉后 24 个 TU
全部仍能编译；计时 = 6 个 TU × 3 轮**顺序交替**（B,M / M,B / …）取中位数，
抵消「先跑的那一臂吃冷缓存」的系统偏差。

结果：25 条可删，实测节省分布 −168ms ~ +305ms（24 TU 合计），**符号都不稳定**。
基线单 TU 约 0.67s，单条标准头的解析成本只有几毫秒到几十毫秒——低于分辨率。

这条负面结果本身有用：它解释了为什么本项目的实测结论一直是「多数候选不值得动」
（doctest 1/16、zlib 1/16、json 0/16）而不是「工具找不到优化」。

## 2. 机制验证：为什么会虚报

直接检验：删掉 prelude.h 的 `#include <cmath>` 后重新编译一个 TU，

```
baseline:           -H 事件 377 个，cmath 出现 1 次
删掉 cmath 这一行:   -H 事件 377 个，cmath 仍然出现 1 次
```

一个都没少——cmath 被别的头（`<algorithm>` 之类）传递拉进来了。`-H`（以及
`-ftime-trace` 的 Source 区间）只记录**首次**包含点，第二次包含被 include guard
吞掉、根本不出现在树里，于是有效树上 cmath 看起来「只挂在 prelude.h 下」，
删了当然「省 432ms」。实际省 0。

## 3. 结构验证：确定性真值

计时不可用，但「删掉这条 include 后哪些文件不再被解析」是**集合**，用真编译的
`-H` 前后对比可以精确得到。8 个 TU 取并集作真值，36 条候选各跑一次真编译：

| 预测器 | 完全一致 | 假阳性文件 | 真消失且预测对 |
|---|---|---|---|
| A 有效树闭包（原 spike） | 18/25 | **45** | 2/2 |
| B 潜在供给闭包（本次修正） | **24/25** | **0** | 1/2 |

唯一不一致：`prelude.h → csignal`，真值里 `csignal` 与 `signal.h` 都消失，
B 只预测出 `csignal`。原因：某个仍在树中的文件文本里写着 `#include <signal.h>`，
但那行在条件编译里永不生效；文本扫描没有宏求值，把它当成了替代供给。
方向是**保守**（少报而非虚报），可接受，但必须写进文档。

## 4. 修正对排序与量的影响

同一份 trace 数据，换闭包语义后：全项目边数 **333 → 205**；
prelude.h 出边 **20 → 13**；承诺收益合计 **9.48s → 5.02s**。

被清零的 7 条（都是「删了不省」）：`cmath` `cstdio` `new` `limits` `ctime`
`utility` `climits`。其中 `cmath` 原本承诺 432ms、`limits` 362ms、`cstdio`
268ms——正是墙钟实验里实测为 0 的那几条，两边独立地指向同一个结论。

`fstream` 从 2274ms 降到 59ms 是另一类修正：它的内容大部分被别的头带进来了，
只剩很少一部分真正独占。

## 5. 这对产品意味着什么

- **边级成本只能用于排序候选，不能当作结论**。一条边的预测值回答「如果删掉它，
  这条路径上少解析多少」，不回答「能不能删」（实测里 13 条高成本边有 12 条
  编译不过），也不保证「删了就有这么多」（前提是那个头真的不再被别处拉进来）。
- **可采纳率低的根因被解释清楚了**：大量候选头本来就被别的头拉着，删掉语义上
  等于没删。这不是候选找错，而是 C++ 头文件的真实结构。
- 因此产品口径写成：**预测给排序，verify 给结论**，两者职责不混。这一条已经
  写在 `attrib/edgecost.py` 的模块文档里，并由 `tests/test_edgecost.py` 的
  `test_potential_graph_kills_the_false_saving` 守护（回归即失败）。

## 6. 复现

```bash
# 1) 采集与边级预测（换闭包语义前后各跑一次）
python experiments/edge_cost_spike.py --project <doctest> --db build/compile_commands.json \
    --tus 24 --compiler clang++ --out experiments/results/edge-spike-doctest-potential-20261019.json
python experiments/edge_cost_spike.py ... --effective-only --out .../edge-spike-doctest-20261019.json

# 2) 墙钟 A/B（36 条候选，约 19 分钟）
python experiments/edge_ab_doctest.py --project <doctest> --db build/compile_commands.json \
    --tus 24 --sample 6 --rounds 3 --spike .../edge-spike-doctest-20261019.json \
    --out experiments/results/edge-ab-doctest-20261019.json

# 3) 结构真值（8 TU，约 4 分钟）
python experiments/edge_closure_check.py --project <doctest> --db build/compile_commands.json \
    --tus 24 --sample 8 --out experiments/results/edge-closure-doctest-20261019.json

# 4) 排序质量对比（两个模型 × 修正前后）
python experiments/edge_rank_eval.py --spike ... --ab ... --noise 0.25
```

环境：Windows 10 / clang 23.1.3 (LLVM) / doctest master / 24 TU / Python 3.13。

## 7. 已知边界（不隐藏）

- 单机、单工具链、单个工程（doctest），不足以支撑跨工程普适性；行数为代理的
  模型只在「头文件大小差异大」的工程上才有区分度，本工程是极端反例。
- 墙钟实验的噪声线是按残差估的（±250ms @ 24 TU），不是按统计学严格推导；
  正因如此，任何「毫秒模型更准」的结论都不该建立在这批数据上。
- 结构实验的「真值」取 8 个 TU 的并集；不同 TU 的包含结构不同，边级预测按 TU
  逐个算，不存在跨 TU 的抵消问题，但并集会掩盖「某 TU 预测对、另一 TU 预测错」
  的细节。
- 文本扫描供给边依赖文件名匹配，同名头（不同目录）会误配；本工程未出现，
  真实工程需按包含路径解析，是后续工作。
