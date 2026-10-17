# 竞品与相关工作：HeavyHeader 站在谁的肩膀上

> 事实核实日期：2026-10-18。每条能力都附可复核的出处；凡是我们**没有**的能力，直说没有。
> 这份文档存在的唯一目的：让评审在看到「HeavyHeader」这个名字时，不会认为作者不知道
> Header Hero 的存在。事实上，本工具的核心成本公式就来自它。

---

## 0. 一句话结论

**单项能力没有一项是我们首创的。** 归因行数来自 Header Hero（2011），逐头文件耗时来自
ClangBuildAnalyzer，建议清单来自 IncludeGuardian，注释掉再编译来自 deheader（2010）。

我们唯一站得住的东西是**把四件事串成一个闭环，并且只把「实测过」的结论写成结论**：

```
结构归因（谁贵） → 反事实实测（删了真省多少） → 三态决策（该不该动/证据够不够） → 门禁（CI 拦住回退）
```

前两格里每一格都有成熟工具；**第三格没有人做**；第四格有人做了一半（deheader 有退出码，
但不含成本权重）。这就是我们的定位，不多说一个字。

---

## 1. 对标总表

| 工具 | 核心指标 | 依赖来源 | 是否实测 | 是否给决策 | 是否可门禁 | 平台/依赖 |
|---|---|---|---|---|---|---|
| **Header Hero**（Bitsquid 2011 / aras-p fork） | 归因行数 = 行数 × 包含次数；Blowup Factor | 正则扫 `#include`，**非**真实预处理器 | 否 | 否（只排序） | 否 | Windows/macOS/Linux（.NET 9 GUI） |
| **ClangBuildAnalyzer**（aras-p） | 逐头文件累计 ms、包含次数、平均 ms | 真实 `-ftime-trace` | 是（编译器内省） | 否 | 否 | 仅 Clang 9+，需带 flag 重编 |
| **IncludeGuardian**（v0.0.8） | 预处理 token 节省百分比 | 编译数据库 + 结构分析 | **否**（不编译） | 否（给清单） | 否 | Windows/Linux x64 二进制 |
| **deheader**（ESR 1.13） | 二值：能否删除 | 真实编译 `rm foo.o; make foo.o` | 是（但不量化） | 否 | **半有**（退出码可做 Makefile 预检） | 任意有 make/ninja 的环境 |
| **HeavyHeader** | 归因行数 + Blowup Factor + **实测 Δ时间/Δ内存/Δ体积** | 真实编译器 `-H` 依赖树 + 反事实重编 | **是（反事实对比）** | **是（三态 + 证据等级）** | **是（按实测净收益）** | GCC/Clang/MSVC，零第三方依赖 |

---

## 2. 逐项核实

### 2.1 Header Hero —— 必须主动交代来源

- **出处**：Niklas Frykholm（Bitsquid），《Caring by Sharing: Header Hero》，2011-10。
  <http://bitsquid.blogspot.com/2011/10/caring-by-sharing-header-hero.html>
- **维护分支**：aras-p 的 fork/rewrite，58 stars，C#/.NET 9 + Avalonia，2025 年重写，
  支持 Windows/macOS/Linux。<https://github.com/aras-p/header_hero>

原文给出的定义（逐字要点）：

1. 它自称 **"header file profiler"**；
2. 排序依据是「该文件贡献给 Total Parsed 的行数」，即 **文件大小 × 被包含次数**；
3. `Total Parsed` = 全量重编时实际被解析的行数（同一头文件被多个 .cpp 包含，每次都要累加）；
4. **`Blowup Factor = Total Parsed / Total Lines`**，含义是「平均每行被解析多少次」；
5. 解析方式是 **"a simple parser that just looks for #include patterns rather than running
   the real C preprocessor"**，作者自己写明副作用：用了复杂预处理技巧的会被漏掉。

**与我们重叠的部分（不回避）**：

- 「归因行数」这个指标与它**公式完全相同**，我们没有资格声称原创。
- 我们的 `total_included_lines()` 就是它的 `Total Parsed`。
- 我们**原先没有** Blowup Factor；已于 2026-10-18 补齐（`ProjectScan.blowup_factor()`），
  定义与它一致，并在报表与 JSON 中同时导出分子分母，读者可自行验算。

**真实差异（只有这一条，不要加戏）**：

- 包含次数与依赖树来自**真实编译器的 `-H` 输出**，不是正则扫描。宏、条件编译、
  `#include_next`、生成的头文件，在正则方案里会算错；我们的数字是编译器实际打开的
  文件与次数。
