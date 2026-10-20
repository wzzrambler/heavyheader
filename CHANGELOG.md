# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- **`docs/demo-walkthrough.md` 重写为五分钟演示脚本**：一条候选（`lonely.h`）走完
  「成本热点 → 反事实验证 → 闸门拦下/确认 → CI 门禁」。每一步的命令都在本机实测过，
  输出逐字写进脚本；第三步用三个文件现场复现「编得过但汇编变了」的最小情形
  （`helper()` 的定义由被删的头提供，链接期才暴露），对应真实工程里的 spdlog 案例。
  脚本明确标注演示语料是仓库里造的，真实工程数字必须引 `experiments/results/`。

### 修正

- **`codegen-changed` 不再猜成因**（`verify.py`、`attrib/advice.py`）。原消息写
  「重载解析或条件编译发生变化」，那是把一类已排查案例的成因当成了通则：同一条
  状态也出现在「少了一个函数定义、链接期才暴露」的情形（成因不同，工具没有证据
  区分）。改为只报「两侧汇编不一致，差异原因需人工判断」，并由
  `test_codegen_changed_does_not_claim_a_cause` 锁死。docstring 里 `<iomanip>`
  案例的「起因是重载解析改变」同样改为只陈述实测到的字节数。
- **测试数按实测值书写**：`HEAD` 为 722 passed / 7 skipped，本轮新增 1 例后为
  **723 passed / 7 skipped**；两份 PDF 与 `SUBMISSION.md` 中的数字已同步。
  留档一个容易上当的细节：`tests/test_repo_links.py` 会按仓库根目录下的每个
  `.py` / `.md` 文件各生成一个参数化用例，所以**根目录多放一个临时脚本，
  测试数就会 +1**——本轮一度因此报出 726，清理临时脚本后回落。报测试数前先确认
  根目录干净。
- **`docs/roadshow-qa.md` 的指标用词**：闸门放行候选的「50% 编译不过」原来被写成
  「50% 的假阳性」，与「疑似未使用」判定的假阳性（三语料为 0）不是同一个指标，
  已分开书写，避免两个数字被混读。

### 文档

- **作品介绍文档 27 → 29 页（v6.0）**，按外部评审反馈修订，未新增任何实验：
  - 封面加文档版本号与数据冻结日期（2026-10-21）；
  - 摘要之后新增「**评审速读：请记住三件事**」（方法论三主张 + 证据出处），
    回应「27 页像研发日志、不像评审文档」；
  - 摘要与「评审关注点导览」统一 **25% 的分母口径**（池内 5/20、非端到端准确率/
    召回率、同池换协议 40%、成员仅重合 3 条、复测存活 73%、离散度 11.8 ms > 收益）；
  - 「零假阳性」在 5 处**紧贴真值限定**书写（真值＝删了还能编过；汇编等价真值下
    doctest 是 4/6）；
  - 新增 **0.1「每个大数字从哪里复现」**：zstd −46.6%、CPU −11.4%、汇编闸门拦下的
    假收益、doctest −3.01%、zlib −2.125%、brotli 31 站点、门禁 3/3、25% 与
    已验证层级两遍，各自对应原始记录文件、测量环境与复现命令；
  - 两个计时数字（**4.5 秒/条**静态分析 vs **52 秒/条**验证阶段）在 4.8 与第 0 章
    分开定义，写明分工不同、不可互相印证；
  - 上游 PR 补链接与**实时状态**（2026-10-21 核实：doctest#1212、madler/zlib#1344
    均为 open、未被合并），并明确「只写已提交，不写已被采用」；
  - 创新主张收紧：「全网搜查未见同能力实现」改为「截至指定日期、基于已核读资料的
    检索结论」，第五章标题改为「已核读工具未覆盖的能力（截至 2026-10-21）」。
- **选题报告**同步 PR 实时状态（3 处）。

- **新命令 `verified`：已验证层级**（`evidence.py`、`cli.py`、`report/markdown.py`）。
  只列**编译器证据**撑住的候选：汇编指纹一致 + 省时过噪声线 + 复测成立。起因是
  上一轮的实测——Lua 上 8 条落地收益里 7 条被文本判据判成 `in_use` / `unknown`，
  却全过了汇编闸门；旧报告把推断和证据混在同一句「建议删除」里。三档措辞纪律由
  测试锁死：`equivalent` 只说「删了程序不变」、不许当收益报；`compiles_only`
  **不许说「程序没变」**（反例：删掉 `<iomanip>` 后目标文件 21,289 → 10,183 字节）。
  实测 Lua 两遍：verified 2 / 3 条，**成员零重合**，6 条拿到闸门证据的候选
  **没有一条来自推荐集**——增量全在判据放弃的那一侧。命令头因此写明「只对
  本轮负责」。
- **`verify --tier {all,verified,equivalent,compiles}`**：按证据档过滤，每条结论
  打印档位与复测遍数；小结给分档计数、状态分布（`compile-error` / `improved` /
  `codegen-changed` 各几条）、闸门跑过与拦下数，以及「判据没推荐却被抬上来」的
  条数。只报成功三档会让「没验过」看起来像「没问题」，所以「无证据」也计入。
- **`VerifyOptions.order`（`"rank"` / `"estimate"`）**。`verified` 按预估收益排，
  不按文本判据排：判据按 rank 排时 `in_use` 在 tier 3，`--top 20` 永远轮不到
  省时最多的那几条。代价是探针淘汰率 13/20（65%），所以这一层的命中率**低于**
  按判据排的池内采纳率，两者**不同分母、不可直接比**。

- **新命令 `configs`：同一棵源码树按多套构建配置各扫一遍**（`configmatrix.py`）。
  判定层本来就按每条编译命令里的 `-D` 求值 `#ifdef`，所以不需要新的分析逻辑：
  把同一份 `compile_commands.json` 派生几份、各扫一遍、按 `(单元, 头, 行)` 对齐，
  只报判定变了的候选。实测 zstd 三套配置（`single` / `-DZSTD_MULTITHREAD` /
  `+ -DZSTD_LEGACY_SUPPORT=1`）：候选 90/91/93，**只有 3 条判定不同**，且方向
  全部用手工编译核对过。回答的是 `scan` 回答不了的问题：这条 include 该不该删，
  取决于**这批编译命令有没有打开某个宏**。

