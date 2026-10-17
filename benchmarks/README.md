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

### 多编译器对比（GCC / Clang / MSVC）

`toolchains` 子命令能在**同一份代码、同一组 flags**下用多个编译器各扫一遍，
把结构指标（归因行数、依赖边、TU 数——这些由编译器真实报告的 include 图算出，
是确定性的）并列到一张矩阵里，回答「换编译器是更快还是更慢、差在哪」。

```bash
# 三个编译器各扫一遍：GCC + LLVM/Clang + MSVC（Windows 上给足 INCLUDE 即可，见下）
heavyheader toolchains benchmarks/cases/demo \
  --compilers "g++,C:/Program Files/LLVM/bin/clang++.exe,C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/<ver>/bin/Hostx64/x64/cl.exe" \
  --std c++17 -I benchmarks/cases/demo/include \
  --format json -o benchmarks/results/toolchains-demo.json
heavyheader toolchains benchmarks/cases/demo \
  --compilers "g++,C:/Program Files/LLVM/bin/clang++.exe,C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/<ver>/bin/Hostx64/x64/cl.exe" \
  --std c++17 -I benchmarks/cases/demo/include \
  --format md -o benchmarks/results/toolchains-demo.md
```

结果在 `benchmarks/results/toolchains-demo.{json,md}`。横评口径的三条坚持：

- **耗时只作参考，不是结论。** 它随机器、负载、`-fsyntax-only` 与否变化；
  单次计时的个位数百分比差异基本是调度噪声（见下），据此宣布「谁更快」是不诚实的。
- **结构指标才是确定性的。** 同一台机器多次跑一致。
- **跨编译器的逐头文件差异只比项目自己的头。** 系统头在 GCC / Clang / MSVC 下路径
  各不相同，按路径拼接毫无意义；混进「同名头差异」只会制造假差异。

#### 跑 MSVC 的环境前提（Windows）

MSVC 的 `cl.exe` 不像 GCC/Clang 开箱即用，它需要正确的 `INCLUDE`（以及 PATH 上的
VC 工具目录）。**不要**用 `vcvars*.bat` 来设——它在受限环境里会因为 `reg.exe`
被沙箱拦掉而失败。直接显式导出环境变量即可：

```bash
export INCLUDE="C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/<ver>/include;C:/Program Files (x86)/Windows Kits/10/Include/<sdk>/ucrt;C:/Program Files (x86)/Windows Kits/10/Include/<sdk>/shared;C:/Program Files (x86)/Windows Kits/10/Include/<sdk>/um"
export PATH="$PATH:C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/<ver>/bin/Hostx64/x64"
```

`toolchains` 对 MSVC 只做 `-Zs`（仅语法检查、不链接），所以其实**不需要 `LIB`**，
`INCLUDE` 给够就能解析到 `<vector>` 等标准库头。

> demo 用例的头文件都加了**带 BOM 的 UTF-8** 开头（注释是中文），这样 MSVC 能按
> UTF-8 读源码，不会因为系统 GBK 代码页误判非 ASCII 内容。

#### 两个踩过的坑（已修复，记在此备查）

1. **中文 Windows 下 `cl` 的诊断输出是 GBK 编码。** Python（PEP 540 UTF-8 mode）
   下 `locale.getpreferredencoding()` 会返回 `utf-8`，按它解码会把中文前缀
   「注意: 包含文件:」读成乱码，导致 `/showIncludes` 的 include 树解析出 0 条边。
   修复：`MsvcDriver._output_encoding()` 改走 `ctypes.windll.kernel32.GetOEMCP()`
   拿真实 OEM 代码页（中文 Windows 上是 `cp936`）。`parse_show_includes` 的正则同时
   接受英文（Note: including file:）与中文（注意: 包含文件:）两种前缀。
2. **MSVC 对同一文件会报出「相对」和「绝对」两种路径形态**（取决于 TU 的工作目录），
   同一个头被当成两个 key，归因与跨编译器逐头对比都会错位、且运行间抖动。修复：
   所有头文件 key 统一 `resolve()` 成绝对真实路径；`classify_headers` 据此判定
   项目/系统头，候选检测仍用 TU 报告的原始路径去查——两套 key 空间各司其职，
   不再混用（详见 `src/heavyheader/attrib/graph.py` 的 `_normalise_header_key`
   与 `scanner.py` 里 `kinds` 的取法）。

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
