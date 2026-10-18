# 治理结构（GOVERNANCE）

> 本文件回答评审第四项（长期发展，20%）的四个问题：**团队持续维护能力、长期规划、
> 治理结构、社区或组织支撑。** 排序图见 [ROADMAP](ROADMAP.md)，贡献方式见
> [CONTRIBUTING](CONTRIBUTING.md)，行为规范见 [CODE_OF_CONDUCT](CODE_OF_CONDUCT.md)。

## 1. 当前治理结构（诚实版）

项目当前由**一名维护者**（项目发起人）以「仁慈独裁者 + 共识优先」模式运营：

| 角色 | 当前担任者 | 职责 |
|---|---|---|
| Maintainer（维护者） | 王子周（项目发起人） | 合并 PR、定优先级、发版本、回 Issue |
| Contributors（贡献者） | 空缺，开放 | 见 CONTRIBUTING「什么类型的贡献最被需要」 |
| Triager | 空缺 | Issue 分诊、复现、补充最小复现样本 |

**贡献者阶梯**（写在这里，让外部贡献者知道路径）：

```
报告 Issue（带扫描报告/最小复现） → 提交 PR（先读 CONTRIBUTING）
  → 持续 3 个以上被合并的 PR → 邀请参与 Issue 分诊
  → 长期参与且方向一致 → 增选为共同维护者（增选需在 Issue 里公开说明）
```

## 2. 决策流程

| 变更类型 | 流程 |
|---|---|
| 文档、测试、bug 修复 | 常规 PR：一名维护者审阅合并 |
| 新命令 / 破坏性变更 / 依赖变更 | 先开 Issue 讨论（含动机与备选方案），实现进 PR，CHANGELOG 记录 |
| 路线图优先级 | 维护者定；**贴一个真实工程的扫描报告**比十个「希望支持 X」更能推动优先级（ROADMAP 末节同） |
| 依赖政策 | **零运行时依赖是架构约束**：默认路径必须能在最朴素的 CI 容器里跑起来；dev 依赖仅 `pytest` / `ruff`，由 `tests/test_no_deps.py` 守护，新增运行时依赖等于破坏约束 |

## 3. 发布纪律

1. 遵循[语义化版本](https://semver.org/lang/zh-CN/)；版本号在 `pyproject.toml`；
2. **发布 = 打 tag**（`vX.Y.Z`）→ `.github/workflows/release.yml` 自动构建产物；
3. 发布前 CHANGELOG 必须有对应章节（实测数字写进条目，不写营销话术）；
4. 测试必须全绿（CI 覆盖三大操作系统 × 四个 Python 版本）；
5. PyPI 上架是 v0.4 目标（ROADMAP），执行时由维护者本人账号操作，不自动化。

## 4. 长期规划（摘要）

完整版在 [ROADMAP](ROADMAP.md)：

- **v0.1–v0.3（已交付）**：可信的测量（成本归因、反事实实测、三态决策、门禁）
  + 平台化（趋势看板、增量扫描、PR 评论、多编译器横评）；
- **v0.4（计划中）**：生态集成（CMake build step、Bazel/Meson 适配、VSCode 插件、
  与 IWYU 输出互转）+ PyPI 上架；
- **维护性承诺**：每个新功能附测试；每个实测结论附复现命令；已知局限单独成章

  （README「诚实的边界」与 `docs/competitor-matrix.md` 的风险登记册是持续维护的
  文档，不是一次性文案）。

## 5. 社区与组织支撑

- **单人开源 ≠ 无组织支撑**：项目以 Apache-2.0 许可运营（[LICENSE](LICENSE)），
  贡献机制（[CONTRIBUTING](CONTRIBUTING.md)）、Issue/PR 模板（`.github/`）、
  安全响应流程（[SECURITY](SECURITY.md)）均已就位，Issue 与 PR 对公众开放；
- **降低参与门槛的具体设计**：`good-first-issue` 标签约定（从文档、复现脚本、
  新编译器驱动这类边界清晰的任务开始）；每个实验都有「复现命令」小节，
  外部贡献者可以只带着好奇心进来；
- **组织化路径**（计划，不承诺）：v0.4 稳定后申请加入开源基金会孵化体系，
  让治理结构从「一个人」演进为「一个社区」。