- **实现片段的实例化站点**（`attrib/graph.py`、`verify.py`）。同一个源文件里
  多行同名 include，以前一律判「行号歧义」——一条候选、估算按整个闭包、**从未
  实测过**。现在按「那个头带不带 include guard」分两种：带 guard 的，删任何一行
  都不减少解析（guard 让第二次起成为空操作），保留一条候选但收益**按 0 计**并把
  理由写进候选；不带 guard 的是实现片段，每次包含是一次实例化，展开成 N 条
  **站点候选**（各带行号）、收益按片段自身的一次解析算、判据一律「无法判定」
  （每次生成哪些符号要展开 `FN` 才知道），交给实验裁决，且 `verify`
  **强制**过汇编闸门。实测 brotli：31 个站点 0 条可删，28 条 compile-error
  逐个报出未声明的符号，3 条 codegen-changed——关掉强制闸门时这 3 条会退化成
  「no-change（−8 ms）」，也就是**看起来无害的破坏性改动**。
- **副作用分层：`object` 直接拦，`call` 交给编译器裁**（`attrib/usage.py`、
  `verify.py`）。`side_effect_kinds_in` 把名字空间作用域里的可疑语句分成两类：
  带初始化的对象定义（`static X y = register_it();`，文本层看得确凿，仍判「在用」）
  与一句光秃秃的宏调用（展开成静态注册还是展开成一串声明，文本层分不出来）。
  后者判「无法判定」并给候选打 `side_effect_suspected`，`verify` **无视
  `--check-codegen` 开关**强制比汇编——证据从「文本猜的」换成「编译器验的」。
  实测 Lua：10 条这类候选里 4 条 improved（−3.4%~−4.3%，复测通过）、1 条编不过、
  5 条无收益，此前它们全部被判「在用」永远进不了池。
- **宏体里的依赖**（`macro_bodies_in` / `macro_defs`）。源码用的宏若在幸存头里
  定义、而宏体里某个名字只有这条 include 提供，判「在用」，理由写成
  「`lua_getlocaledecpoint` 展开后用到 `localeconv`（由 `locale.h` 提供）」。
  少了这一条，Lua 的 4 条 `→ locale.h` 候选会推荐出编不过的删除。
- **`VerifyOptions.timing_jobs`（默认 1，串行）**。探针阶段仍然并行；计时阶段
  串行。理由见下面的修复条目。

- **scan 报告新增「待验证队列」小节**（`report/markdown.py`）。没跑 verify
  的扫描报告以前把「无法判定」层留给读者自己判断；现在按 `verify` 的测量
  顺序列出队列（疑似未使用 → 无法判定 → 在用），并给出分层计数。依据是
  实测：Lua 语料上「无法判定」层 32/32 条通过三真值——「静态判不了」多数
  是判定层保守，而反事实实验能裁。`verify --top` 的帮助文本同步说明池顺序。
- **基准 JSON 序列化 `estimated_seconds_saved`**（`report/jsonio.py`）。
  秒数预估参与 verify 排序，但此前只进报告不进 JSON，从 JSON 重载的扫描
  （compare / 看板 / 外部脚本）会在分层内退化成按行数排。四工程基准已
  刷新：候选与判定数不变（合计 104 个候选、3 个疑似未使用），预估换锚后
  首位变为 httplib 12.09s / json.hpp 3.42s / helpers-inl.h 1.16s。

### 修复

- **实现片段里的代码不算 TU 的代码**（`has_include_guard` / `extra_source_ids`）。
  brotli 用「无 include guard 的实现片段」组织代码（`*_inc.h`：`.c` 先
  `#define FN …` 再 `#include` 它来实例化），片段里的使用以前**根本没被读到**，
  于是把片段对 `cluster.h` 的依赖判成不存在——推荐层 11 条里 4 条编不过
  （报错位置在 `block_splitter_inc.h:224`，不在被改的 `.c` 里）。现在把**无 guard
  的项目内依赖**的标识符并进 `source_ids`：brotli 推荐层 11 → 4、编不过 4 → 0；
  Lua 那条从第四轮就在的历史假阳性（`luac.c → lopnames.h`）也一并消失，
  推荐层精度 18/19 → **18/18**，P@5/P@10 从 80%/90% 升到 100%/100%。
  doctest 6/6、zlib 1/1 不变。

- **实现片段里的代码不算 TU 的代码**（`has_include_guard` / `extra_source_ids`）。
  brotli 用「无 include guard 的实现片段」组织代码（`*_inc.h`：`.c` 先
  `#define FN …` 再 `#include` 它来实例化），片段里的使用以前**根本没被读到**，
  于是把片段对 `cluster.h` 的依赖判成不存在——推荐层 11 条里 4 条编不过
  （报错位置在 `block_splitter_inc.h:224`，不在被改的 `.c` 里）。现在把**无 guard
  的项目内依赖**的标识符并进 `source_ids`：brotli 推荐层 11 → 4、编不过 4 → 0；
  Lua 那条从第四轮就在的历史假阳性（`luac.c → lopnames.h`）也一并消失，
  推荐层精度 18/19 → **18/18**，P@5/P@10 从 80%/90% 升到 100%/100%。
  doctest 6/6、zlib 1/1 不变。

- **源码侧按 `-D` 求值：死分支里的引用不算「用到」**（`IdentifierIndex.source_live_ids`）。
  换语料换出来的最大一条：zstd `lib/common/pool.c → allocations.h`，
  `ZSTD_customCalloc` / `ZSTD_customFree` 的全部引用都在
  `#ifdef ZSTD_MULTITHREAD` 里，而单线程构建没定义它——旧判定据此判「在用」，
  真值是这个头整个可删：**384 ms → 205 ms（−46.6%），解析文件 184 → 32**。
  两个边界写进了测试：条件指令行本身永远算活（`#ifdef NO_GZCOMPRESS` 决定走哪个
  分支）；求不了值的 `#if` 不算死区（`__cplusplus`、`_WIN32` 由编译器/平台给）。
  三语料三真值零回归（Lua 18/19、doctest 6/6、zlib 1/1）。

- **大范围误判：带属性的声明被当成了「执行」**（`attrib/usage.py`）。判据原来只看
  「语句以 `名字(` 开头且没有返回类型前缀」，于是 UCRT 的
  `_Success_(return != 0) _ACRTIMP char* gets_s(…);`、
  `_CRT_INSECURE_DEPRECATE_GLOBALS(…) extern char* _pgmptr` 全被判成副作用——
  stdio.h / stdlib.h / string.h 的候选永久「在用」。现在抹掉开头那次宏调用后
  **若还剩一条声明**，就按声明处理。Lua 语料因此放开 13 条此前被判「在用」的大额候选。
