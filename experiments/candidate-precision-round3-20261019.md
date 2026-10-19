# 候选识别精度 · 第三轮：第三个语料的泛化检验（2026-10-19）

## 动机与前两轮

前两轮的全部根因都出自 doctest 和 zlib 两个语料，有过拟合风险。本轮引入第三个语料
**Lua 5.4.7**（33 个编译单元，clang，`compile_commands.json` 手工生成）做泛化检验：
如果判定逻辑是普适的，Lua 上不该出现成批的新错误——结果恰恰相反，立刻暴露 6 条
「疑似未使用」假阳性（判成可删、实际编不过），连带挖出五处新根因。

对照基线 = 第二轮提交 `f9e9397`（同一把尺子：编译真值 + `-H` 解析文件数差 +
`--check-codegen` 汇编指纹等价）。

## 最终结果（修复全部落地后，`*-20261019h.json`）

| | doctest（24 TU / 38 候选） | zlib（34 TU / 40 候选） | Lua（33 TU / 233 候选） |
|---|---:|---:|---:|
| 「疑似未使用」推荐 | 3 | 1 | 21 |
| 其中真有用 | **3（100%）** | **1（100%）** | **19（90%）** |
| 编不过（假阳性） | **0** | **0** | **0** |
| 空头承诺 | 0 | 0 | 1 |
| P@5 / 首条有用 | 60% / **第 1** | 20% / **第 1** | 80% / **第 1** |
| 纯行数口径（Header Hero）首条有用 | 第 27 | 第 39 | 第 11 |

三个语料的「疑似未使用」推荐**零假阳性**——没有任何一条会推荐出编不过的删除。
唯一的非有用项（Lua `lcode.c -> limits.h`）是空头承诺：编得过、但一个文件都没少解析，
属于排序问题而非正确性问题。第二轮基线在同尺子下的对应数字：doctest 3/3（持平）、
zlib 首条有用第 39 位（现在第 1）、Lua 17 条里 **6 条编不过**（现在 0）。

## 五处根因与修法

### 1. 幸存头「提到」≠「提供」

Lua `ldebug.c` 调用 `luaC_checkGC`，真声明在 `lgc.h`；`ldo.h` 只在另一个宏的
**展开体里调用**它。旧口径按「文本出现过」算供给，`ldo.h` 成了「另一个供给者」，
候选被判可删，删除后 `call to undeclared function`。
**修法**：`header_provider_ids` 只认声明形态（`#define` 名、函数原型/定义、类型与
对象声明；原型也算——头文件里几乎全是原型）。

### 2. 幻影命中：谁都不提供的名字

doctest `handler.cpp -> handler.h`：唯一的具体命中 `DOCTEST_CONFIG_DISABLE` 在可达集
里**没有任何头定义过**——它来自编译命令行 `-D`。删除任何 include 都带不走它，
不构成保留证据；旧口径把它当硬证据，4 条真可删的候选全部被顶成「在用」。
**修法**：供给判定从计数升级为**集合语义**——graph 传每个名字的真实声明者集合
（`declarers`）与别名定义处（`alias_defs`），`analyse_usage` 按候选的消失集合逐名字
裁决，谁都不提供的命中记进 `unprovided` 并写进报告。

### 3. 别名链的三种命运

`#define X Y` 的语义取决于它在哪一侧、Y 活不活：

| 形态 | 判定 | 实例 |
|---|---|---|
| 被删闭包里定义 | X 随头消失 → 依赖 | minigzip `O_BINARY → _O_BINARY`（目标再 ubiquitous 也没用） |
| 幸存头 + Y 幸存侧有声明者 | X 可用 → 不是证据 | doctest 警告宏链 `…_PRIVATE_…_POP → …_COMMON_…_POP` |
| 幸存头 + Y 只在被删闭包 | 展开到未声明名字 → 依赖 | zlib `inflate_fast → z_inflate_fast` |

### 4. 宏名当类型限定符的声明

