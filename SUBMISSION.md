# 提交清单（SUBMISSION）

> 本文件记录「还剩什么、每步怎么做、卡在哪」。
> 代码、测试、基准、PDF、视频都已完成；剩下两项需要**你本人**操作。

## 当前完成度

| 项 | 状态 | 位置 |
|---|---|---|
| 代码 | ✅ 148 文件 / 50+ commit | `src/heavyheader/` |
| 测试 | ✅ 418 个全绿 | `tests/` |
| 治理文件 | ✅ README/LICENSE/CONTRIBUTING/CoC/SECURITY/ROADMAP/CHANGELOG/CI | 仓库根 |
| 基准数据 | ✅ fmt 真实项目 + 构造工程 + 三编译器横评 | `benchmarks/` |
| 多编译器对比 | ✅ GCC / Clang / MSVC 真实三编译器横评 | `toolchains` 子命令 + `benchmarks/results/toolchains-demo.{json,md}` |
| 完整构建实验 | ✅ 真实工程 63 TU，完整构建 −7.1% 墙上 / −11.4% CPU，断言逐个一致 | `experiments/` + `experiments/results/` |
| 删除建议决策层 | ✅ 222 候选全部实测：建议删除 1 / 保留 187 / 需人工判断 34，三维度数据 + 竞品交叉 | `attrib/advice.py` + `experiments/results/decision-*.md` |
| 竞品真实对比 | ✅ clang-include-cleaner 同工程同任务：0 删除 / 101 补充；我们 1 条实测 −43.5% | `experiments/results/competitor-*.md` |
| 竞品尽调 | ✅ 四层竞品地图 + 12 项能力矩阵 + 逐格证据等级 + 7 项产品风险 | `docs/competitor-matrix.md` |
| 同任务基准计划 | ✅ 预注册协议、3 个工程、8 类数据、3 个门槛（阈值待基线后填） | `docs/benchmark-plan.md` |
| G1 同任务对照（ClangBuildAnalyzer） | ✅ 同编译器/同候选规则/同构口径：Top-10 重合 7/10、ρ 0.68；**明确不支持「我们更准」** | `docs/benchmark-report.md` §8 + `experiments/results/cba-compare-20261018.{md,json}` |
| G2 优化闭环 | ❌ **不成立**：真实工程可采纳率 0/10；阳性对照 1/6 证明管线本身有效。据此不再宣传「自动优化」 | `docs/benchmark-report.md` §9 + `experiments/results/g2-loop-*.{md,json}`、`g2-control-*.{md,json}` |
| G3 CI 门禁 | ✅ 成立（限归因行数型回归）：构造样本与**真实工程 25 TU** 双重复验——注入回归 3/3 检出、无回归 3/3 通过、构建损坏 3/3 拦截 | `docs/benchmark-report.md` §10 + `experiments/results/g3-{gate,real}-*.{md,json}` |
| G4 工程开销 | ✅ 成立：1.15×（g++）/ 1.20×（clang++），峰值 9.2 MB，三次完全确定；MSVC 未测 | `docs/benchmark-report.md` §11 + `experiments/results/g4-overhead-*.{md,json}` |
| MSVC 真实运行 | ✅ CI 实测：`scan --compiler cl` → TU=2、失败 0、归因 196,157 行；`gate` 通过。run #5/#6 success，产物可下载 | `.github/workflows/msvc-evidence.yml` + `docs/benchmark-report.md` §11 |
| 作品介绍文档 | ✅ 15 页 PDF | `HeavyHeader-作品介绍文档.pdf` |
| 选题报告（补充） | ✅ 14 页 PDF | `HeavyHeader-选题报告.pdf` |
| 演示视频 | ✅ 约 5 分钟 MP4（10.6 MB） | `HeavyHeader-演示视频.mp4` |
| 公开仓库 | ✅ https://github.com/wzzrambler/heavyheader | 50+ commit / 148 文件，CI 三工作流 |
| 报名表 | ⬜ 待填写（需本人信息） | 见下 |
| 邮件提交 | ⬜ 待发送 | 见下 |

## 第 1 步：推 GitHub 公开仓库 ✅ 已完成

仓库：**https://github.com/wzzrambler/heavyheader**（Public，50+ commit，148 文件）

- 远程为空仓库，直接推送成功，无需 merge
- 分支已命名为 `main`（README 与 CI 工作流均按 `main` 配置）