- **多行 `#define` 的续行被当成名字空间作用域的语句**（`_top_level_statements`）。
  Lua `lstate.h` 的 `#define gco2ts(o) check_exp(…)` 让整个头凭空「有副作用」，
  连带锁死 6 条删除机会。现在预处理指令连同续行整体跳过。
- **「带初始化的对象定义」只认一种写法**（`_defines_an_object`）。原正则只匹配
  `static X y = …`，`static int alpha_id = register_alpha();` 因为多了个类型词漏掉——
  方向错的漏报（动态初始化的对象恰恰最该拦）。改成函数判据。
- **计时阶段的并发把差值的符号交给了机器**（`verify.py`）。基线原先在循环里
  串行测、补丁侧在 `jobs` 宽的池里跑；这台机器一次编译墙钟 215 ms 里只有 29 ms
  是编译器自己的（其余是进程启动 + 文件系统），4 路并发把每个编译拖慢 20–25%
  且抖动随负载变化。实测同一批 10 条候选：计时段 4 路并发 → 基线 267–282 ms、
  **0/10 improved**；串行 → 基线 223–234 ms、**4/10 improved**。两处修：基线先
  在同一个池子里预热再查缓存；计时段默认 `timing_jobs=1`。
- **clang 的 `-ftime-report` 此前从未被解析**（`parse/timereport.py`）。解析
  正则只认 GCC 的「`name :` 数字」形态，而 clang 是「数字列在前、阶段名在后」
  （`0.0395 (100.0%)  Front end`）。后果：所有 clang 扫描一直走「TOTAL 缺失 →
  驱动实测墙钟」兜底，而扫描是并行的，墙钟被多个编译进程争抢——TU 总时长虚高，
  预估、排序、噪声线预筛同源带偏。现在同时支持两种方言与 clang 的
  `Total Execution Time` 汇总行（其墙钟取括号内那个，第一个数字是 CPU 时间），
  `total_wall_seconds` 优先取权威 `TOTAL`（pass 组自己的 `Total` 不是编译总计）。
- **预估锚定从「编译总时长」改为「前端（解析）耗时」**
  （`attrib/graph.py::_estimate_seconds`）。删掉一条 include 只省解析，不省
  代码生成；旧锚把后端成本按行数比例摊进来——实测 Lua `lgc.c→stdio.h` 把
  7.6ms 的真实收益估成 0.339s（虚高约 50 倍），新锚估 0.011s（1.5 倍）。

### 实验与调研

- **第五个语料 brotli 1.1.0：推荐层第一次出现 4 条假阳性**（基准报告 §9.17）。
  `c/` 下 32 TU、2.95 M 行解析量、串行基线 45.0 s；84 条候选（疑似 11 / 无法判定
  8 / 在用 65）。修复实现片段判据后推荐层 4 条全部成立，`verify --top 30`
  实测 **2 improved**：`utf8_util.h ← utf8_util.c` 373 → 200 ms（−46.4%）、
  `quality.h ← compound_dictionary.c` 466 → 379 ms（−18.7%），聚合 −0.58%。
  已知缺口记在案：3 条候选因「同名 include 出现 3–19 次」被判 ambiguous，从未实测。

- **第五个语料 brotli 1.1.0：推荐层第一次出现 4 条假阳性**（基准报告 §9.17）。
  `c/` 下 32 TU、2.95 M 行解析量、串行基线 45.0 s；84 条候选（疑似 11 / 无法判定
  8 / 在用 65）。修复实现片段判据后推荐层 4 条全部成立，`verify --top 30`
  实测 **2 improved**：`utf8_util.h ← utf8_util.c` 373 → 200 ms（−46.4%）、
  `quality.h ← compound_dictionary.c` 466 → 379 ms（−18.7%），聚合 −0.58%。
  已知缺口记在案：3 条候选因「同名 include 出现 3–19 次」被判 ambiguous，从未实测。

### 实验与调研

- **配置矩阵：同一棵源码树三套构建，判定跟着配置走**（`experiments/config_matrix.py`、
  基准报告 §9.16）。zstd `lib/` 按 `single` / `mt`（`-DZSTD_MULTITHREAD`）/
  `mt_legacy` 三种配置各扫一遍，按 `(tu, 头, 行)` 对齐：候选 90/91/93，
  **只有 3 条判定不同**——`pool.c → allocations.h` 在多线程下正确地变成「在用」
  （删了编不过）、`zstd_compress_superblock.h` 从「无法判定」变成「疑似未使用」
  （两边都能编过，方向是保守→推荐）、`pool.c → debug.h` 在多线程下不再是候选。
  结论有两半：判定确实按配置求值且方向正确；但**配置敏感性是局部现象**
  （3/90），做对了不会整体改表，只在关键处改对。

- **批量编辑（杠杆 3）实测：单位对，机会不够**（`experiments/batch_probe.py`、
  基准报告 §9.14）。单条删 1–3 ms 在 220 ms 编译上测不准，那就按「同一个 .c
  里删掉几条」这个开发者真实动作去测：逐条先过「编得过 + 汇编不变」，再按单元
  组批（≤6 条），基线/补丁各取 3 次最小值，整组 ≥2% 且复测通过才算数。
  Lua：77 条开放候选 / 19 个可组批单元 → **4 组过线**（`lapi.c` +4.33%、
  `ltm.c` +2.97%、`lutf8lib.c` +5.57%、`lvm.c` +3.55%），19 个单元合计
  4.22 s → 4.19 s（**−0.73%**），折合全工程约 −0.4%。**这个杠杆不成立**——
  不是测不出来，是 Lua 上已没有可收成的机会。同时更正
  `experiments/results/timing-funnel-lua-20261019.md` 里「全量应用 = 聚合 28%」
  的估计（它用的是锚定修复前的预估，虚高约 50 倍）。
  两个仪器坑记录在案：汇编对比必须用「能解析包含」的那套选项（否则每组都被
  误判「汇编变了」）；批量前必须逐条过闸门（否则 24 组里 9 组整组报废）。

### 变更

