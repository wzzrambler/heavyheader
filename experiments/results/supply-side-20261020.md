# 供给侧漏报：诊断与第一个修复（2026-10-20）

起因：§9.19.7 发现「8 条落地收益里 7 条不在推荐集」，于是想放宽供给侧。但
「放宽」会先放掉零假阳性，所以先量清楚**真能删的候选是被哪一条判据挡住的**。

## 一、漏报的实测分布（Lua 34 TU / 222 条边）

新脚本 `experiments/supply_diagnosis.py`：把三真值（真值可删）与候选的
`usage_reason` 对齐，按阻塞原因归类。

| | 数量 |
|---|---:|
| 真值可删 | 69 |
| 工具推荐（`plausibly_unused`），其中真能删 | 18（18/18，**零假阳性**） |
| **供给侧漏报**（真能删但没推荐） | **51** |

漏报按阻塞原因（修复前）：

| 阻塞原因 | 条数 | 说明 |
|---|---:|---|
| 只做转发 / 只提供宏 | 34 | `_declares_nothing` 分支，判 unknown |
| 疑似副作用（UCRT 声明宏） | 9 | `_CRT_INSECURE_DEPRECATE_GLOBALS(...)` 等 |
| 只在幸存头条件编译区声明 | 2 | `NULL` / `printf` / `remove`，判 unknown |
| 宏体依赖 | 4 | 其中 3 条是**关键字假依赖**（见下） |
| 其它（EOZ 等） | 2 | |

**最大那 4 条漏报**（按预估收益）里有 3 条确认能省 3.1%~6.4%——漏的不是小鱼。

## 二、找到并修掉一个真 bug：宏体里把关键字当依赖

`macro_bodies_in` 用裸的 `_IDENTIFIER.findall` 抽取宏展开体，**没有过滤关键字**。
Lua 的 `luaconf.h` 里 `#define LUAI_UACNUMBER double`，于是判定认为
「删掉 stdlib.h 就没有 `double` 了」——而 `double` 是关键字，不随任何头文件
消失。这条假依赖把 4 条真能删的候选锁成 `in_use`。

修复：改用 `identifiers_in()`（过滤关键字、编译器内建、单字符）。

**实测影响（Lua，`candidate-precision-lua-20261020m.json`）**：

| | 修复前 | 修复后 |
|---|---:|---:|
| `in_use` | 145 | 141 |
| `unknown` | 59 | 63 |
| 推荐层 / 其中真能删 | 18 / 18 | **18 / 18（零假阳性不变）** |

判定变化的 4 条全部 `in_use → unknown`，且**真值全部为「可删」**：
`liolib.c`、`lmathlib.c`、`lstrlib.c` → stdlib.h，`llex.c` → string.h。
其中 3 条经重复测量确认能省 3.11%~6.42%。

**注意方向**：这 4 条只是从「确定在用」变成「判不了」，**没有进推荐集**——
它们还被 UCRT 的「疑似副作用」拦着。修复去掉的是一条**假依赖**，不是最后一道闸门。

## 三、第二个语料的回归检查（doctest 149 TU）

| | 归档（修复前） | 修复后 |
|---|---:|---:|
| 候选 | 130 | **126** |
| 推荐层 / 其中真能删 | 6 / 6 | **6 / 6（零假阳性不变）** |
| 判定变化 | — | 4 条 `unknown → in_use`（真值均可删） |

**这 4 条不能干净归因于本次修复**：候选集本身从 130 变成 126，说明两次扫描的
图不同（并行探测的已知抖动，见 `scanner` 的稳健性注释）。修复只可能**减少**
`in_use` 的证据，不可能增加——所以这 4 条更可能是抖动产物。

**遗留方法问题**：跨提交的精度对比必须固定 `probe_jobs=1`，否则扫描抖动会
伪造出判定变化。下一轮对比要先把这条钉死。

## 四、还没动的三类（按漏报规模排序，不盲目放宽）

1. **只做转发 / 只提供宏（34 条）**：判 unknown 是对的——这类头的作用取决于
   后续头怎么用它的宏，文本层确实看不清。
2. **UCRT 疑似副作用（12 条）**：`_CRT_INSECURE_DEPRECATE_GLOBALS(_get_pgmptr)`
   这类形态。这条是**目前最大的单一拦路虎**，而且拦住的正是最大的一批收益
   （`lbaselib` / `lmathlib` / `lvm` / `ldblib` …）。现有设计让它们走
   `unknown` + 强制汇编闸门，闸门实测都过了——所以正确的改法**不是放宽文本
   判据**，而是给「三真值 + 复测全部通过」的候选单开一个**已验证层级**，
   用编译器证据而不是文本证据把它推给用户。
3. **只在幸存头条件编译区声明（约 4 条）**：`NULL` / `printf` / `errno`。
   拦住的 `lgc.c→stdio.h`（−5.29%）与 `lvm.c→stdio.h`（−6.74%）是全部候选里
   最大的两条。现有判据把「条件区里的声明」判成不可信是对的，但代价是这两条
   永远进不了推荐——同样该靠「已验证层级」而不是改判据。

## 复现

```
python experiments/supply_diagnosis.py \
    --precision-json experiments/results/candidate-precision-lua-20261020m.json \
    --project %TEMP%/upstream/lua-5.4.7 \
    --db %TEMP%/upstream/lua-5.4.7/compile_commands.json \
    --compiler "C:/Program Files/LLVM/bin/clang.exe" --tus 34 \
    --out experiments/results/supply-diagnosis-lua-20261020m.json
```
