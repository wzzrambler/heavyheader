# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

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