- **条件编译区里的声明不再当「另有供给者」**（`IdentifierIndex.header_live_provider_ids`
  / `header_conditional_provider_ids`）。doctest `doctest.h` 里
  `class TraversalState` 整段包在 `#if defined(DOCTEST_CONFIG_IMPLEMENT)` 里，
  这个单元根本不编译那段；按文本算「另有供给」会把 `test_traversal.cpp → traversal.h`
  这条删了就编不过的候选推荐出去。现在这类名字既不算供给者、也不算「谁都不提供」，
  一律判「无法判定」并写清理由——由 verify 的编译裁决。代价是 Lua 队列从 32 条
  涨到 58 条，推荐层从 20 条变成 19 条（三语料合计推荐 26 条，25 条真有用）。
- **消失集合那一侧只认「定义」**（`header_definition_ids`）。`lualib.h` 里
  `LUAMOD_API int (luaopen_base)(…)` 只是**使用**了 `LUAMOD_API`（真定义在
  `luaconf.h`），把「提到」算成「提供」会让源码里的这次使用变成
  「只有这条 include 提供它」的证据，lualib.h 的 5 条候选全被幻影命中顶掉。

- **候选精度第五轮：失败原因分类 + 「分析时间→已证实优化」核心指标**（对照见
  [`experiments/candidate-precision-round5-20261019.md`](experiments/candidate-precision-round5-20261019.md)）。
  新脚本 `experiments/failure_taxonomy.py` 把每条候选的结局分成七类（判定假阳性 /
  空头承诺 / 静默改程序 / 已证实 / 判定正确 / 保守漏报 / 无法实测），并探测
  「被判 in_use 的前 40 条镜像样本」把漏报也量出来。四语料 100 TU：已证实 23 条、
  保守漏报 47 条、**约 4.5 秒分析/条**、每 100 TU 23 条。结论：推送集零假阳性是
  准确（26 条疑似未使用里 1 条漏判），保守挡住的主要是小额机会（47 条里仅 1 条
  预估超 0.1 s），瓶颈在语料冗余度与噪声线而不在算法。
- **修复：驱动层相对路径静默退化**（`GccLikeDriver.run`）。`-H` 打印的路径形态
  跟着编译 cwd 走：编译命令用相对 `-Iinclude` 时，下游消费者按扫描进程 cwd
  解析不到头文件——行数记 0、标识符集为空，用法判定**不报错地**退化（spdlog
  上疑似未使用从 2 条虚增到 7 条）。现在事件解析后按编译 cwd 把相对路径落地，
  两条回归测试锁住（`tests/test_drivers.py`）。
- **修正 spdlog 计时选型结论**：`cfg.cpp -> helpers-inl.h` 的 −77.8% 是**静默改
  程序**——删除后汇编从 10,329 行塌缩到 40 行（删掉的是 `load_levels` 等公共 API
  的实现，`-fsyntax-only` 照样过、链接期才炸）。计时口径的两条「improved」在
  第三条真值（汇编指纹）下全部现形；`corpus-timing-spdlog-20261019.md` 已加补记。

### 变更（第四轮）

- **候选精度第四轮：空头承诺清零**（对照见
  [`experiments/candidate-precision-round4-20261019.md`](experiments/candidate-precision-round4-20261019.md)）。
  Lua 第 2 名候选（`lcode.c -> limits.h`）预估省一大笔、实测少解析 0 个文件——
  根因是 mention 补边把 `#include <limits.h>` 解析到了 UCRT 的同名实现头，而
  编译器（尖括号不搜 includer 目录、clang 内建目录优先）实际解析到 clang 的
  包装头。修法：mention 记录尖/引号形态（**相对路径启发只对引号成立**）、
  新增遮蔽判定（同名候选里「树里由另一个候选激活」的不是解析结果）。Lua
  空头承诺 1→0、P@5 80%→**100%**、候选数 233→222（11 条错误独占的系统头
  候选消失）；doctest/zlib 持平（前几名已被真依赖占满）。
- **候选精度第三轮：第三个语料（Lua）暴露新根因，判定升级为集合语义**
  （对照见
  [`experiments/candidate-precision-round3-20261019.md`](experiments/candidate-precision-round3-20261019.md)）。
  为防过拟合，引入第三个语料 Lua 5.4.7（33 个编译单元）做泛化检验——立刻暴露
  6 条「疑似未使用」假阳性，连带挖出五处根因，全部修复后三个语料的
  `plausibly_unused` 推荐做到**零假阳性**（doctest 3/3、zlib 1/1、Lua 19/20，
  唯一非有用项是编得过但省 0 个文件的空头承诺）：
  * **幸存头「提到」不等于「提供」**：Lua `ldo.h` 在宏展开体里*调用*
    `luaC_checkGC`，真声明在别处——按「文本出现过」算供给会把真依赖判成
    可删。`header_provider_ids` 改为只认**声明形态**（`#define` 名、函数
    原型/定义、类型与对象声明）。
  * **别名链解析**：幸存头里的 `#define X Y` 在 Y 于幸存侧仍有声明者时
    继续让 X 可用（doctest 的警告宏链）；Y 的声明只活在被删闭包里时不算
    （zlib `inflate_fast`）。被删闭包里的别名则随头一起消失（`O_BINARY`）。
    判定从计数升级为**基于集合的精确语义**：graph 传每个名字的真实声明者
    集合与别名定义处，`analyse_usage` 按候选的消失集合逐名字裁决。
  * **环境噪音剔除**：可达集里没有任何头提供的命中（多半是命令行 `-D` 宏，
    如 doctest 的 `DOCTEST_CONFIG_DISABLE`）不再构成保留证据，单独记进
    `unprovided` 并写进报告。这一类幻影命中曾把 doctest 4 条真可删的候选
    全部顶成「在用」。
  * **宏名当类型限定符的对象定义**：`local const ct_data static_ltree[…]`
    （zlib trees.h）、`DOCTEST_THREAD_LOCAL …`（doctest）——旧正则只认
    关键字限定符，这批表定义在头和源里都「没有声明者」。
  * **条件分支求值**：拿编译命令行 `-D` 把 `#ifdef X` / `#ifndef X` 求值成
    死/活分支（含 `#else` 翻转），**判死了的分支**与**求不了值的复杂
    `#if`** 里的定义不再算源码自供给（zlib `crc32.c` 的 `x2n_table` 藏在
    非激活分支里、`trees.c` 的表藏在复杂 `#if` 里，两处真依赖因此凭空消失）。
    求值在源码侧做——头文件整个被 include guard 包着，不能这么算。
  * **`##` 拼接检测**（`usage.paste_affixes`）：`#define l_floatatt(n) (DBL_##n)`
    把源码写的 `MANT_DIG` 拼成 `DBL_MANT_DIG`——一个源码和头文件里都没写过
    的名字。拼接产物命中即判「在用」（Lua `lcode.c -> float.h`）。
  * **对照（同一把尺子，基线 = f9e9397 第二轮）**：Lua 从 17 条「疑似未使用」
    里 6 条编不过，到 20 条里 0 条编不过；doctest P@5 60%→60%（首条有用
    保持第 1 位）且 3/3 全对；zlib 首条有用第 39 → **1** 位、`plausibly_unused`
    1/1 全对；排序口径 P@5：doctest 60%、zlib 20%、Lua 80%——全部远高于
    纯行数口径（Header Hero）的 0%/0%/0%。