**推送前检查（均已完成）**：
- [x] `.gitignore` 已排除 `video_build/`（3012 帧 PNG，约 1.5 GB）
- [x] PDF 与 MP4 均已一并推送；视频另传 B 站/网盘，报名表填链接即可
- [x] README / pyproject / SECURITY / CONTRIBUTING 里的仓库地址均为真实地址
      （`docs/finalize.py` 已统一替换）

## 第 2 步：填写报名表（需要你）

官网：https://www.oschina.net/oa2026/

报名表字段（从官网前端代码中提取，已核实）：

| 字段 | 填什么 |
|---|---|
| 项目名称 | `HeavyHeader — 面向 C/C++ 工程的可验证构建优化与性能回归治理工具` |
| 参赛赛道 | 赛道一 · 开源基础软件与解决方案 |
| 申报主体类型 | **开源社区** |
| 单位 / 学校 | 你的姓名或常用 ID（个人申报） |
| 负责人 | 你的姓名 |
| 邮箱 | 常用邮箱（用于接收报名确认） |
| 电话 | 手机号 |
| 其他团队成员 | 留空（≤ 8 人，你是 1 人） |
| 承诺声明 | 勾选同意 |

**提交后会收到确认邮件**，按邮件指引提交作品材料。没收到确认邮件前，
作品材料发了也可能不被受理——所以先报名，等邮件。

## 第 3 步：发送作品材料（需要你）

确认邮件收到后，三件套打包发到 **bjoscc@oschina.cn**：

1. **代码仓库链接** — 第 1 步推好的 GitHub 地址
2. **作品介绍文档** — `HeavyHeader-作品介绍文档.pdf`
   - ✅ 封面「项目负责人」已填「王子周」（`docs/finalize.py` 已应用），无需再改
   - 若日后需重填作者：`python docs/finalize.py --author "王子周" --repo "https://github.com/wzzrambler/heavyheader" --apply`
3. **演示视频链接** — `HeavyHeader-演示视频.mp4` 传 B 站/网盘后的链接
   （大赛要的是链接，不是附件）

**截止：2026 年 10 月 31 日 24:00**

## 时间线

- **10-31 24:00** — 报名 + 作品提交截止
- **11-06** — 入围名单公布
- **11-13** — 线下总决赛，北京亦庄通明湖会展中心，**需到场路演**

## 如果要做路演准备

入围后才需要，但提前想清楚不吃亏：

- 路演核心就一句话：「现有工具给你一个排序，或一份能删的清单；我们给你一条**带反事实
  测量的决策**，并且真的删给你看——每条建议都带明确结论和可复现的实测数据。」
- 必须主动交代来源，否则会被认为没做功课：**归因行数与 Blowup Factor 的公式来自
  Header Hero（Bitsquid，2011）**；「注释掉再编译」来自 deheader（ESR，2010）；
  逐头文件耗时 ClangBuildAnalyzer 早已给出。我们的差异是依赖树取自真实编译器 `-H`、
  并对候选做 A/B 实测后给出三态决策与门禁。逐条出处见 `docs/competitors.md`。
- 最有说服力的 30 秒：真实工程（nlohmann/json，63 个 TU）完整构建 −7.1% 墙上时间、
  −11.4% 编译 CPU，4,264,414 条断言逐个一致——这是可复现的实测，不是估算。
- 更要准备的一条：对照实验里**工具曾经输过**（候选排序命中 0/5，经验法则 3/12）。
  主动说，并说清三件事：根因（按收益排序会把「所有单元都要的头」排在前面）、
  修法（可采纳性分层 + 判定覆盖独占闭包，`rank_check.py` 零次试验检验）、
  以及当前仍然承认的精度差距（222 个候选里 187 个编译不过）。
  这比只报好消息可信得多。
- 评委可能问：「这和 IWYU / clang-include-cleaner 什么关系？」答：互补，且我们用
  真实竞品在同工程同任务上对照过——它 0 条删除建议、101 条补充建议；我们
  1 条实测有效（−43.5%，三个维度全部实测）。它管正确性（AST 级），
  我们管成本（编译输出级）。**可选的 AST 交叉检查已实现**（`--semantic`，调用
  `clang-include-cleaner` 做交叉印证，工具沉默时不据此降级）；ctypes 直连 libclang
  的路线经实验证伪，负面结果与复现步骤记录在 `experiments/libclang_spike.py`。
