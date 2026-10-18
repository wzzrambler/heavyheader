# G2 补充：闸门交白卷时给了什么结论（2026-10-18，zlib）

- 工程：zlib，15 个 TU，编译器 `C:/Program Files/LLVM/bin/clang.exe`。
- 头文件级候选：**0 条**（放行 0 / 拦下 58）。
- 全项目解析量：132,685 行。

## 成本集中在哪

| 头文件 | 归属 | 归因行数 | 占比 |
|---|---|---:|---:|
| `zlib.h` | project | 30,885 | 23.3% |
| `C:\mingw64\x86_64-w64-mingw32\include\_mingw.h` | system | 10,110 | 7.6% |
| `crc32.h` | project | 9,446 | 7.1% |
| `C:\mingw64\x86_64-w64-mingw32\include\stdlib.h` | system | 9,438 | 7.1% |
| `zconf.h` | project | 8,265 | 6.2% |
| `C:\mingw64\x86_64-w64-mingw32\include\corecrt.h` | system | 7,665 | 5.8% |
| `C:\mingw64\x86_64-w64-mingw32\include\stdio.h` | system | 6,700 | 5.0% |
| `C:\mingw64\x86_64-w64-mingw32\include\_mingw_mac.h` | system | 6,210 | 4.7% |
| `C:\mingw64\x86_64-w64-mingw32\include\sec_api\stdio_s.h` | system | 4,590 | 3.5% |
| `C:\mingw64\x86_64-w64-mingw32\include\string.h` | system | 4,069 | 3.1% |

前 10 个头文件占 **73.4%**；项目自有头文件占 **41.1%**。

## 收益最高但被拦下的

| 位置 | include | 放大后行数 | 影响 TU | 原因 | 证据 |
|---|---|---:|---:|---|---|
| `zlib.h` | `zconf.h` | 48,645 | 15 | owner_uses | 该文件使用了 Bytef、FAR、STDC、ZEXPORT、ZEXPORTVA（由 zconf.h 提供） |
| `zutil.h` | `zlib.h` | 46,341 | 9 | owner_uses | 该文件使用了 FAR、MAX_MEM_LEVEL、MAX_WBITS、MSDOS、OS2（由 zlib.h 提供） |
| `zconf.h` | `C:\Program Files\LLVM\lib\clang\23\include\limits.h` | 31,410 | 15 | owner_uses | 该文件使用了 UINT_MAX、ULONG_MAX、USHRT_MAX、_WIN32、__MINGW32__（由 limits.h 提供） |
| `C:\Program Files\LLVM\lib\clang\23\include\limits.h` | `C:\mingw64\x86_64-w64-mingw32\include\limits.h` | 29,415 | 15 | system_site | — |
| `C:\mingw64\x86_64-w64-mingw32\include\limits.h` | `C:\mingw64\x86_64-w64-mingw32\include\crtdefs.h` | 27,630 | 15 | system_site | — |
| `C:\mingw64\x86_64-w64-mingw32\include\crtdefs.h` | `C:\mingw64\x86_64-w64-mingw32\include\corecrt.h` | 27,450 | 15 | system_site | — |
| `C:\mingw64\x86_64-w64-mingw32\include\sec_api\stdio_s.h` | `C:\mingw64\x86_64-w64-mingw32\include\stdio.h` | 20,950 | 5 | system_site | — |
| `C:\mingw64\x86_64-w64-mingw32\include\corecrt.h` | `C:\mingw64\x86_64-w64-mingw32\include\_mingw.h` | 19,785 | 15 | system_site | — |

## 下一步建议

- 可行性闸门放行了 0 条改动（共审视 58 条收益非零的 include 行）。这是结论不是故障：本轮没有一条「删掉仍能编译」的候选，工具不会为了给出建议而降低这道门槛。
- 归因行数里只有 41.1% 落在项目自有头文件上，其余来自系统/第三方头。删除 include 行对这部分没有用——能做的是减少**包含**它们的项目头数量（拆分伞头、改前向声明），或用 `--pch` 把它们预编译掉。这两项本工具都只给估算，收益仍需实测。
- 16 条被拦下的原因是「拥有该行的文件自己就在用这些符号」。这是真实依赖：删掉它编译不过，工具判对了。要动这类成本得换手段——把大头文件拆小（`--forward-decl` 找可改前向声明的位置）、把不常用的实现挪到 .cpp，而不是删这一行。
- 2 条即使删掉也不省钱：同一批头文件已从其它 include 到达，属于「看起来冗余、实际由别处承担」。治理这类成本要从那个真正的入口下手。
- 40 条位于系统/第三方头文件内部。改别人的头文件不是本项目能承担的决策，故不列入候选（可加 `--include-system-sites` 强制列出，仅供查看）。
- 本诊断不构成删除建议：任何删除都必须先过 `verify` 重新编译全部受影响单元的实测，估算省下的行数或秒数都不能作为安全性依据。