- **候选精度第二轮：先把尺子修对，再改算法**（对照见
  [`experiments/candidate-precision-round2-20261019.md`](experiments/candidate-precision-round2-20261019.md)）。
  * **基准加第三条真值轴**（`--check-codegen`，汇编指纹）。原来只有「编不编得过」
    与「少解析几个文件」，两条都不回答「编出来的是不是同一个程序」。加上之后立刻
    抓到 3 条被旧基准算作「真有用」、实际上**静默改程序**的改动：zlib
    `zutil.c -> gzguts.h`（激活的条件编译分支变了）、doctest
    `context.cpp -> junit.h` / `xml.h`（`DOCTEST_REGISTER_REPORTER` 的静态注册整段消失）。
    doctest 那两条当时还排在第 1、2 位。两个工程「有用」的计数因此 6→4、2→1——
    减少的 3 条本来就不该删。
  * **对照（同一把尺子，改前 = a85f087）**：doctest P@5 **0% → 60%**、P@10
    0% → 30%、首条真有用第 **19 → 1** 位、`plausibly_unused` 有用率
    **0% → 100%（3/3）**；zlib P@5 0% → 20%、首条真有用第 40 → 1 位。
  * 四处根因：① 消失集合漏排除「其它直接 include 的头本身」（`closure_of` 不含起点）；
    ② **源码自己定义的名字**不算依赖（新增 `usage.definitions_in`；定义前必须有
    类型样式前缀，所以 `TEST_CASE(one) {` 不会被误认成定义）；
    ③ **定义晚于使用时声明仍然必需**（新增位置判据；doctest
    `exception_translator.cpp` 第 14 行先用、第 19 行才定义）；
    ④ `#define local static` 是宏定义不是别名（展开目标是关键字时提供的是宏名本身）。
  * **新增副作用型 include 检测**（`usage.side_effects_in`）：头文件在名字空间作用域
    执行的动作（无类型前缀的 `NAME(...)`、带初始化的非 const 对象定义）命中即判
    「在用」，并把那句语句原样写进报告。这类依赖文本层本来完全看不见。
  * **一处差点改错的改动已回退**：曾把「只出现在 `#ifdef` 里的宏」降级成不算依赖，
    基准数字变好看了，但代码等价闸门显示汇编变了——那会推荐出一个静默改程序的编辑。
    判定保持 `in_use`，理由单独记进 `conditional_only` 并写进报告。
- **候选识别精度**（对照见
  [`experiments/candidate-precision-20261019.md`](experiments/candidate-precision-20261019.md)）：
  doctest 24 TU 上，空头承诺（编得过但一个文件都没少）从 **23/60 降到 1/38**，
  真有用的候选一条没丢（6→6），首条真能删的位置从第 35 位提到第 **1** 位；
  zlib 35 TU 上候选从 87 降到 47（含去掉 4 条重复），首条真有用的位置 52→26。
  四处根因与修法：
  ① **闭包建在 `-H` 有效树上**，而 `-H` 只记录首次生效的包含点——被 guard 吞掉的
  第二次包含不在树里，于是「删掉这条会少解析 275 个头」全是假的。修法：成本继续用
  有效树，删除预测另建按路径的潜在供给图（同名且无法相对定位时不猜）。
  ② **命中判定不问「谁还会提供它」**：只要源码提到闭包里的名字就算在用，doctest 上
  60/60 全判「正在使用」，而真值有 48% 能删。修法：只认「消失集合是唯一供给者」的
  命中，其余记为 `cross_provided` 并在报告里说明。
  ③ **重命名宏的两侧语义**：幸存头里的 `#define X Y` 只是别名（zlib 的
  `zconf.h` 重命名 `inflate_fast` 让真依赖被判可删 → 剥掉）；被删闭包里的
  `#define X Y` 却是在**定义**宏 X（zlib fcntl.h 的 `O_BINARY` 随头消失，
  第一版误剥导致判成可删 → 保留）。两侧各有一个真实案例与回归测试。
  ④ **行号一直是空的**：`-H` 与 `/showIncludes` 都不报行号，报告里每条候选都写
  「行号未知」。修法：把 verify 的定位逻辑抽成 `heavyheader.includes`，扫描期填入
  （强匹配路径后缀 → 弱匹配文件名 → 同名歧义就放弃）；歧义时列出**全部**候选
  位置（如 minigzip.c 的 `<stdlib.h>` 在第 34、47 行各一处），不再只给「未知」。
- **`verify` 默认开启代码等价校验**（`--no-check-codegen` 可关）。理由是本轮实测：
  `verify --top 3 --runs 2` 只慢 **+12%**，换来的是「建议删除」从「能编过」升级为
  「生成的代码没变」——而 `verify` 正是「要不要合并」的确认步。批量初筛
  （`scan --probe*`）不默认开：那里慢 **+30%**，且 doctest 语料上真阳性为 0，
  收益只在保险，不在发现。
  报告现在会明确写出本轮**做没做**等价校验（`ProjectScan.codegen_checked`），
  「建议删除」在两个口径下含义不同，不能让读者自己猜。
- **闸门的能力边界写进代码**：`-S`/`-g0` 是 GCC/Clang 写法，MSVC 的 `/FA` 未实现。
  硬开会让「编译失败」被当成「代码变了」，**每个候选都误报**——比不开更糟。
  `codegen.supported()` 判能力，不支持时完全不跑，`verify` 另在 stderr 说明。

### 修复