- 因此我们能在同一份依赖树上继续做**反事实实测**（删掉这一行再编译一次），
  而 Header Hero 到「谁贵」为止，不回答「删了省多少」。
- 它是 GUI 工具、需要手动指定源码/包含目录；我们是 CLI，可直接接 `compile_commands.json`
  并进 CI。

未对齐的部分（如实记录）：它的 GUI 有可交互的包含关系图（双击聚焦、上下游文件数），
我们没有对应界面，只有文本/JSON 输出。

### 2.2 ClangBuildAnalyzer —— 我们撤回一句错话

- **出处**：aras-p，2019-09-28 发布说明 <https://aras-p.info/blog/2019/09/28/Clang-Build-Analyzer/>；
  仓库 <https://github.com/aras-p/ClangBuildAnalyzer>。
- **真实输出格式**（原样引用，来自上述两处）：

  ```text
  *** Expensive headers:
  136567 ms: /.../Foundation.h (included 92 times, avg 1484 ms), included via:
    ...
  112344 ms: Runtime/BaseClasses/BaseObject.h (included 729 times, avg 154 ms), included via:
  ```

  也就是说：**逐头文件的累计毫秒、被包含次数、平均单次毫秒**，三样都有，且还给出
  前几条包含链。数据来自真实 `-ftime-trace`（编译器前端自己的计时），比我们的
  墙钟差分更细。

- **限制**：仅 Clang 9+；必须在编译时加 `-ftime-trace` 重编一遍；只描述「现在花了多少」，
  不提供「删掉会怎样」的反事实；无基线、无门禁。

**因此我们撤回并已改写以下表述**（旧句在新版中已删除）：

> 「现有工具能告诉你这个 include 该不该删，但**没有一个工具回答它到底让我多等了几秒**。」

这句话是**错的**，且容易被现场证伪（评审打开 ClangBuildAnalyzer 的输出即可）。
改写后的表述是：

> 现有工具能回答「现在这个头文件花了多少时间」（ClangBuildAnalyzer，基于 `-ftime-trace`）
> 或「这个 include 能不能删」（deheader，注释掉再编译），但**不回答「删掉这一条，
> 这次构建具体省多少时间、多少内存、多少体积」**——因为它们要么没有反事实，
> 要么只有二值判断。

### 2.3 IncludeGuardian —— 重叠度最高，必须承认它更全

- **出处**：<https://includeguardian.io>、<https://includeguardian.io/instructions>，v0.0.8，
  免费，仅 Windows x64 / Linux x64 二进制。
- **输入**：编译数据库 `-p compile_commands.json` + 源文件列表，或 `--dir` 递归扫目录。
- **给出的建议分类**（原文七类，逐条对应）：

  | IncludeGuardian 的建议 | 我们是否有 |
  |---|---|
  | `unguarded files`（缺 include guard / 多重包含未优化） | 有（「必赚项」章节） |
  | `unreferenced components`（无人包含的 header+source） | 有（候选检测的一部分） |
  | `include directives`（最贵的单条 include，建议改前向声明/换更小头） | 有（成本热点 + 前向声明候选） |
  | `make private`（`#include` 从 header 挪到 source） | 部分（候选里能体现，但没有专门分类） |
  | `pch additions`（哪些外部头该进 PCH） | 有（`--pch`） |
  | `large files`（大头文件建议拆分） | 部分（独占闭包里会提示大文件，但未作为独立建议） |
  | `inline sources`（source 太小建议并入 header） | **没有** |

- **指标性质**：它统计的是**预处理 token 的节省百分比**，并明确自述
  "not a perfect substitute for the actual time saved"，只是「确定性且快」的替代品。
- **它没有的**：**不做任何编译/测量**（因此给的是估算而非实测）、不做门禁、
  只有 Win/Linux 二进制（无源码跨平台）、无三态决策。

诚实结论：**就「建议清单的覆盖度」而言它比我们全**，我们至少缺 `inline sources`
这一整类，`make private` 与 `large files` 也没有独立成章。这是明确的产品差距，
不应在报告里含糊过去。

### 2.4 deheader —— verify 的核心动作与它同源

- **出处**：Eric S. Raymond，v1.13（Homebrew 2026-08-08 校验），已进 Debian/Homebrew/Nix/openSUSE。
  <http://www.catb.org/~esr/deheader/>、手册页 <http://www.catb.org/~esr/deheader/deheader.html>
