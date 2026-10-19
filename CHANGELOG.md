# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

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