- **代码等价闸门的两类误报**（首轮 doctest 上 16 个候选中 7 个误判）：
  ① 编译数据库带 `-g`，汇编里的 `.cv_file` / `.cv_loc` / `.debug$S` 记录了源文件路径
  与行表，删 include 后代码没变这些字节变了；汇编里路径还是**双反斜杠**转义形式，
  只掩单反斜杠等于没掩。修法：比对时统一加 `-g0`，掩码同时处理转义形式。
  ② doctest 用 `__FILE__` 拼错误信息，两份源码分处不同目录 → 字符串常量不同 →
  连符号名都不同（`??_C@_0KE@…` vs `??_C@_0IK@…`，长度与哈希写在名字里）。
  修法：`compare()` 把两份源码**先后写到同一个路径**再编，`__FILE__`、行标记、
  符号名天然一致——比继续堆掩码规则更彻底。
  修完：16 个候选 0 误报。数据与两次误报的汇编 diff 见
  [`experiments/codegen-gate-cost-20261019.md`](experiments/codegen-gate-cost-20261019.md)。

### 实验与调研

- **伞头拆分实测（doctest，24 个编译单元）**：每个 TU 平均只需要 prelude.h 里
  36 条 include 中的 **4 条**，且各 TU 的最小集合有 **10 种形态**；按需 include 后
  编译时间 **16.94s → 14.14s（−16.55%）**，24 个 TU 的汇编输出**逐字节相同**。
  结论：拆成「宏头 + 声明头」两个固定头几乎不省，收益只在逐文件按需 include 时
  才拿得到。
  方法学记录（比结论更重要）：判据错了三次——「编译通过」放过了删 `<iomanip>`
  后重载解析改变（目标文件 21,289 → 10,183 字节）；「目标文件大小相同」太弱；
  「目标文件哈希相同」不可用（同一输入两次编译哈希就不同）。最终判据是
  **汇编文本哈希**（`-S` 确定性输出），并需剥掉与 `-S` 冲突的 `-c`、固定输出路径
  （clang 会把输出文件名写进汇编）。完整过程见
  [`experiments/umbrella-split-20261019.md`](experiments/umbrella-split-20261019.md)。
- **阻断归因（blocker attribution）：把「删了编不过」从一句话变成可执行信息**——
  此前 222 个候选里 187 条被编译器否决，报告里每条都只写 `compile-error`，使用者
  只知道「不行」。现在解析编译器报错（GCC/Clang/MSVC 三种方言），取出首个阻断符号
  与位置，判定四类处置方向：可改前向声明 / 需完整定义 / 不是类型（宏、函数、枚举）/
  与删除无关。判定保守：先确认符号确实由被删 include（含其传递闭包）提供，再看源码
  用法是否只在指针/引用位置；系统/第三方头直接判「前向声明不适用」（C++ 不许用户
  前置声明 std 成员）。顺带把报错里的临时目录路径压成文件名。
  实测（doctest 24 单元）：7 条被否决的候选全部归因到同一个宏
  `DOCTEST_SUPPRESS_PRIVATE_WARNINGS_PUSH`——这说明它们是伞头依赖，前向声明救不了，
  而不是「工具没招」。10 个单测覆盖三种编译器方言与四类判定。
  先例交代：前向声明替换本身有先例（IWYU；YALLA，CGO'25 实测最高 24.5×），
  本项新增的是**归因**（每条否定给出符号级理由），不是替换动作。
- **`edge-cost` 子命令：按 include 边归因解析成本**——`scan` 回答「哪个头贵」
  （节点），`edge-cost` 回答「源文件里哪一行删掉能省多少」（边），因为开发者能删的
  是那一行。采集 = 一次真编译同时开 `-ftime-trace` 与 `-H`，trace 的嵌套区间还原
  逐文件自耗时，边按其独占闭包求和。clang-first：GCC/MSVC 没有逐文件自耗时，
  明确报错而不退化成估算。报告首行写口径（预测 ≠ 结论，可行性归 `verify`）。
  实现：`edgecollect.py`（采集）+ `report/edges.py`（渲染）+ `attrib/edgecost.py`
  （计算）；6 个测试（含真 clang 端到端与 CLI 落盘，无 clang 时跳过）。
- **`attrib/edgecost.py`：边级边际成本（含潜在供给闭包修正）**——对每条 include
  边算「删掉它后本 TU 不再解析的文件集合」，用 `-ftime-trace` 的自耗时求和得到
  边际毫秒。关键修正：`-H` 只记录每个头**首次生效**的包含点，被 include guard
  吞掉的替代供给点看不见，导致「独占闭包」系统性高估删除收益（doctest 上 25 条
  可删边虚报 45 个文件，7/20 条边承诺了不存在的收益）；补全文本层供给边后
  假阳性 45 → 0、完全一致率 18/25 → 24/25。9 个单测守护语义，其中
  `test_potential_graph_kills_the_false_saving` 是该 bug 的回归锁。
  实验与负面结果见 experiments/edge-precision-20261019.md——**未宣称**
  「毫秒排序比行数排序更准」：墙钟 A/B 显示单条标准头的节省低于噪声，
  两模型与实测的秩相关均为负，这套能力只用于排序候选，结论仍归 verify。
- **`blame` 子命令：commit 级成本归因（成本考古）**——把头文件成本回归归因到
  具体 commit、文件与 include 行。`scan` 在 git 仓库里运行时自动记录 commit；
  `blame` 对两份基线算逐头文件成本差，用 `git diff` 解析 include 行变化，
  以「可达目标且新增/失去它的 TU 集合完全一致」确定影响半径，预测
  `Σ行数 × |TU 集|`。预测与实测分行列出、不相加；解释不了的进「未解释」。
  全网搜查未见同能力工具（GitHub repo search + 官网 + 学术，2026-10-19）。
  端到端验证（真实仓库 + 真实基线）：+56,487 行的回归被精确指名 commit/文件/
  include 行，预测 +56,485 = 实测 +56,485（high），剩余 +2 行如实留未解释。
  记录见 experiments/blame_probe.md；源码 src/heavyheader/blame.py；8 个测试
  （含真实 git 仓库端到端）。基线 JSON 新增 `git_commit` 字段（旧基线缺省
  None，blame 退回 --from/--to）。

### 修复

