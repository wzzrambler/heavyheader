# 提交清单（SUBMISSION）

> 本文件记录「还剩什么、每步怎么做、卡在哪」。
> 代码、测试、基准、PDF、视频都已完成；剩下两项需要**你本人**操作。

## 当前完成度

| 项 | 状态 | 位置 |
|---|---|---|
| 代码 | ✅ 83 文件 / 32 commit | `src/heavyheader/` |
| 测试 | ✅ 292 个全绿 | `tests/` |
| 治理文件 | ✅ README/LICENSE/CONTRIBUTING/CoC/SECURITY/ROADMAP/CHANGELOG/CI | 仓库根 |
| 基准数据 | ✅ fmt 真实项目 + 构造工程 | `benchmarks/` |
| 作品介绍文档 | ✅ 14 页 PDF | `HeavyHeader-作品介绍文档.pdf` |
| 选题报告（补充） | ✅ 12 页 PDF | `HeavyHeader-选题报告.pdf` |
| 演示视频 | ✅ 约 5 分钟 MP4（10.6 MB） | `HeavyHeader-演示视频.mp4` |
| 公开仓库 | ✅ https://github.com/wzzrambler/heavyheader | 32 commit / 83 文件，CI 三工作流 |
| 报名表 | ⬜ 待填写（需本人信息） | 见下 |
| 邮件提交 | ⬜ 待发送 | 见下 |

## 第 1 步：推 GitHub 公开仓库 ✅ 已完成

仓库：**https://github.com/wzzrambler/heavyheader**（Public，32 commit，83 文件）

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
| 项目名称 | `HeavyHeader — C/C++ 构建效能治理平台` |
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

- 路演核心就一句话：「现有工具告诉你该不该删，我们告诉你它能让你多等几秒，
  并且真的删给你看。」
- 最有说服力的 30 秒：fmt 上 6 个候选取 3 个实测，全部正确判定「删了会编译失败」
  ——证明不乱喊；构造工程上准确识别并省 38.5%——证明真有用。
- 评委可能问：「这和 IWYU 什么关系？」答：互补。IWYU 管正确性（AST 级），
  我们管成本（编译输出级），Roadmap v0.3 会做 libclang 后端做精确模式。