- **核心动作**（手册原文）：对每个 `foo.c`，测试 `rm foo.o; make foo.o` 是否返回 0；
  `-r` 可自动删除；返回 1 表示发现可删 include，**"you can use it for pre-release
  sanity checking in Makefile"**——也就是说，**用退出码做门禁这件事也不是我们首创**。
- **限制**：只给二值结论，不量化成本（`-v 1` 会为进度显示对测试编译计时，但不作为指标输出）；
  整段跳过 `#if/#ifdef` 区域（自述「推理 -D/-U 组合太复杂」）；手册 BUGS 自述
  偶发误删实际需要的头文件，建议在版本控制下使用。

诚实结论：**「注释掉再编译」这个方法论是 ESR 在 2010 年就做出来的**，我们不是发明者。
我们做的是在同一个动作上**加测量与决策**：跑两次（基线/补丁）、取多次运行的
中位数与离散度、同时记录时间/内存/体积、失败则降级为 HUMAN 而不是直接判死。

---

## 3. 那我们到底剩下了什么

只列能经得起追问的，不列形容词：

1. **反事实的量化**：不是「这个头文件现在花 1484 ms」（ClangBuildAnalyzer 能给），
   也不是「这条 include 能删」（deheader 能给），而是「**删掉这一条后，这个 TU
   的构建时间/内存/产物体积各变化多少，变化是否超过噪声**」。这个数字只有做
   A/B 两次真实编译才存在。
2. **三态决策 + 证据等级**：`REMOVE / KEEP / HUMAN(lean)`。deheader 是二值，
   IncludeGuardian 是建议清单，Header Hero 是排序，ClangBuildAnalyzer 是现状快照。
   **没有任何一个输出「不建议动，因为收益低于噪声」这种结论**——而这恰是工程上
   最需要的，否则清单越长越没人看。
3. **门禁建立在实测净收益上**：deheader 的门禁是「有没有发现可删项」，会把
   「可删但省 0.3%」也判成失败；我们的门禁按**实测净收益阈值**判定，噪声内的
   改动不阻断 CI。
4. **成本与安全性分离**：只有「编译通过 + 测试通过 + 收益超噪声」才给 REMOVE；
   可选的 AST 交叉检查（`--semantic`，调 `clang-include-cleaner`）只用于**增强**
   结论，工具沉默时**绝不**据此降级——因为「工具没提」不等于「没在用」。
5. **跨编译器、零第三方依赖**：GCC/Clang/MSVC 都走同一套实测流程；
   默认路径不要求 LLVM 工具链。

一句话版本：

> 别人给你一个排序，或一份能删的清单；我们给一条**带反事实测量、带证据等级、
> 能被 CI 执行**的决策。

---

## 4. 我们明确不声称的事

写在这里，是为了避免下一版报告再次说过头：

- ❌ 不声称「归因行数」或「Blowup Factor」是原创——**Header Hero 2011 年就有了**。
- ❌ 不声称「没有一个工具能回答多等了几秒」——**ClangBuildAnalyzer 能，且更细**。
- ❌ 不声称建议清单比 IncludeGuardian 全——**它更全，我们缺 `inline sources` 等类别**。
- ❌ 不声称「注释掉再编译」是原创——**deheader 2010 年就在做，且有退出码门禁**。
- ❌ 不声称比 `-ftime-trace` 的计时更精确——**编译器内省优于墙钟差分**。
- ✅ 只声称：把上述能力**组合成闭环**，并给出**反事实的量化**与**可门禁的三态决策**。

---

## 5. 复核清单

| 事实 | 出处 | 核实日期 |
|---|---|---|
| Header Hero 归因公式、Blowup Factor、正则解析 | bitsquid.blogspot.com/2011/10/caring-by-sharing-header-hero.html | 2026-10-18 |
| Header Hero fork 状态（58★、.NET 9、2025 重写） | github.com/aras-p/header_hero | 2026-10-18 |
| ClangBuildAnalyzer 输出格式与限制 | aras-p.info/blog/2019/09/28/Clang-Build-Analyzer/ ；github.com/aras-p/ClangBuildAnalyzer | 2026-10-18 |
| IncludeGuardian 七类建议、token 指标、v0.0.8、Win/Linux | includeguardian.io ；includeguardian.io/instructions | 2026-10-18 |
| deheader 核心动作、`-r`、退出码、1.13、发行渠道 | catb.org/~esr/deheader/deheader.html ；pkg.so/brew/deheader | 2026-10-18 |

若上述任一页面更新导致结论变化，以页面为准，并同步修订本文与 README 的措辞。
