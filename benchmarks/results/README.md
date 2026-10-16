# 基准结果

本目录保存**可复现**的扫描结果，作为项目「场景落地」主张的证据。
数字来自真实运行，不是示例数据。

## fmt-report.html / fmt-baseline.json

对 [fmtlib/fmt](https://github.com/fmtlib/fmt)（`--depth 1` 克隆）的扫描结果。

```bash
git clone --depth 1 https://github.com/fmtlib/fmt.git /tmp/fmt
heavyheader scan /tmp/fmt --compiler g++ --std c++17 \
  -I /tmp/fmt/include --exclude test \
  --format html -o benchmarks/results/fmt-report.html \
  --save benchmarks/results/fmt-baseline.json
```

`--exclude test` 是必须的：fmt 的测试套件依赖 gtest/gmock，未安装时那些编译单元
会失败。`src/fmt.cc` 仍会失败——该文件是 C++20 module（第 8 行 `module;`），
MinGW GCC 16.2 不支持 `-fmodules`。**这是工具链限制，不是工具缺陷**，且失败被
如实报告而非静默跳过。

## 结果解读

| 指标 | 值 |
|---|---|
| 编译单元 | 3 成功 / 1 失败（C++20 module，见上） |
| 依赖边 | 651 |
| 头文件解析量 | 331,016 行 |
| 成本候选 | 6 个，其中 **0 个**疑似未使用 |

**最有价值的结果是那个 0。** fmt 是维护良好的库，它的 include 确实是干净的。
工具给出了 6 个成本候选，`verify` 对前 3 个逐一实测，全部报 `compile-error`——
也就是说，删掉它们真的会编译失败：

```
## windows.h ← os.cc
   状态：compile-error — os.cc:90:9: error: 'FORMAT_MESSAGE_ALLOCATE_BUFFER' was not declared
## format-inl.h ← format.cc
   状态：compile-error — format.cc:10:1: error: 'FMT_BEGIN_NAMESPACE' does not name a type
## os.h ← os.cc
   状态：compile-error — os.cc:102:12: error: 'fmt' does not name a type
```

一个只会喊「你可以删掉这个」的工具，第一次误报就会被人关掉。HeavyHeader 在成熟
项目上报告「没有可删的」，同时仍然量化出成本集中在哪——这个对比本身就是价值。

与之对照，`benchmarks/cases/demo` 里刻意构造的未使用 include 被准确识别，
实测节省 200 ms（38.5%）。**该报的报，不该报的不报**，两边都有数据。

## 复现注意事项

- 具体数字随编译器版本、平台、机器负载变化，**不要逐位比对**；
- 关注数量级与结论方向（候选是否为 0、verify 是否报 compile-error）；
- 上表数字取自 GCC 16.2.0 (MinGW-w64) + Python 3.13。