- **CI 全绿**：修复 7 个跨平台测试失败（此前本地 Windows 全绿、CI 12 矩阵 +
  Coverage 全红，慢性问题）与 macOS 矩阵腿的 3 个 semantic 测试失败。根因均为
  Windows 路径解析的平台依赖：
  - `attrib/cost.py _key`：显式 `lower()`（`os.path.normcase` 在 POSIX 是
    no-op，trace 键查找在 Linux/macOS 全部落空）；
  - `parse/depfile.py` 与 `parse/includetree.py` 的 `_normalise`：
    `PureWindowsPath` 必须先 `as_posix()` 再包平台 `Path`——
    `Path(PureWindowsPath(...))` 的渲染在 Python 3.11/3.12/3.13 上行为
    不一致（单组件 / 混合分隔符 / 分部件）；
  - `scanner._strip_source_token`：改为分隔符/大小写不敏感比较
    （compile_commands 的 `file` 字段与命令行分隔符风格不一致，POSIX 上
    漏剥源文件 token——「编译两遍」计量污染的另一半）；
  - `tests/test_parsers.py`：6 处期望值改为规范形式（平台中性）；
  - `tests/test_no_deps.py`：去掉 3.10 不存在的 `tomllib`（改为 tomllib
    优先 + 3.10 文本回退）——此前 CI 3.10 矩阵腿收集即失败（exit 2）；
  - `tests/test_semantic.py`：三个 `analyse` 测试改用 tmp_path 造真实假
    文件，不再依赖 CI 机器碰巧装有的 LLVM 安装（macOS 两条默认路径都
    没有，是最后一处红腿的根因）；
  - `attrib/semantic.py _detect`：默认路径里的裸命令名改为 `shutil.which()`
    解析（此前 `Path(...).exists()` 查的是 CWD 不是 PATH，是死条目）。
- 这些同时是产品 bug：工具在 Linux/macOS 上解析 MSVC `/showIncludes` 输出
  或 Windows `compile_commands.json` 时会得到畸形成路径。

## [0.1.0] - 2026-10-19

### 新增（首发）

首发即包含 v0.1–v0.3 的全部能力与实测证据。以下为 2026-10-19 收尾轮新增的
治理 / 文档 / 落地材料：

- **`docs/innovation.md`（独创性说明）**：把定位从「闭环组合」改写为
  **反事实构建实验（CBE）**范式——一句话卖点、三项可核查创新点（独占闭包边际
  归因 / A/B 测量协议 / 三态可审计决策）、逐条出处与不声称清单，以及
  「为什么不是堆叠现成工具」的消融表（拿掉任一层都有实测记录在案的错误结论）。
- **`docs/scenario.md`（场景落地）**：把散落的实测证据组织成
  「开源 C/C++ 工程头文件治理」的完整场景叙事——四步全链路、四个真实工程的
  数据卡、部署/运行/测试证据、复现入口。
- **`GOVERNANCE.md`（治理结构）**：角色与贡献者阶梯、决策流程、发布纪律
  （tag → `release.yml`）、依赖政策（零运行时依赖为架构约束）、社区与
  组织支撑路径。
- **`contrib/upstream/`（上游落地战役）**：两个 PR 级补丁（doctest 删
  `prelude.h` 未用的 `<unordered_set>`，实测 −3.01%；zlib 删 `zutil.h` 未用的
  `<stdlib.h>`，实测 −2.125%），均基于上游 HEAD 生成、对全新源码树
  `patch --dry-run` 验证可套用；英文 PR 正文含实测数据与验证边界。
- **`tests/test_no_deps.py`**：CI 守护零运行时依赖约束（pyproject 无
  dependencies + `src/` 只导入标准库），把 GOVERNANCE 的承诺变成会失败的测试。
- **README 修订**：定位改为 CBE 范式；安装段改为源码安装（PyPI 2026-10-19
  核查确认为 404，不挂空头支票，上架列入 ROADMAP v0.4）；修复快速开始的
  编号重复。
- **`.github/CODEOWNERS`**：默认审阅人。

> 以下为 v0.1–v0.3 的原始条目。

### 新增

- **闸门交白卷时的诊断（`attrib/diagnosis.py`）**：`--header-sites` 下，每一条
  收益非零、却被可行性闸门拦下的 include 行都会记录原因；真依赖会附具体标识符
  证据，系统头内部的行单独计数。一条候选都不放行时，报告改用
  「没有可删的了：成本在哪、为什么删不掉」作答——成本集中度、被拦下的收益榜、
  原因分布、下一步建议，并以「任何删除都要先过 `verify`」收尾。
  实测：nlohmann/json 369 条、zlib 58 条被逐条判过，放行 0 条；doctest 放行 16 条。
- **新开关 `--include-system-sites`**：把系统/第三方头文件内部的 include 行也
  列入（仅供查看，不建议改——改别人的头不是本项目能承担的决策）。
- **新开关 `--probe-compile`**：只验「删掉这行还能不能编」，不测时间，成本约为
  `--probe` 的 1/6。报告据此把候选分成「编译探测通过」与「编不过」两类，
  并明写前者不等于省时。
- **新开关 `--allow-unknown-sites`**：把「文本判定给不出结论」的站点也放进候选
  （用一次编译换召回）。这类候选标为低可信度，并在理由里注明是被开关放行的。

### 基准与门槛（G1–G4）

- **G2 重做后成立（有限）**：换头文件级候选 + 交替测量后，doctest（24 TU）的
  16 条闸门候选全部测完——8 条能编过、1 条经复测确认省时（删 `prelude.h` 里的
  `<unordered_set>`，24 单元全通过，聚合 −3.01%），即 **1/16**；关掉闸门后
  zlib 也找到一条（`zutil.h` 删 `<stdlib.h>`，9 单元全通过，3314 → 3244 ms，
  **−2.125%**，复测通过），即 **1/16**；nlohmann/json 为 0/16。据此**仍不宣传
  「自动找到优化」**，对外只表述为「可验证 + 可拦截」；并同时说明**放行的 16 条
  里有一半编译不过**、**闸门会漏**（zlib 那条就是漏的）。
- **闸门的作用被重新定义（三档全量探测）**：放行 16 条通过率 50%、+unknown
  19 条 53%、完全不筛 66 条 47%——闸门对「能否编译」无预测力，它省的是候选
  数量（1/4 的 verify 成本）。另测「闸门是否漏掉收益」：漏掉的 23 条能编候选
  里，初筛唯一过 2% 的复测为 −0.548%，即没有漏掉值得采纳的改动。
