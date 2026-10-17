# 贡献指南

感谢你愿意把时间花在 HeavyHeader 上。这份文档的目的不是列规矩，而是帮你**把力气花在刀刃上**——
一个早期项目最缺的不是代码量，是对的方向。

## 什么类型的贡献最被需要

按优先级排列：

1. **真实项目的扫描结果**。如果你有一个构建慢的 C/C++ 项目，跑一次 `heavyheader scan`，
   把报告里的**数字**（不是源码）反馈到 Issue，比任何功能请求都有价值。我们最需要知道的是
   工具在真实代码库上会不会给出荒谬的结论。
2. **编译器输出的样本**。不同版本 GCC/Clang/MSVC 的 `-H`、`-ftime-report`、`/showIncludes`
   格式有细微差异。如果你的编译器版本输出格式和我们的测试样本不一致，那是 bug，请带上
   几行真实输出提 Issue。
3. **误报报告**。「工具说 X 可以删，删了就编译不过」是最高优先级的 bug。请附上最小复现。
4. **文档与翻译**。方法论文档写得不清楚、术语不一致，都请直接指出。
5. **新功能**。见下方「动手之前」。

## 动手之前

**先开 Issue，再写代码。** 早期项目方向变得快，一个没人讨论过的 PR 有很高概率白写。

如果你已经想清楚要做什么，请在 Issue 里说清三件事：

- **问题**：你遇到了什么，能用命令行输出或报错说明吗？
- **场景**：什么项目、什么编译器、多大代码量？
- **你建议怎么做**：如果你有想法。

如果只是「我觉得这里可以更好」，也欢迎——但请附上你为什么这么觉得。

## 开发环境

```bash
git clone https://github.com/wzzrambler/heavyheader.git
cd heavyheader
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

要求 Python 3.10+，**无第三方运行时依赖**——这是刻意选择，让工具能在最朴素的 CI 容器里跑起来。

## 提交规范

```
<type>(<scope>): <subject>

<body>
```

`type` 取值：`feat` / `fix` / `docs` / `test` / `refactor` / `perf` / `chore`。

示例：

```
fix(attrib): exclude the candidate's own closure from the shared set

The "already paid for" union was built from all direct includes,
including the one under consideration. That put the candidate's own
closure into the shared set, made every exclusive set empty, and
silently produced zero candidates on every project.

Adds a regression test: the symptom (no candidates at all) is
indistinguishable from "nothing to optimise", so it needs a test
rather than a careful reading.
```

提交信息写**为什么**，不写**做了什么**——`git log` 能告诉你做了什么。

## 代码风格

- 遵循 PEP 8，行宽 100。CI 会跑 `ruff`。
- 类型标注是必须的。项目用 `from __future__ import annotations`，所以可以用
  `list[str]` 而不必 `List[str]`。
- docstring 写**为什么这样设计**，不写**这行代码在干什么**。后者读代码就知道，前者读不出来。
  上面那个 commit message 就是范例。
- 注释里不要写「注意：这里是绕过某个问题」却不说什么问题。要么说清，要么别提。

## 测试

每个 bug 修复必须带一个**会失败的测试**。顺序是：先写测试、看它红、再修、看它绿。

如果 bug 的症状是「什么都没发生」（比如候选列表为空），测试尤其重要——那种 bug 从输出上
看和「没问题」完全一样。

```bash
pytest                          # 全部测试
pytest tests/test_attribution.py -q   # 单个文件
pytest -k exclusive             # 按名字过滤
```

## 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。

## 许可证

提交 PR 即表示你同意你的贡献以 [Apache-2.0](LICENSE) 许可证发布。