`local const ct_data static_ltree[D_CODES] = {…}`（zlib trees.h）、
`DOCTEST_THREAD_LOCAL std::vector<…> g_cs;`（doctest）——旧对象声明正则只认关键字
限定符，这批表定义在头和源里都「没有声明者」，四条真依赖被判「另有供给 → 可删」。
**修法**：`_OBJECT_DECL` 的限定符列表放行普通标识符，靠回溯保证最后一个标识符落在
声明名位置；`return y;` 这类语句由 `_NOT_A_TYPE` 拦住。

### 5. 条件分支求值（`-D` 驱动）

两类「源码里有定义、但那个定义在真实构建里不存在」的形态：

* **判死了的分支**：crc32.c 的 `#ifdef DYNAMIC_CRC_TABLE` 分支里定义了
  `x2n_table`，本构建该宏不在命令行 `-D` 里 → 分支没编译 → 名字实际来自被删的
  `crc32.h`。旧位置判据看到「定义在使用之前」就把证据剔掉了。
* **求不了值的复杂 `#if`**：trees.c 的表声明藏在
  `#if defined(GEN_TREES_H) || !defined(STDC)` 里——`STDC` 由包含链里的 zconf.h
  定义，命令行判不了。按「未知=活」处理同样漏掉真依赖。

**修法**：`_blocking_conditional_regions` 拿命令行 `-D` 把 `#ifdef X` / `#ifndef X`
求值成死/活（`#else` 翻转、分开记录半段）；判死的与求不了的区间里的定义**不算
自供给**。只对源码侧做——头文件整个被 include guard 包着，不能这么算。

### 附加：`##` 拼接

`#define l_floatatt(n) (DBL_##n)` 把源码写的 `MANT_DIG` 拼成 `DBL_MANT_DIG`——一个
源码和头文件里都没写过的名字，文本层唯一的线索是幸存头里的字面词缀 `DBL_`。
拼接产物命中即判「在用」，记进 `pasted`。形参一侧（`##n`）不算——拼出来的词随
调用点变化。

## 迭代过程留下的两个教训

1. **每处放宽都要立刻过三语料**。中间版本（`header_provider_ids` 改声明形态后）
   在 Lua 上是对的，但把 doctest/zlib 的 4 条真有用打回了「在用」——供给者口径
   过严，排序 P@5 从 60%/20% 掉到 0%/0%。最终靠集合语义 + 别名链把两边同时做对，
   而不是在两种错的口径里二选一。
2. **条件区间一刀切同样过猛**：把所有条件区间里的定义都剔出自供给（f 版），
   doctest 3 条真有用（定义在激活的 `#ifndef DOCTEST_CONFIG_DISABLE` 里）被误杀。
   命令行 `-D` 求值把「激活分支」找回来，复杂 `#if` 才保守剔除。

## 已知边界（如实记录）

* 复杂 `#if` 表达式不求值：真值依赖包含链里定义的宏（`STDC`）时，区间按「不可信」
  保守处理，方向是多报「在用」（漏推荐），不产生错误建议。
* Lua 还剩 1 条空头承诺（`lcode.c -> limits.h`：编得过但少解析 0 个文件）——
  `float.h` 闭包与其它直接 include 完全重合，属排序粒度问题。
* AST 级信息（命名空间、重载、模板实例化）仍是 IWYU/clang-include-cleaner 的
  优势区；本工具的价值在零依赖、三编译器、把实测放在判定链上。

## 复现

```bash
python experiments/candidate_precision.py \
  --project <lua-5.4.7> --db <compile_commands.json> \
  --compiler <clang> --tus 0 --jobs 8 --check-codegen \
  --out experiments/results/candidate-precision-lua-20261019h.json
```

数据文件：`experiments/results/candidate-precision-{doctest,zlib,lua}-20261019b.json`
（第二轮基线）。最终数据见第四轮报告
（[`candidate-precision-round4-20261019.md`](candidate-precision-round4-20261019.md)，
`…-20261019i.json`）。
