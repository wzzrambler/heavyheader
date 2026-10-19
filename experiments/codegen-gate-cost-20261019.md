# 代码等价闸门的成本与误报实测（2026-10-19）

要回答的问题：`--check-codegen` 值不值得默认开。结论先行：**在 `verify` 上默认开
（+12% 耗时），在 `scan --probe*` 上不默认（+30%）**。理由与数据如下。

## 1. 成本

| 路径 | 语料 | 不开闸门 | 开闸门 | 增量 |
|---|---|---:|---:|---:|
| `scan --probe-compile --probe-top 16` | doctest，24 TU | 16.2 s | 21.1 s | **+30%**（+0.31 s/候选） |
| `verify --top 3 --runs 2` | benchmarks/cases/demo | 12.2 s | 13.6 s | **+12%** |

差距来自分母：`--probe-compile` 本来每个候选只编一次，闸门再加两次（原始 + 补丁，
各编到汇编）；`verify` 本来每侧要测 `runs` 次、共 2×runs 次，两次的占比自然小。

## 2. 误报：第一轮 7/16，全是假的

首轮在 doctest 上跑，16 个候选里有 7 个被判 `codegen-changed`。逐个抓汇编比对，
两次误报的成因完全不同，都不是「代码变了」：

**成因一：调试信息里的路径。** 编译数据库带 `-g`，汇编里因此有 `.cv_file` /
`.cv_loc` 与 `.debug$S` 的 `.asciz "C:\\…\\type.cpp"`。删掉一行 include 后代码没变，
但这些记录文件名与行表的字节变了。掩码也没救回来——汇编里的路径是**双反斜杠**
转义形式，只替换单反斜杠等于没掩。
修法：汇编比对统一加 `-g0`（比的是代码，不是调试信息），掩码同时处理转义形式。

**成因二：`__FILE__` 进了字符串常量。** doctest 用 `__FILE__` 拼错误信息：

```
.asciz "C:\\…\\doctest-master\\…\\type.cpp:47: Internal doctest error: …"
```

verify 把补丁写到临时目录，两份源码路径不同 → 字符串常量不同 → 连它的**符号名**
都不同（`??_C@_0KE@HELCDJLG@…` vs `??_C@_0IK@BANAIOMD@…`，长度与哈希都在名字里）。
这不是掩码能补的：符号名本身就是内容的函数。
修法：`compare()` 把两份源码**先后写到同一个路径**再编，`__FILE__`、行标记、
符号名天然一致。这比继续加掩码规则更硬——它把一整类伪差异从根上去掉了。

修完之后：16 个候选 **0 个** `codegen-changed`（11 个 compiles、5 个 compile-error）。

## 3. 真阳性：这个语料上是 0

doctest 是维护良好的库，去掉 `prelude.h` 的直连 include 后，没有一条改变了生成代码。
所以闸门在这类工程上**不改变任何结论**，它买的是保险，不是收益。

已知它确实能抓到东西的例子来自伞头实验：删掉 `<iomanip>` 后编译通过、目标文件从
21,289 字节缩到 10,183 字节（重载解析变了）。那种改动在旧 `verify` 里会被报成
`improved` 并进入「建议删除」。

## 4. MSVC 上不能开

`-S` / `-g0` 是 GCC/Clang 的写法，MSVC 的对应参数是 `/FA`，没有实现。硬开会让
「编译失败」被当成「代码变了」，**每个候选都误报**——比不开更糟。
现在 `codegen.supported()` 判能力：不支持就完全不跑，报告里也**不许**声称做过校验
（`ProjectScan.codegen_checked` 只在真跑过时为真，报告据此切换措辞）。

## 5. 决定

| 命令 | 默认 | 理由 |
|---|---|---|
| `verify` | **开**（`--no-check-codegen` 可关） | 这是「要不要合并」的确认步，+12% 换「代码没变」这个结论，值 |
| `scan --probe` / `--probe-compile` | 关（`--check-codegen` 开） | 批量初筛要的是排序，+30% 而语料上 0 真阳性，不划算 |

无论开没开，报告都会写明本轮做没做等价校验——「建议删除」在做过校验时是
「代码与原来一致」，没做时只是「能编过」，这是两个不同的结论，不该让读者猜。

## 6. 复现

```bash
# 成本对照
heavyheader scan <doctest> --compile-commands build/compile_commands.json \
  --compiler clang++ --probe-compile --probe-top 16
heavyheader scan <doctest> --compile-commands build/compile_commands.json \
  --compiler clang++ --probe-compile --check-codegen --probe-top 16

# 单条核查（把两份汇编落盘 diff）
python -c "…"   # 见本轮对话；核心是 codegen.fingerprint 前后各存一份
```
