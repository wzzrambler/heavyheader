# 多编译器构建成本对比 · demo

编译器：g++, C:/Program Files/LLVM/bin/clang++.exe, C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.43.34808/bin/Hostx64/x64/cl.exe

| 编译器 | TU | 归因行数 | 项目头 | 非项目头 | 依赖边 | 耗时(s) | 相对基准 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `g++` | 2 | 206,106 | 44 | 206,062 | 425 | 0.75 | 基准 | — |
| `C:/Program Files/LLVM/bin/clang++.exe` | 2 | 166,159 | 44 | 166,115 | 368 | — | — | 无 TOTAL 计时 |
| `C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.43.34808/bin/Hostx64/x64/cl.exe` | 2 | 162,943 | 44 | 162,899 | 221 | 0.91 | +21.5% | — |

本机实测：`g++` 最快，比 `C:/Program Files/Microsoft Visual Studio/2022/Community/VC/Tools/MSVC/14.43.34808/bin/Hostx64/x64/cl.exe` 少花 17.7% 的墙钟时间。

## 项目头文件的跨编译器差异

所有项目头在各编译器下的归因行数一致——说明结构解析与工具链无关，差异只体现在耗时与非项目头（系统/第三方）的解析量上。

> **口径**：结构指标（TU / 归因行数 / 依赖边）由编译器真实报告的 include 图算出，是确定性的；**耗时只作参考**，它随机器与负载变化，且此处为 `-fsyntax-only` 的实测。逐头文件对比只统计项目自己的头（系统头路径在不同工具链下不同，按路径比较没有意义）。
