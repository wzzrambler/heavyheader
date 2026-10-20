# 已验证层级：把「编译器说得清」的那批候选拿出来单列（2026-10-20）

## 1. 为什么做这一层

上一轮供给侧漏报诊断量出一件事：Lua 上 **8 条落地收益里 7 条被文本判据判成
`in_use` / `unknown`**，可它们全都过了汇编等价闸门——删掉之后两侧编到汇编、
归一化后比哈希，指纹一致。判据说不清的地方，编译器说得清。

旧界面把两种来源混在同一句「建议删除」里：一边是文本推断（`plausibly_unused`），
一边是编译器裁决（汇编一致 + 实测省时 + 复测成立）。读者分不出哪句是证据。

这一轮把它做成产品表面：新增 `heavyheader verified` 命令、`verify --tier` 过滤、
报告里的「已验证层级」小节，以及把档位写进每条结论。**没有放宽任何判据**。

## 2. 做了什么

| 位置 | 内容 |
|---|---|
| `src/heavyheader/evidence.py`（新） | 纯函数：分档计数、按档筛选、文本判据 × 证据档交叉表、`outside_recommendation`（判据没推荐却被闸门抬上来的那批） |
| `heavyheader verified`（新） | 只列编译器证据撑住的候选；强制 `codegen_check=True`；`-o` 落 JSON |
| `heavyheader verify --tier` | `all / verified / equivalent / compiles` 过滤；每条结论打印证据档与复测遍数 |
| `VerifyOptions.order="estimate"` | 这一层按**预估收益**排，不按文本判据排（见 §4） |
| 报告 | 「实测结论」表格加证据档列；新增「已验证层级（按证据强度，不按文本判据）」小节 |

三档的措辞纪律（有测试锁死）：

- `verified`：汇编一致 + 省时过噪声线 + 复测成立 → 可以照着改；
- `equivalent`：汇编一致，但收益没过线 → **程序没变已证，值不值得删未证**，
  不进默认清单，也不许当收益报；
- `compiles_only`：只证明「删了能编过」→ **不许说「程序没变」**。反例是删掉
  `<iomanip>` 后目标文件 21,289 → 10,183 字节。

## 3. 实测（Lua 5.4.7，34 TU，clang，`--top 20 --runs 3 --confirm 2`）

复现：

```bash
python -m heavyheader verified <lua-5.4.7> \
    --compiler "C:/Program Files/LLVM/bin/clang.exe" \
    --compile-commands <lua-5.4.7>/compile_commands.json \
    --top 20 --runs 3 --confirm 2 \
    -o experiments/results/verified-layer-lua-20261020.json
```

### 第 1 遍

| 分档 | 条数 |
|---|---:|
| 已验证 | 2（`lvm.c→stdio.h` −3.4%、`lbaselib.c→stdlib.h` −2.1%） |
| 已证等价 | 4 |
| 无证据 | 14 |

闸门跑了 7 条、拦下 1 条；`outside_recommendation` = **6/6**。

### 第 2 遍（同一命令，同一池）

| 分档 | 条数 |
|---|---:|
| 已验证 | 3（`lcode.c→stdlib.h` −4.3%、`liolib.c→stdlib.h` −2.8%、`lgc.c→stdio.h` −2.3%） |
| 已证等价 | 3 |
| 无证据 | 14 |

状态分布：`compile-error 13`、`improved 3`、`no-change 3`、`codegen-changed 1`。
闸门跑 7 条、拦下 1 条；`outside_recommendation` = 6/6。

### 两遍并排看，最重要的两件事

1. **成员零重合**（2 条 vs 3 条，一条都不重）。收益全在 2%–4%，正好压在噪声线
   上方一点——**这一层也不是一张稳定名单**，它只对「本轮」负责。所以命令头
   必须写这句话，报告里也不能把它当成「工具找到的 N 条可删」。
2. **6 条拿到闸门证据的候选，没有一条来自推荐集**（`plausibly_unused` 0 条）。
   这一层的全部增量都在判据放弃的那一侧——和第 1 节的方向性证据完全对上。

## 4. 为什么这一层按收益排序，不按判据排序

`verify` 的默认排序是 `candidate_rank_key`：疑似未使用在前，「在用」在 tier 3。
Lua 上 `plausibly_unused` 只有 18 条，`in_use` 145 条。**按判据排的 `--top 20`
永远只看到推荐集附近那一片**，而实测省时最多的 7 条都在判据不推荐的那一侧。

所以 `verified` 用 `VerifyOptions.order="estimate"`：只按预估收益降序，判据在这
一层没有投票权。

代价必须一起说：**探针淘汰率极高**——20 条里 13 条编译失败（65%）。这些正是
「判据说在用、其实删了编不过」的重复包含（`stdio.h` / `stdlib.h` 在多个 TU 里
被反复包含）。所以：

- 这一层的命中率（2–3/20）**低于**按判据排的池内采纳率（5/20 = 25%）；
- 两者**不是同一个分母**，不能直接比，更不能说「按收益排更准」；
- 它的价值不是命中率，是**能捞到判据结构上捞不到的那几条**。

## 5. 不能说的话

- 「已验证层级证明了工具的可采纳率更高」——没有。它换的是**展示立场**，不是判据。
- 「verified 的 N 条可以照单全删」——不能。成员不稳（零重合），只代表本轮过线。
- 「equivalent 也是建议」——不是。这一档只说「删了程序不变」，值不值得删仍未知；
  实测里这一档在 Lua 上全是系统头，被多 TU 共用。
- 「compiles_only 说明程序没变」——绝对不许。它只说明编得过。

## 6. 数据

- `experiments/results/verified-layer-lua-20261020.json`（第 2 遍，含 `statuses`）
- 第 1 遍只有终端输出留存（`-o` 指向同一路径被第 2 遍覆盖）：verified 2 /
  equivalent 4 / 无证据 14，`lvm.c→stdio.h` −3.4%、`lbaselib.c→stdlib.h` −2.1%。
  单遍数字不复现，故只作对照，不作结论。
- 相关：`evidence-tiers-20261020.md`（同池 × 三档，5/9 摆动）、
  `supply-side-20261020.md`（漏报 51 条的分类）。
