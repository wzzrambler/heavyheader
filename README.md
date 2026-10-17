# HeavyHeader

**C/C++ 构建效能治理平台** —— 用编译器自己的输出，回答「这个头文件让我多等了几秒」。

[![CI](https://github.com/wzzrambler/heavyheader/actions/workflows/ci.yml/badge.svg)](https://github.com/wzzrambler/heavyheader/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

---

## 它解决什么问题

大型 C/C++ 工程的构建时间是真实的成本：一次全量构建动辄数十分钟到数小时，CI 集群机时、
工程师等待时间都花在里面。而构建慢的最大单一来源，往往不是算法，而是**头文件失控**——
一个被几万个编译单元传递包含的重量级头文件，会让整个项目反复为它付费。

现有的工具能告诉你「这个 include 该不该删」，但**没有一个工具回答「它到底让我多等了几秒」**：

| 工具 | 能做什么 | 不能做什么 |
|---|---|---|
| [include-what-you-use](https://include-what-you-use.org/) | 基于 AST 判断 include 是否必需 | 不给成本量化，无法排序优先级 |
| [cpp-dependencies](https://github.com/tomtom-international/cpp-dependencies) | 分析模块间依赖关系 | 面向架构评审，不回答构建耗时 |
| [include-gardener](https://github.com/fedorbondarczyk/include-gardener) | 可视化 include 关系图 | 只展示结构，不给归因 |
| IDE 的「Include Graph」 | 查看单个文件的包含树 | 手动、单文件、无项目级视图 |

HeavyHeader 的差异化在于：**它不自己解析 C++**。它让真实编译器报告自己做了什么，再把
编译器的真实计时数据与依赖图做交叉归因，输出**按预期收益排序**的精简清单，并且可以**真的
删掉、重新编译、实测验证**。

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

`scan` 另有两个可选分析：`--pch` 估算预编译头收益，`--forward-decl` 寻找可改为
前向声明的 include。两者都是估算，报告内附限制说明。

### 1. 成本归因（`scan`）

归因行数 = `头文件行数 × 引用它的编译单元数`。它衡量一个头文件让整个项目多解析了多少行
代码——这是构建耗损最直接的代理指标，且对同一份代码是确定性的。

同时给出四类发现：

- **缺少 include guard 的头文件**：同一个编译单元内被重复解析。对带 guard 的头文件来说
  这在物理上不可能发生，所以这是确定性信号；补上 `#pragma once` 永远安全且收益立竿见影。
- **独占闭包**：删掉某条 include 后，只有它会停止被解析的头文件集合。如果某个头文件还能
  从别的 include 到达，删掉当前这条**并不省钱**——这是本工具与普通 lint 工具的本质区别。
- **模板实例化成本**（始终输出）：`-ftime-report` 从 GCC 9 起就报告这个阶段，但对模板重型
  C++ 它常是最大单项——实测包含 `nlohmann/json.hpp` 的文件，**39% 的构建时间**花在这里。
  每个编译单元的秒数与占比是精确的；按头文件的「模板表面」是启发式，报告里明确标注。
- **可改为前向声明的 include**（`--forward-decl`）：编译时间优化里收益最高的一类。
  保守规则 + 给出具体类型名、使用行号和阻断类型，供人在数秒内确认或否决。

### 2. 实测验证（`verify`）

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

无第三方运行时依赖，Python 3.10+ 即可：

```bash
pip install heavyheader
```

或从源码：

```bash
git clone https://github.com/wzzrambler/heavyheader.git
cd heavyheader
pip install -e .
```

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

# 4. 接入 CI：保存基线，之后每次 PR 对比
heavyheader scan ./my-project --save baseline.json
heavyheader gate ./my-project --baseline baseline.json --max-regression 5

# 5. 增量扫描：第二次起只重编受改动影响的编译单元
heavyheader scan ./my-project --cache .heavyheader-cache.json

# 6. 历史趋势：每次扫描追加一行，随时回看成本怎么变
heavyheader scan ./my-project --history cost.jsonl
heavyheader history cost.jsonl                      # 终端摘要（可直接进 CI 日志）
heavyheader history cost.jsonl --html board.html    # 导出一份自包含网页
heavyheader history cost.jsonl --serve              # 本地看板（http://127.0.0.1:8080）

# 7. PR 评论：把两次扫描对比渲染成评论，可选经 gh 发布到 PR
heavyheader scan ./my-project --save base.json
heavyheader scan ./my-project --save pr.json
heavyheader pr-comment base.json pr.json                       # 只看渲染正文
heavyheader pr-comment base.json pr.json --post                # 经 gh 发布（需 Actions 环境）
heavyheader pr-comment base.json pr.json --post --fail-on-regression
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
- **前向声明建议必须人工确认**。「只以指针/引用形式使用」是语义属性，我们用正则判断，
  可能被宏、typedef、模板参数欺骗。报告给出具体类型名、使用行号和阻断类型，就是为让人
  在数秒内确认或否决。
- **不做精确的 AST 级分析**。要精确判定需要 libclang，那会把工具的使用门槛抬到需要装一整套
  LLVM 工具链——这是我们刻意避开的 trade-off，精确模式在 [ROADMAP](ROADMAP.md) 里。
- **不改写你的代码**。`verify` 只在临时目录里做实验，原始文件一个字节都不会动。
- **不跨机器比较绝对耗时**。墙钟时间随负载波动，门禁因此只对比确定性的归因行数和相对比例。

完整的方法论、指标定义和已知局限见 [docs/methodology.md](docs/methodology.md)。

## 架构

```
src/heavyheader/
├── drivers/        # 向编译器提问：GCC/Clang/MSVC 各自的方言
├── parse/          # 解析 -H 依赖树、-ftime-report 计时、make 风格依赖文件
├── attrib/         # 成本归因：依赖图、独占闭包、重复解析检测、使用启发式
├── verify.py       # 实测验证：注释掉、重编译、计时
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
