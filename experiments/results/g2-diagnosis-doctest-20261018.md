# G2 补充：闸门交白卷时给了什么结论（2026-10-18，doctest）

- 工程：doctest，24 个 TU，编译器 `C:/Program Files/LLVM/bin/clang++.exe`。
- 头文件级候选：**16 条**（放行 16 / 拦下 557）。
- 全项目解析量：6,562,385 行。

## 成本集中在哪

| 头文件 | 归属 | 归因行数 | 占比 |
|---|---|---:|---:|
| `C:\mingw64\x86_64-w64-mingw32\include\winnt.h` | system | 258,960 | 3.9% |
| `C:\Program Files\LLVM\lib\clang\23\include\avx512fintrin.h` | system | 224,424 | 3.4% |
| `C:\Program Files\LLVM\lib\clang\23\include\avx512vlintrin.h` | system | 193,608 | 3.0% |
| `C:\mingw64\x86_64-w64-mingw32\include\winuser.h` | system | 163,272 | 2.5% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\stl_algo.h` | system | 141,504 | 2.2% |
| `C:\Program Files\LLVM\lib\clang\23\include\avx2intrin.h` | system | 124,560 | 1.9% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\basic_string.h` | system | 122,304 | 1.9% |
| `C:\Program Files\LLVM\lib\clang\23\include\avxintrin.h` | system | 121,536 | 1.9% |
| `C:\Program Files\LLVM\lib\clang\23\include\emmintrin.h` | system | 118,560 | 1.8% |
| `C:\mingw64\x86_64-w64-mingw32\include\winerror.h` | system | 118,224 | 1.8% |

前 10 个头文件占 **24.2%**；项目自有头文件占 **1.6%**。

## 收益最高但被拦下的

| 位置 | include | 放大后行数 | 影响 TU | 原因 | 证据 |
|---|---|---:|---:|---|---|
| `doctest\parts\private\prelude.h` | `C:\mingw64\x86_64-w64-mingw32\include\windows.h` | 2,944,992 | 24 | owner_uses | 该文件使用了 NOMINMAX、WIN32_LEAN_AND_MEAN、WINAPI_FAMILY、WINAPI_FAMILY_APP、__cdecl（由 windows.h 提供） |
| `C:\mingw64\x86_64-w64-mingw32\include\windef.h` | `C:\mingw64\x86_64-w64-mingw32\include\minwindef.h` | 2,248,752 | 24 | system_site | — |
| `C:\mingw64\x86_64-w64-mingw32\include\minwindef.h` | `C:\mingw64\x86_64-w64-mingw32\include\winnt.h` | 2,217,912 | 24 | system_site | — |
| `C:\mingw64\x86_64-w64-mingw32\include\windows.h` | `C:\mingw64\x86_64-w64-mingw32\include\windef.h` | 2,192,928 | 24 | system_site | — |
| `C:\mingw64\x86_64-w64-mingw32\include\winnt.h` | `C:\Program Files\LLVM\lib\clang\23\include\x86intrin.h` | 1,886,160 | 24 | system_site | — |
| `C:\Program Files\LLVM\lib\clang\23\include\x86intrin.h` | `C:\Program Files\LLVM\lib\clang\23\include\immintrin.h` | 1,824,744 | 24 | system_site | — |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\fstream` | `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\istream` | 1,009,752 | 24 | system_site | — |
| `doctest\parts\private\prelude.h` | `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\fstream` | 997,056 | 24 | owner_uses | 该文件使用了 WIN32_LEAN_AND_MEAN、mutex、stdout（由 gthr-default.h 提供） |

## 下一步建议

- 可行性闸门放行 16 条；以下成本分布说明其余 557 条为什么没进候选表。
- 归因行数里只有 1.6% 落在项目自有头文件上，其余来自系统/第三方头。删除 include 行对这部分没有用——能做的是减少**包含**它们的项目头数量（拆分伞头、改前向声明），或用 `--pch` 把它们预编译掉。这两项本工具都只给估算，收益仍需实测。
- 47 条被拦下的原因是「拥有该行的文件自己就在用这些符号」。这是真实依赖：删掉它编译不过，工具判对了。要动这类成本得换手段——把大头文件拆小（`--forward-decl` 找可改前向声明的位置）、把不常用的实现挪到 .cpp，而不是删这一行。
- 3 条文本判定给不出结论：命中的标识符（`config`、`value` 这类）在几十个头里都出现，不能当作依赖证据，也不能当作没在用。这些需要人工看一眼，或用语义工具（本工具不假装替你判）。
- 505 条位于系统/第三方头文件内部。改别人的头文件不是本项目能承担的决策，故不列入候选（可加 `--include-system-sites` 强制列出，仅供查看）。
- 本诊断不构成删除建议：任何删除都必须先过 `verify` 重新编译全部受影响单元的实测，估算省下的行数或秒数都不能作为安全性依据。
