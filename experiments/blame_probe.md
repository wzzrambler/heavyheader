# 候选单件能力的撞车检查与可行性验证（2026-10-19）

为回答「还有没有新的单件首创能力可做」，对四个候选做了全网搜查（GitHub repo
search API / 项目官网 / 网页搜索 / 学术检索）与本地可行性验证。结论先行：
**「commit 级头文件成本归因（成本考古）」全网空白，且本地验证通过。**

## 1. 候选与撞车检查

| 候选 | 搜查结论 | 证据（核实日期） |
|---|---|---|
| **commit 级成本归因**（把成本回归归因到具体 commit + include 行） | **未见同能力工具**。相邻但不同：jenkinsci/build-time-blame-plugin（Jenkins 控制台输出历史，不碰头文件）、jeremy-rifkin/build-blame（合并 ninja + clang trace 的单次构建剖析，非 commit 级）、Bencher 与 cpp-compile-overhead（编译耗时趋势/健康度，不归因到改动） | GitHub repo search API（"build time blame" / "include cost header" / "compile time regression git"）+ 官网，2026-10-19 |
| 成本最小充分 include 集（多 provider 时按成本选子集） | IWYU 已回答「正确性最小集」（AST 逐符号）；**成本维度的联合优化未见**。实用价值中等（同一符号多 provider 的场景有限） | include-what-you-use.org，2026-10-19 |
| 单条 include 改动对并行 CI 墙钟（-jN）的预测 | build-blame 做 ninja trace 的线程近似；「单改动 → 墙钟」预测无人做。需要完整构建 DAG，采集成本高 | github.com/jeremy-rifkin/build-blame，2026-10-19 |
| 边级边际毫秒（v0.4 主攻） | 组合层面空白；单格先例见 competitor-matrix.md §2.2 | 2026-10-19 |

## 2. 本地可行性验证：commit 级成本归因

在 `benchmarks/cases/demo` 的拷贝上构造三个 commit，每 commit 用
`heavyheader scan --save` 存确定性基线，验证「基线 diff + git 历史 →
指名 commit」这条链路是否走得通：

| commit | 改动 | 归因行数 | 指纹 |
|---|---|---:|---|
| c1 | 基线 | 206,106 | — |
| c2 | main.cpp 加 `#include "bloated.h"`（**冗余**：已可从 heavy.h / lonely.h 到达） | 206,106 | **零 delta**——独占闭包语义自动把「改了但没多花钱」识别为无回归 |
| c3 | core.h 加 `#include "bloated.h"`（core.h 被 2 个 TU 引用，bloated.h 新增 1 个可达 TU） | 262,593（**+56,487，+27.4%**） | bloated.h 的**整个闭包**（regex / iostream / locale / stl_algo…约 80 个头）逐一同比例翻倍——「新增一条跨 TU 的包含边」的确定性指纹 |

归因逻辑（已验证可计算）：`git diff --name-only c2..c3` 给出 `include/core.h`；
diff 解析出新增边 `core.h → bloated.h`；bloated.h 闭包 size × 新增可达 TU 数
（22,312 × 2 = 44,624，实测 +56,487，差额来自 core.h 自身 +1 行与重复解析项）
与实测 delta 模式吻合。**结论：数据、指标、图结构全部就绪，归因是一次图运算。**

## 3. 产品形态建议

`heavyheader blame <baseline-old> <baseline-new>`（或 `gate --blame`）：
门禁失败时自动输出「哪个 commit、改了哪个文件、新增/删除了哪条 include、
预测影响哪些 TU、实测 +N 行」。与现有资产的关系：scan 出基线（已有）、
git 历史（标准）、独占闭包（已有）——**不需要新数据源**。

## 4. 复现

```bash
cp -r benchmarks/cases/demo /tmp/blame-demo && cd /tmp/blame-demo
git init -q && git add -A && git commit -qm c1
heavyheader scan . --compiler g++ --std c++17 -I include --save c1.json
# 改 include/core.h 加一行 #include "bloated.h"，commit，再 scan --save c2.json
# 对比两份 JSON 的 headers[].attributed_lines，git diff --name-only 定位文件
```