- **`verify --confirm`（默认 2）**：判为 improved 后必须复测仍成立才保留结论。
  实测依据：同一候选连测三次 percent_saved 在 1.04%–2.10% 间摆动，improved
  出现 1/3 次；第一轮 improved 的候选复测 0/4 复现。复测不通过者降级为
  `no-change`，并把各遍读数写进理由。
- **门禁缺陷修复**：可用编译单元少于基线时，门禁从「只报警告」改为**直接失败**。
  修复前实测：注入导致一个 TU 编译失败，TU 从 2 掉到 1，总成本算出 −67.7%，
  门禁**通过**——构建坏得越彻底越容易过。
- **候选排序的 JSON 往返修复**：新增序列化 `usage_verdict`，旧基线按
  `usage_label` 反推。此前从 JSON 重载会把全部候选降为 `unknown`，
  「明确在用」的伞头文件排到「疑似未使用」之前。

- **删除建议决策层（`attrib/advice.py`）**：每个候选从「疑似未使用」这类
  可能性措辞，升级为三态明确结论——**建议删除 / 保留 / 需人工判断**。
  判定规则是确定性纯函数（同证据同结论），报告按「功能影响 / 维护成本 /
  构建体验」三个维度列出依据，「需人工判断」附倾向（偏删/偏留）与阈值说明。
- **实测维度扩展**：编译时间之外，新增编译器自报内存分配峰值
  （GCC/Clang `-ftime-report` 的 GGC 列）与目标文件体积（一次完整编译实测）。
  工具链不提供的维度在报告里写「该工具链不提供」，**不写成 0**。
- **复现命令**：报告附 `heavyheader scan … --probe` 完整命令，
  每条实测结论都可在读者机器上重跑。
- `scan --probe` 报告新增「删除建议：值不值得删」小节；候选表新增「结论」列，
  未实测的候选显示「未实测」而不是猜测。

### 变更

- 噪声阈值 `TIME_NOISE_FLOOR`（2%）统一定义在 `attrib/advice.py`，
  `verify` 反向引用——状态列的「improved」与结论列的「建议删除」
  从此共用同一个定义，不会漂移。
- 候选排序在探针后按结论分层：建议删除 > 需人工判断（偏删）>
  需人工判断 > 保留/实测失败。

### 修复

- `drivers/base.py`：`DriverOptions` 新增 `output_path`，GCC 走 `-o`、
  MSVC 走 `/Fo`，并剥离与「产出对象文件」冲突的 `-fsyntax-only` / `/Zs`
  与 `-MD`/`-MF` 系依赖文件参数（后者会把 `.d` 写到调用方工作目录）。
- `scanner.py` / `drivers/base.py`：**compile_commands 模式下源文件被传了
  两次**（数据库自带的 `-c foo.cpp` 留在 flags 里，驱动又追加一次），GCC
  静默编译两遍并打出两条 `-ftime-report` TOTAL，读取方只取第一条——
  扫描/探针/基线的计时与 include 树全部建立在错误命令上。现已在构造任务
  时剥离源文件 token，并加第二道防线；GCC 计时损坏（TOTAL 缺失）时回退
  驱动实测墙钟并标明口径，不再让 0.0 冒充「节省 100%」。
- `verify.py`：带 UTF-8 BOM 的源码（Windows 编辑器默认产出）首行
  `#include` 定位不到——BOM 不是 `\s`，正则匹配不到，于是一个真实存在的
  include 被判「找不到该注释哪一行」，结论从「建议删除」降级为
  「需人工判断」。定位与打补丁两条路径均已处理，补丁保留 BOM。

## [0.1.0] - 2026-10-17

首个可用版本。目标是在真实 C++ 项目上给出可信、可复现的数字。

### 新增

- **编译器驱动**：GCC 9+ / Clang 17+ 走 `-H` + `-ftime-report`，MSVC 走
  `/showIncludes` + 墙钟计时。一次编译同时收集依赖树、分阶段计时、缺 guard 头文件清单。
- **成本归因引擎**：
  - 归因行数 = `头文件行数 × 引用它的编译单元数`
  - 独占闭包计算：只有删掉某条 include 后才会停止解析的头文件才算收益
  - 重复解析检测：`inclusion_events > tu_reach` 是缺 include guard 的确定性信号
  - 使用状态启发式（明确标注为启发式，权威结论来自 `verify`）
- **四个命令**：
  - `scan`：分析源码树，输出 Markdown / HTML / JSON，可存基线、可追加历史
  - `verify`：注释掉候选 include、重新编译、实测耗时差异（每侧重复取最小值）
  - `gate`：CI 门禁，成本回归超阈值则非零退出，并指名最严重的回归头文件
  - `compare`：对比两份扫描结果
- **报告**：Markdown、自包含单文件 HTML（内联 SVG 图表，无外部依赖）、JSON。
- **基线序列化**：纯 JSON，无 pickle，可 diff、可被其他工具消费。
- **`compile_commands.json` 支持**：严格按其中的 flags 编译。
- **61 个单元测试**，CI 覆盖 Linux / Windows / macOS × Python 3.10–3.13。

### 修复

- `parse/depfile.py`：make 规则切分此前按第一个裸冒号切分，Windows 盘符冒号
  （`C:/build/t.o: ...`）会被误判为分隔符，导致目标被截断、其余内容被当成依赖。
  改为切「后跟空白或行尾」的第一个冒号。
- `parse/includetree.py`：MSVC 缩进深度此前按 `缩进 // 固定步长` 计算，而 MSVC
  最浅层本身就有空格。改为相对于观测到的最小缩进计算，步长取相邻缩进层的
  最小差值。
- `attrib/graph.py`：独占闭包的「已付费」并集此前包含候选自身，导致每个候选的
  独占集合恒为空——所有项目都报告「无可优化项」。这个症状与「确实没问题」
  在输出上无法区分，故以回归测试防守。
- `report/jsonio.py`：`RemovalCandidate.usage_label` 漏了序列化，基线文件会丢
  失使用状态信息。

### 已知局限

见 [docs/methodology.md](docs/methodology.md) 第 9 节。要点：

- 行数不等于解析难度，重型模板头的权重被低估；
- 默认 `-fsyntax-only`，不含代码生成阶段成本；
- 使用状态是文本启发式，为副作用而包含的头文件会被误判；
- 跨机器不比较绝对墙钟时间。

[未发布]: https://github.com/wzzrambler/heavyheader/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/wzzrambler/heavyheader/releases/tag/v0.1.0
