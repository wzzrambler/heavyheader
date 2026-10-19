# HeavyHeader

**面向 C/C++ 工程的可验证构建优化与性能回归治理工具** —— 找到候选、真实编译验证、
把可信的结果变成可审计的 CI 基线。

**一句话**：HeavyHeader 把「删掉这行 include 会怎样」从经验猜测变成一次**可复现的
受控实验**——这个范式我们称之为*反事实构建实验（CBE）*：依赖树取自真实编译器、
收益用交替 A/B 实测越过噪声线才算数、结论三态可审计。竞品回答「谁贵」「缺什么」
「还能编吗」；我们回答「删了的**净收益**是多少、有多大把握」。范式说明、三条
创新点与消融检验见 [`docs/innovation.md`](docs/innovation.md)。

[![CI](https://github.com/wzzrambler/heavyheader/actions/workflows/ci.yml/badge.svg)](https://github.com/wzzrambler/heavyheader/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

---

## 它解决什么问题

大型 C/C++ 工程的构建时间是真实的成本：一次全量构建动辄数十分钟到数小时，CI 集群机时、
工程师等待时间都花在里面。而构建慢的最大单一来源，往往不是算法，而是**头文件失控**——
一个被几万个编译单元传递包含的重量级头文件，会让整个项目反复为它付费。

现有的工具各自解决了这个问题的一段。为避免「看起来像 Header Hero」的误解，先说清来源：
本工具的**归因行数**（`头文件行数 × 被包含次数`）与 **Blowup Factor** 的公式，都来自
Niklas Frykholm 2011 年的 [Header Hero](http://bitsquid.blogspot.com/2011/10/caring-by-sharing-header-hero.html)。
它用正则扫 `#include` 得到这个排序；我们用真实编译器的 `-H` 依赖树。它在「谁贵」处停下，
我们继续往下做反事实实测。完整对标（含逐条出处与核实日期）见
[`docs/competitors.md`](docs/competitors.md)。

| 工具 | 能做什么 | 不能做什么 |
|---|---|---|
| [Header Hero](http://bitsquid.blogspot.com/2011/10/caring-by-sharing-header-hero.html)（2011） | 归因行数排序、Blowup Factor；秒级扫全库 | 正则解析（漏宏/条件编译）；只排序，不验证、不量化删后收益 |
| [ClangBuildAnalyzer](https://github.com/aras-p/ClangBuildAnalyzer) | 真实 `-ftime-trace` 的逐头文件累计 ms、包含次数、均值 | 仅 Clang 9+；只描述现状，没有「删了会怎样」；无基线、无门禁 |
| [CompileScore](https://github.com/Viladoman/CompileScore) | `-ftime-trace` / Build Insights 聚合：逐 include 成本、每 TU 时间线、includers 图，VS 内着色 | 仅描述现状；无反事实、无决策、无门禁；Windows / VS 为主，无行数归因口径 |
| [IncludeGuardian](https://includeguardian.io/) | 七类结构建议（缺 guard、最贵 include、PCH、拆分…），token 级估算 | 不编译、不实测；无三态决策；仅 Win/Linux x64 二进制 |
| [deheader](http://www.catb.org/~esr/deheader/)（2010） | 注释掉一条 include、重新编译看是否通过；`-r` 可自动删 | 二值结论，不量化收益；跳过 `#if` 区域；无收益阈值 |
| [include-what-you-use](https://include-what-you-use.org/) | AST 级判断 include 是否必需 | 不给成本量化，无法按收益排序 |

HeavyHeader 不声称任何单件能力首创（每件都有更早的先例，见上表出处）。它换掉的是
**范式**：从「排行榜」到「反事实实验」——
**真实编译器 `-H` 取依赖树 → 独占闭包算出每条 include 的边际收益、筛出候选 →
对候选做 A/B 交替实测（会话内噪声线 + min-of-3 + improved 强制复测）→
用实测 Δ时间/Δ内存/Δ体积给出 `建议删除 / 保留 / 需人工判断` 三态结论 →
按实测净收益门禁。**
「组合」与「创新」的界线是可消融的：拿掉其中任何一层，都有实测记录在案的错误结论
在等着（排序失效、2% 漂移变收益、4 条 improved 复测 0/4……）——消融表见
[`docs/innovation.md`](docs/innovation.md) §4。

> **诚实定位（2026-10-19 复核）**：发现成本（谁最贵）**不是**我们的优势——
> ClangBuildAnalyzer 与微软 C++ Build Insights 都已覆盖，且数据来自编译器内省；
> 同任务对照（G1）实测 7/10 重合、ρ 0.68，**不支持「我们更准」**。
> 差异化在链条的后两段：**候选发现**（竞品也做）→ **反事实验证**（真删、真编、
> 比汇编，竞品到此为止）→ **回归治理**（把证实过的基线接进 CI 门禁，持续监控
> 成本变化）。「指出哪里可能有问题」不构成壁垒，「实际移除并证明程序没变」加上
> 「此后每次提交都被同一把尺子量」才构成。
> 独创宣示只讲「反事实实验协议 + 三态可审计决策」，逐条证据见
> [`docs/innovation.md`](docs/innovation.md)；能力矩阵与逐格证据等级见
> [`docs/competitor-matrix.md`](docs/competitor-matrix.md)，同任务对照的
> 预注册协议与 G1–G4 数据见 [`docs/benchmark-report.md`](docs/benchmark-report.md)。
> 仍不声称任何「首创 / 唯一」，负面对照样例全部主动公开。

## 核心能力

```
$ heavyheader scan ./src --format html -o report.html
2 TU · 425 条依赖边 · 206,100 行头文件解析量 · 5 个成本候选（其中 1 个疑似未使用）
```

| 命令 | 作用 |
|---|---|
| `scan` | 分析源码树，产出 Markdown / HTML / JSON 报告，可存为基线 |
| `verify` | 对候选 include 真的删掉、重新编译、实测耗时差异 |
| `gate` | CI 门禁：头文件成本相对基线回归超阈值则流水线失败 |
| `compare` | 对比两份扫描结果，定位成本上升的具体头文件 |
| `blame` | 成本考古：把头文件成本回归归因到 commit、文件与 include 行 |
| `edge-cost` | 按 include **边**归因解析成本（clang-first，需真编译一次）；预测排序用，结论仍归 `verify` |
| `toolchains` | 同一份代码在多个编译器下的成本对比，支撑工具链迁移决策 |

`scan` 另有两个可选分析：`--pch` 估算预编译头收益，`--forward-decl` 寻找可改为
前向声明的 include。两者都是估算，报告内附限制说明。

`verify` **默认**做代码等价校验（`--no-check-codegen` 可关），`scan --probe` 需显式
加 `--check-codegen`：删除后额外比对汇编指纹，拦住「编译通过但生成代码变了」的候选
（例如删掉 `<iomanip>` 后重载解析改变）。这类改动在别的工具眼里是「可以删」，
在本工具里是「需人工判断、不计收益」。成本实测：`verify` +12%，批量探测 +30%
（见 `experiments/codegen-gate-cost-20261019.md`）。MSVC 无 `-S`/`-g0` 等价写法，
该校验会跳过并在报告里写明——不会假装做过。

`edge-cost` 与它们的分工：它回答「每条 `#include` 删掉后少解析多少」——按**边**
而不是按头文件归因，因为开发者能删的是源文件里的那一行。它算的是**预测**，
不判断能不能删（那是 `verify` 的事）；闭包只统计真会消失的文件，被别处
include guard 拉住的头不计入（口径与实测见
[`experiments/edge-precision-20261019.md`](experiments/edge-precision-20261019.md)）。

### 1. 成本归因（`scan`）

归因行数 = `头文件行数 × 引用它的编译单元数`。它衡量一个头文件让整个项目多解析了多少行
代码——这是构建耗损最直接的代理指标，且对同一份代码是确定性的。

报告同时给出 **Blowup Factor**（`全项目解析行数 ÷ 唯一文件行数`，与 Header Hero 同定义）：
一个数字说明「平均每行被解析了多少次」。总量只说明项目多大，这个比值才说明结构是否失控。

同时给出四类发现：

- **缺少 include guard 的头文件**：同一个编译单元内被重复解析。对带 guard 的头文件来说
  这在物理上不可能发生，所以这是确定性信号；补上 `#pragma once` 永远安全且收益立竿见影。
- **独占闭包**：删掉某条 include 后，只有它会停止被解析的头文件集合。如果某个头文件还能
  从别的 include 到达，删掉当前这条**并不省钱**——这是本工具与普通 lint 工具的本质区别。
  这里有两张图，不能混用：**成本**按 `-H` 有效树算（一个头只解析一次才叫成本），
  **删除预测**要按补全后的供给图算（`-H` 只记录首次生效的包含点，被 guard 吞掉的
  第二次包含同样是一条到达路径）。混用的后果实测过：doctest 上 23 条候选承诺省
  27 万行，删掉后一个文件都没少。
- **三种「在用」的理由分得开**：① 源码自己就定义了这个名字（本文件的函数定义、
  形参名、宏），不算依赖；② 名字只出现在 `#ifdef`/`#ifndef` 里——删除不会编译失败，
  但会**换一个分支被编译**，所以仍然拦住并写明理由；③ 头文件在名字空间作用域里
  **执行**了动作（如 `DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter);`），
  这类 include 靠副作用起作用，源码一个名字都没提到也不能删——实测删掉它编译通过、
  少解析一个文件，而 JUnit reporter 的静态注册整段消失。
- **候选精确到行**：`-H` 与 `/showIncludes` 都不报行号，所以由源码定位填入；同名头
  有歧义时明确拒绝（不猜），而不是随便挑一行让人删错文件。
- **模板实例化成本**（始终输出）：`-ftime-report` 从 GCC 9 起就报告这个阶段，但对模板重型
  C++ 它常是最大单项——实测包含 `nlohmann/json.hpp` 的文件，**39% 的构建时间**花在这里。
  每个编译单元的秒数与占比是精确的；按头文件的「模板表面」是启发式，报告里明确标注。
- **可改为前向声明的 include**（`--forward-decl`）：编译时间优化里收益最高的一类。
  保守规则 + 给出具体类型名、使用行号和阻断类型，供人在数秒内确认或否决。

**候选为 0 时不会只给一张空表。** 打开 `--header-sites` 后，头文件里的 `#include`
也进入候选空间（一条公共头里的冗余 include，会被每个解析它的单元反复付费），同时
每一条收益非零、却被可行性闸门拦下的行都会记录原因。一条都不放行时，报告改用
「成本在哪、为什么删不掉」作答：成本集中度、收益最高的几条分别被什么挡住（具体到
标识符，或「位于系统头内部」）、原因分布、以及下一步该换什么手段。

实测口径：nlohmann/json 上 369 条收益非零的 include 行被逐条判过——68 条是真依赖
（带标识符证据）、299 条位于系统/第三方头内部、2 条无法判定，放行 0 条。这份清单
比一句「没有建议」有用，也比硬凑 5 条删不动的建议诚实。

**闸门放行 ≠ 删了能编。** 它是文本启发式，实测在 doctest 上放行 16 条、其中
**8 条编译失败**。所以还有两个档位可选：

- `--probe-compile`：每条候选删一次、编译一次，只回答「删了还能编吗」，
  成本约为 `--probe` 的 1/6。报告会把候选分成「编译探测通过」与「编不过」两类，
  并明写「能编译不等于省时」。实测（doctest，66 条候选）：放行 16 条通过率
  50%，完全不筛 66 条通过率 47%——**闸门对「能否编译」没有预测力**，它省的是
  候选数量（verify 成本降到 1/4），不是每条的质量；
- `--allow-unknown-sites`：把「文本判定给不出结论」的站点也放进来（这类命中的
  全是 `config` 这种几十个头都声明的环境噪音）。它们会被标为低可信度并注明
  是被开关放行的——用一次编译去换召回，结论仍只由 `verify` 给。

### 2. 实测验证与明确结论（`verify` / `scan --probe`）

成本模型再严谨也仍是模型。`verify` 会注释掉候选 include、重新编译、计时对比：

```
## lonely.h ← main.cpp
   状态：improved — 基线 520 ms → 移除后 420 ms
   实测节省：100 ms (19.2%)

## core.h ← util.cpp
   状态：compile-error — 移除后编译失败：util.cpp:3:14: error: 'Core' was not declared
```

每侧重复编译 N 次取**最小值**——噪声只会让测量变慢，不会变快，所以最小值是最接近真实成本的
估计。编译失败同样是被记录的结果，不会被静默丢弃。

**判定要通过复测（`--confirm`，默认 2）。** 只测一遍的话，1%–2% 的「收益」可能只是这次
恰好测快了：同一批候选连测三次，读数在 1.04%–2.10% 之间摆动，`improved` 出现 1/3 次。
所以任何被判为 improved 的候选会**再独立测一遍**，两遍都过阈值才保留结论，否则降级为
`no-change`，并在理由里写明各遍读数。

代价要说清楚：复测只对 improved 的候选执行，判定成本约为单遍的 **2 倍**（每多一遍 =
基线 N 次 + 移除后 N 次编译）；no-change 与编译失败的候选不额外付钱。想回到旧行为用
`--confirm 1`，但要接受上面那个 1/3 的翻转率。

**每个候选最终拿到一个明确结论，而不是「疑似未使用」。** 结论按三个维度给出依据：

| 维度 | 回答什么 | 数据来源 |
|---|---|---|
| 功能影响 | 删了之后还能不能编、哪些标识符会消失 | 真实编译 + 独占闭包标识符分析 |
| 维护成本 | 删除让整个项目少解析多少代码、还有几个使用单元 | 独占闭包 + 全项目引用统计 |
| 构建体验 | 编译时间、编译器内存分配、目标文件体积各差多少 | 逐侧实测（每侧 3 次取最小） |

三态结论：**建议删除**（编译通过且耗时下降超过噪声线）、**保留**（编译器已否决，
不必再试）、**需人工判断**（编译可行但证据不足，附倾向与理由）。未实测的候选显示
「未实测」——没测过就不下判断。报告附完整复现命令，每条结论都能在读者机器上重跑。

> **编译通过 ≠ 功能正确。** 「建议删除」的全部含义是「这个编译单元不带这行也能编、
> 而且更快」；合并前请运行对应测试。

可选 **`--semantic`** 开关会再请 LLVM 的 `clang-include-cleaner` 做一次 AST 级交叉检查：
当它把同一个 include 也判定为可删时，作为佐证写进「建议删除」理由。设计上**只增强、
不削弱**——工具的沉默（没列为可删）不等于「在用」，绝不据此降级我们的结论；工具缺失时
自动跳过，结论仍以本工具实测为准。详见 [ROADMAP](ROADMAP.md) v0.3。

静态判定在三个语料上做过对照实验（doctest / zlib / Lua，见
[`experiments/candidate-precision-round3-20261019.md`](experiments/candidate-precision-round3-20261019.md)）：
「疑似未使用」的推荐在三个语料上**零假阳性**——没有一条会推荐出编不过的删除；
首条真有用的候选都排到第 1–2 位，而按纯行数排序（Header Hero 口径）要翻遍整张表。
判定是纯文本层的：副作用型 include、`##` 拼接、别名链、命令行 `-D` 的条件分支
求值都在这一层完成，不依赖 AST。

「命中率低」的瓶颈也用失败原因分类量化过（[`experiments/candidate-precision-round5-20261019.md`](experiments/candidate-precision-round5-20261019.md)，
四语料 100 个编译单元）：候选推送的浪费率约 4%，判定层的主要误差是**保守漏报**
（47 条，集中在 UCRT 宏副作用误判与 unknown 层），且 47 条里只有 1 条预估超过
0.1 s——保守挡住的主要是小额机会。核心指标**「分析时间 → 已证实优化」为约 4.5 秒/条
（每 100 TU 23 条）**；计时口径的大额候选另有静默改程序的实例（spdlog 上 −78% 的
「提升」实为删掉公共 API 的实现，由汇编闸门拦下）。

### 3. CI 门禁（`gate`）

```
$ heavyheader gate ./src --baseline baseline.json --max-regression 5
头文件解析量：206,100 → 241,300 (+17.1%)
编译耗时：0.87s → 1.02s (+17.2%)
门禁失败：
  - header cost regressed +17.1% (206100 -> 241300 attributed lines), budget is +5.0%
    worst offender: include/bloated.h 0 -> 22312 lines (+100.0%)
```

失败的构建会**指名道姓**说出是哪三个头文件、哪些 include 行引入的。一个只说「成本回归
+12%」的门禁毫无用处；能定位到人的门禁才会被留下来。

## 安装

无第三方运行时依赖（Python 3.10+ 标准库），当前从源码安装：

```bash
git clone https://github.com/wzzrambler/heavyheader.git
cd heavyheader
pip install -e .
```

PyPI 上架列入 [ROADMAP](ROADMAP.md) v0.4；发布纪律（tag → `release.yml`）见
[GOVERNANCE.md](GOVERNANCE.md)。

## 快速开始

```bash
# 1. 扫描（自动识别 compile_commands.json，或递归查找源文件）
heavyheader scan ./my-project --compiler g++ --std c++17 -I include

# 2. 生成可分享的 HTML 看板
heavyheader scan ./my-project --format html -o report.html

# 3. 实测验证前 5 个候选
heavyheader verify ./my-project --top 5 --runs 3

# 4. 附带估算预编译头收益与前向声明机会
heavyheader scan ./my-project --pch --forward-decl

# 5. 接入 CI：保存基线，之后每次 PR 对比
heavyheader scan ./my-project --save baseline.json
heavyheader gate ./my-project --baseline baseline.json --max-regression 5

# 6. 增量扫描：第二次起只重编受改动影响的编译单元
heavyheader scan ./my-project --cache .heavyheader-cache.json

# 7. 历史趋势：每次扫描追加一行，随时回看成本怎么变
heavyheader scan ./my-project --history cost.jsonl
heavyheader history cost.jsonl                      # 终端摘要（可直接进 CI 日志）
heavyheader history cost.jsonl --html board.html    # 导出一份自包含网页
heavyheader history cost.jsonl --serve              # 本地看板（http://127.0.0.1:8080）

# 8. PR 评论：把两次扫描对比渲染成评论，可选经 gh 发布到 PR
heavyheader scan ./my-project --save base.json
heavyheader scan ./my-project --save pr.json
heavyheader pr-comment base.json pr.json                       # 只看渲染正文
heavyheader pr-comment base.json pr.json --post                # 经 gh 发布（需 Actions 环境）
heavyheader pr-comment base.json pr.json --post --fail-on-regression

# 9. 成本考古：门禁挂了？自动定位是哪个 commit、哪个文件、哪条 include
#    （scan 在 git 仓库里运行时会自动记录 commit，blame 无需手记 revision）
heavyheader blame base.json pr.json --repo . --format md

# 10. 边级成本：每条 include 删掉后少解析多少（需要 clang 系工具链）
heavyheader edge-cost ./my-project --compiler clang++ --top 20

# 11. 多编译器对比：同一份代码在不同工具链下的成本与耗时差异
heavyheader toolchains ./my-project --compilers g++,clang++
```

### 增量扫描

全量扫描要真编译每一个编译单元，改一行代码也要全跑一遍。加 `--cache` 之后，
只有「源文件、依赖头、编译选项」三者之一变了的编译单元才会重新编译：

```
$ heavyheader scan ./my-project --cache .cache.json
增量：复用 47/54 个编译单元（跳过 47 次编译，87%），重编 7 个。
```

判定分两级：文件的 size 与 mtime 都没变就**不读文件内容**；mtime 动过才比
内容哈希——所以 `git checkout`、`touch` 不会让整个项目白白重编。缓存损坏或
编译选项变化会自动退回全量扫描，不会给出过时的结论。**编译失败的单元不进缓存**：
它的依赖集不完整，等缺失的头文件被补上，缓存里那批依赖「看起来没变」，
会一直重复报同一个错误。

### PR 评论

把两次扫描（`--before` 基线、`--after` PR 扫描）的对比渲染成一段 GitHub 风格
Markdown 评论——含门禁结论、指标对比表、上升/下降的头文件、可精简的 include 候选。
加 `--post` 会经 `gh` 发布到 PR；不加则只把正文打印到标准输出，方便落进 CI 日志
或自行粘贴。发布是**幂等**的：带隐藏 marker，PR 反复推送时编辑旧评论而不是堆积新评论。

```
$ heavyheader pr-comment base.json pr.json --post
已发布 PR #42 的评论（仓库 owner/repo）。
```

`--fail-on-regression` 让成本回归超阈值时退出码为 1，使 CI 步骤失败；`gh` 缺失或
拿不到 PR 号时退出码为 2，且正文照常打印，绝不静默丢失。发布依赖 GitHub Actions
自带的 `gh` CLI，不引入任何第三方库。

### 多编译器对比

`scan` 回答「一个工具链下，成本花在哪些头文件上」；`toolchains` 回答「换一个
编译器，是更快还是更慢、差在哪」。它对同一份代码**依次**跑每个编译器（串行，
避免并行争抢 CPU 把耗时抬高），输出一张矩阵：每个编译器的 TU 数、归因行数
（全部 / 项目头 / 非项目头）、依赖边、耗时与相对最快，以及项目头文件在各编译器
间的差异表。

```bash
$ heavyheader toolchains ./my-project --compilers g++,clang++
$ heavyheader toolchains ./my-project --compilers g++,clang++ --format json
```

三个刻意的取舍：**编译器缺失不会让整张矩阵失败**——如实标成「失败：找不到编译器」，
其余编译器照常对比；**耗时只作参考**（本机、`-fsyntax-only`、随负载变化），结构指标
（TU / 归因行数 / 依赖边）才是确定性的；**逐头文件差异只比项目自己的头**——系统头在
不同工具链下路径各不相同，按路径比较只会制造假差异。

## 真实工程上的完整构建对照

先前的基准都在证明「工具能读到正确的数据」。这个实验回答另一个问题：**按它的结论
动手，完整构建到底快了多少**。

对象是 nlohmann/json v3.11.3 的测试目标（63 个编译单元，源码按 SHA-256 固定），
两臂各 5 次干净构建：

| 指标 | 基线 | 加 PCH | 结果 |
|---|---:|---:|---|
| 完整构建墙上时间（中位数） | 43.2 s | 40.1 s | **−7.1%** |
| 编译 CPU 合计（中位数） | 292 s | 259 s | **−11.4%** |
| 63 个测试套件断言数 | 4,264,414 | 4,264,414 | **逐个一致** |

估算与实测**分两张表**，从不合并：工具估算「消掉 91.2% 的头文件解析量」，实测是
「编译 CPU −11.4%」。反推下来头文件解析约占编译 CPU 的 17%——两个数不同是因为
它们量的不是同一件事。

对照实验（`experiments/control.py`）在同一个工程上还得出一个**对工具不利**的结论：
在「逐个删 include」这件事上，按调用频次排序的经验法则命中 3/12，而工具的候选排序
命中 0/5——因为它按收益排序，而收益最大的头正是所有单元都必需的头。这条缺陷连同
复现代码一起写在 [experiments/README.md](experiments/README.md) 里。

排序缺陷已修并经过零次构建试验的回顾性检验（`experiments/rank_check.py`）；当前版本
在每个候选上给出明确结论与实测依据（`experiments/decision_check.py`，222 个候选全部
真实编译）：**建议删除 1、保留 187、需人工判断 34**。精度仍然落后于 AST 级工具——
187/222 编译不过是事实，不是噪声；报告因此把「保留」也当作一等公民展示。

```bash
python experiments/fetch.py            # 拉取并校验固定快照
python experiments/run_experiment.py   # 完整构建前后对照
python experiments/control.py          # 与经验法则的对照
python experiments/decision_check.py   # 删除建议的明确结论 + 实测维度 + 竞品交叉
```

## 支持的工具链

| 编译器 | 依赖树 | 分阶段计时 |
|---|---|---|
| GCC 9+ | `-H` | `-ftime-report` |
| Clang 17+ | `-H` | `-ftime-report` |
| MSVC | `/showIncludes` | 墙钟计时（MSVC 无等价的文本格式） |

有 `compile_commands.json` 时会严格按其中的 flags 编译——否则同一份代码在不同 flags 下的
数字没有可比性。

## 诚实的边界

这个工具**不**做什么，和它做什么同样重要：

- **不判定 include 是否「未使用」的语义真相**。文本启发式只回答「源码里有没有出现该头文件
  声明的标识符」，为副作用而包含的头文件（如 `<iostream>` 的流初始化）会被误判。权威结论
  来自 `verify`：真的删掉、重新编译。
- **「建议删除」不等于「功能正确」**。它只保证这个编译单元不带这行也能编、而且更快；
  跑测试是开发者的步骤，报告里明写这一条。
- **候选池的精度不够**。实测数据：nlohmann/json 的 222 个候选里 187 个编译不过、
  1 个实测有效。「能给明确结论」不等于「结论都对」——本工具的进步在结论形态与证据
  维度，不在命中率。可选 AST 交叉检查 `--semantic`（subprocess 调用 clang-include-cleaner）已实现（见 [ROADMAP](ROADMAP.md) v0.3）；完整语义判定仍是未来方向。
- **前向声明建议必须人工确认**。「只以指针/引用形式使用」是语义属性，我们用正则判断，
  可能被宏、typedef、模板参数欺骗。报告给出具体类型名、使用行号和阻断类型，就是为让人
  在数秒内确认或否决。
- **AST 级分析是可选的，且只作交叉检查**。`--semantic` 会请本机的 `clang-include-cleaner`
  做一次 AST 校验，但默认关闭——默认路径仍保持零依赖、能在最朴素的 CI 容器里跑起来；
  工具缺失时静默跳过。完整语义判定（进程内 AST 后端）仍是未来方向，原因见 [ROADMAP](ROADMAP.md) v0.3。
- **可行性闸门会漏掉少数可删项**。它宁可交白卷也不推荐「删了编不过」的行，代价是
  保守：zlib 的 `zconf.h → <limits.h>` 被判「在用」（`UINT_MAX` 等宏），实测却编得过
  ——只是仅省 0.356%，低于聚合噪声线。准确的说法是「放行的候选里编译失败 0 条」，
  不是「不会漏」。见 `docs/benchmark-report.md` §9.7。
- **不改写你的代码**。`verify` 只在临时目录里做实验，原始文件一个字节都不会动。
- **不跨机器比较绝对耗时**。墙钟时间随负载波动，门禁因此只对比确定性的归因行数和相对比例。

完整的方法论、指标定义和已知局限见 [docs/methodology.md](docs/methodology.md)。

## 架构

```
src/heavyheader/
├── drivers/        # 向编译器提问：GCC/Clang/MSVC 各自的方言
├── parse/          # 解析 -H 依赖树、-ftime-report 计时、make 风格依赖文件
├── attrib/         # 成本归因：依赖图、独占闭包、重复解析检测、使用启发式、删除建议决策
├── verify.py       # 实测验证：注释掉、重编译、计时，并给出明确结论
├── scanner.py      # 编排：发现编译单元 → 并行编译 → 汇总统计
├── baseline.py     # 基线、对比、CI 门禁
└── report/         # Markdown / HTML（自包含单文件）/ JSON 渲染
```

设计只有一条主线：**不自己解析 C++**。工具向真实编译器提问并读取它的回答，因此分析结果在
构造上是精确的——不存在一个会出细微差错的手写预处理器。详见
[docs/architecture.md](docs/architecture.md)。

## 参与贡献

欢迎 Issue 和 PR。请先读 [CONTRIBUTING.md](CONTRIBUTING.md)——尤其是「什么类型的贡献
最被需要」一节，它能帮你把力气花在刀刃上。

行为规范见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)，安全问题见 [SECURITY.md](SECURITY.md)。

## 许可证

[Apache-2.0](LICENSE)。选择它的理由：对贡献者和使用者都明确，含专利授权条款，
且与生态中大量 C++ 项目的许可证兼容。
