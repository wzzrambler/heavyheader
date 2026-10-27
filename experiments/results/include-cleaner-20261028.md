# clang-include-cleaner 对位实测（2026-10-28）

复现：`experiments/incclean_probe.py`。代码：`src/heavyheader/attrib/incclean.py`、
`src/heavyheader/attrib/crosscheck.py`；CLI：`heavyheader include-cleaner`。

## 为什么要做第二个后端

`iwyu` 那条路有一个没法回避的缺口：本项目构建不出 IWYU 二进制（需按 LLVM
版本源码编译），所以它的解析**没有经过端到端实测**，验证于按官方格式构造的
样本。报告正文里声明了这一点，也正因为声明了，它给出的对位只能当线索。

clang-include-cleaner 不同：本机 `C:\Program Files\LLVM\bin\clang-include-cleaner.exe`
（LLVM 23.1.3）是现成的。同样一类能力，但能真跑——于是解析格式可以从**真实
进程输出**校准，而不是从文档或别人的脚本推。本文记录的就是这次校准得到的
东西，包括其中一条**写错过又被实测推翻**的结论。

## 三条实测行为

以下三条都来自真实调用，不是读文档读来的。它们直接决定了实现：

1. **一次只能分析一个文件。** 传两个源文件时输出
   `-print requires a single input file`。所以「批量分析」只能是**逐单元起
   进程**——N 个编译单元就是 N 次完整 AST 分析，这是该工具的成本模型，
   不是我们的实现偷懒。`--compile-commands` 走的就是这条路。

2. **编译失败时跳过该文件，退出码非 0。** 被跳过的文件不产出任何建议。
   这**绝不等于**「它认为这个文件没问题」——工具对这个文件里的 include
   **什么都没说**。把「没分析」读成「没意见」，会让一份对位报告凭空多出
   一批假的放心。所以实现里把它单列为「未参与分析」，并且**不参加对位**。

3. **输出里不含文件名。** `--print=changes` 只给
   `- "lonely.h" @Line:5` 这样的行，归属哪个源文件完全由调用方保证。
   CLI 因此要求 `--log` 必须配 `--source`，不配就报错退出，而不是猜。

**格式**（LLVM 23.1.3，`--print=changes`）：`- ` 开头为删除、`+ ` 开头为插入；
本地头写作 `"foo.h"`、系统头写作 `<vector>`；删除行带 `@Line:N`，插入行不带
（插入位置由工具自己定，我们不该编一个行号出来）。

## 一处写错、被实测推翻的结论

初稿在模块文档里写「编译失败时退出码**仍然是 0**」。这是**错的**。

错因：`clang-include-cleaner ... | head -20` 之后看到 `Exit Code: 0`——那是
**管道最后一个命令**（`head`）的退出码，不是工具的。实测（不经管道）退出码
为非 0。

已更正。顺带说明更正之后的真正理由：退出码非 0 只说明「失败了」，**说不出
为什么**——1 至少覆盖「源文件有编译错误」「编译数据库加载失败」「一次传了
多个源文件」三种情形，处理方式完全不同（第一种要先修好编译，第三种是本
模块的调用约束）。所以要给出可执行的下一步，仍然得解析文本。这也是实现里
存 `skip_reason` 与 `diagnostics` 的原因。

（这一条记在这里，是因为它与本项目此前一次教训同型：把工具输出重定向到
`/dev/null` 只解析 JSON，然后断言「工具静默失败」。**下判断之前先看原文的
完整输出**，包括退出码的来源。）

### 复现脚本里差点发生的第二次同类错误

`experiments/incclean_probe.py` 初版用 `compiler="clang++"` 扫描 demo，得到
**0 个候选**——和 CLI 的 5 个对不上。

原因：本机的 clang 装在 `C:\Program Files\LLVM\bin` 且**不在 PATH** 上，
`scan_project` 找不到编译器时**只给一条 warning**（`compiler 'clang++' not
found on PATH`），`candidates` 归零，其余数字照常。也就是说：换成 clang 扫
描，这份归档就会变成「0 候选、0 一致」，而且看起来完全正常。

脚本因此改用 g++，并把 `scan.warnings` 打印出来。顺带说明：这条 warning
是本工具**已经报出来**的，不是静默失败——前提是你别把 stderr 丢掉。

## demo 语料上的完整闭环

命令（`--probe` 让我们这一侧也有实测结论，而不是静态口径）：

```
python -m heavyheader scan benchmarks/cases/demo -I benchmarks/cases/demo/include \
    --std c++17 --probe --format json -o scan.json
python -m heavyheader include-cleaner --scan scan.json \
    --compile-commands compile_commands.json
```

结果：

| 口径 | 条数 |
|---|---:|
| 参与分析的文件 | 2 |
| 未参与分析的文件 | 0 |
| clang-include-cleaner 的删除建议 | 1 |
| 其中本工具也判「删」 | 1 |
| 本工具判 KEEP、它说删（分歧） | 0 |
| 本工具独有、它未提及的候选 | 4 |

唯一的建议是 `main.cpp` 第 5 行的 `lonely.h`，**按行号**对位命中我们的候选，
预计省 **22,316** 行；我们 `--probe` 的实测结论同为 REMOVE，两边一致。

值得单独说一句的是另外 4 条：本工具报了成本候选，clang-include-cleaner
**没有**建议删它们。这不是矛盾——它是正确性口径（这些 include 提供的符号
确实被用到了），我们是成本口径（它们贵）。两条信息合起来才有判断：
「能删」是少数，「能删且便宜」是更少的少数，而「贵但删不掉」是重构该
从哪里下手的线索。

## 两个后端共用一套对位口径

实现时把「外部工具说要删 ↔ 我们这一侧的候选」抽到了
`attrib/crosscheck.py`，`iwyu` 与 `include-cleaner` 共用。

这不是单纯去重：如果两个后端各写一份对位，同一条候选可能在 IWYU 报告里算
「两边一致」、在 include-cleaner 报告里算「分歧」。读者无法判断这是两个
工具真实的差别，还是我们某一侧写歪了。**数字一旦不可比，做两个后端这件事
本身就失去意义。**

口径本身：优先按行号，行号对不上才退回文件名（basename），并把用哪种
记在 `matched_by` 里——按文件名匹配是模糊的，同名不同目录会串。
「未实测」（这一轮没跑 `--probe`）与「我们无此候选」必须分开渲染，
混同等于把「没测过」说成「没有」。

## 边界

- 端到端只在 **demo 语料（2 TU）** 上跑过。语料太小，上面那张表的数字
  **不能当成效果证据**，它证明的是机制闭环：真跑工具 → 解析 → 对位 →
  得出结论。
- 未在 json / spdlog 等更大规模语料上跑：该工具逐单元起进程做完整 AST
  分析，规模上去之后耗时不可忽略，需要单独设计（限流、抽样）。
- 未验证其他 LLVM 版本。`--print=changes` 的输出格式没有稳定性承诺，
  换版本后应先重跑本文的复现脚本确认。
