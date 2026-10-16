# 基准

这个目录放可复现的基准。**每个用例都必须能独立跑出数字**，而不是一段需要人肉
拼接的命令——否则它不会被跑第二次，也就没有存在价值。

## cases/demo

一个 45 行、刻意构造的 C++ 工程，覆盖工具的四类核心发现：

| 文件 | 构造的问题 | 工具应当发现 |
|---|---|---|
| `include/lonely.h` | 被 `main.cpp` 包含但从未使用 | 成本候选，使用状态「疑似未使用」 |
| `include/bloated.h` | 只被 `lonely.h` 到达的重量级闭包 | `lonely.h` 的独占闭包含它 |
| `include/unguarded.h` | 刻意不加 `#pragma once` | 「缺少 include guard」必赚项 |
| `include/core.h` | 被两个编译单元引用 | 成本热点 Top 1 |

### 跑法

```bash
# 1. 扫描，产出 HTML 看板
heavyheader scan benchmarks/cases/demo \
  --compiler g++ --std c++17 \
  -I benchmarks/cases/demo/include \
  --format html -o report.html

# 2. 实测验证（真的删掉、重编译、计时）
heavyheader verify benchmarks/cases/demo \
  --compiler g++ --std c++17 \
  -I benchmarks/cases/demo/include \
  --top 3 --runs 3

# 3. 基线 + 门禁
heavyheader scan benchmarks/cases/demo --compiler g++ --std c++17 \
  -I benchmarks/cases/demo/include --save baseline.json
heavyheader gate benchmarks/cases/demo --compiler g++ --std c++17 \
  -I benchmarks/cases/demo/include --baseline baseline.json
```

### 预期结果

在 GCC 上，`lonely.h` 应当被标记为「疑似未使用」并排在候选首位，
`verify` 应报告移除它带来可观的实测节省；`unguarded.h` 应出现在
「缺少 include guard」表中，解析次数为 3、涉及 TU 数为 2。

**这些是预期，不是断言。** 编译器版本、平台、机器负载都会影响具体数字，
所以基准的价值在于「跑一遍看数量级是否合理」，而非逐位比对。

## cases/ 之外的基准

真实开源项目的扫描结果不放进仓库（体积和许可证都是问题），跑法：

```bash
git clone --depth 1 https://github.com/fmtlib/fmt.git /tmp/fmt
heavyheader scan /tmp/fmt --compiler g++ --std c++17 \
  -I /tmp/fmt/include --exclude test
```

`--exclude test` 是必须的：fmt 的测试套件依赖 gtest/gmock，未安装时那些
编译单元会失败。**失败会被如实报告而不是静默跳过**——报告顶部会写明
「N 个编译单元失败，数字可能偏低」。看到这行时，请先把失败数降到 0 再解读数字。
