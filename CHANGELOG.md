# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- **闸门交白卷时的诊断（`attrib/diagnosis.py`）**：`--header-sites` 下，每一条
  收益非零、却被可行性闸门拦下的 include 行都会记录原因；真依赖会附具体标识符
  证据，系统头内部的行单独计数。一条候选都不放行时，报告改用
  「没有可删的了：成本在哪、为什么删不掉」作答——成本集中度、被拦下的收益榜、
  原因分布、下一步建议，并以「任何删除都要先过 `verify`」收尾。
  实测：nlohmann/json 369 条、zlib 58 条被逐条判过，放行 0 条；doctest 放行 16 条。
- **新开关 `--include-system-sites`**：把系统/第三方头文件内部的 include 行也
  列入（仅供查看，不建议改——改别人的头不是本项目能承担的决策）。

### 基准与门槛（G1–G4）

- **G2 重做后成立（有限）**：换头文件级候选 + 交替测量后，doctest（24 TU）产出
  1 条经复测的优化（删 `prelude.h` 里的 `<unordered_set>`，24 单元全通过，
  聚合 −3.01%）；nlohmann/json 与 zlib 都是 0/5。据此**仍不宣传「自动找到
  优化」**，对外只表述为「可验证 + 可拦截」。
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
