# 边级边际成本归因 · 可行性 spike

- 编译单元：24（成功 24）
- 边际秒数合计（Top 60 口径外的全部边也参与计算）：10.37s 自耗时

## 按边际秒数排序的 include 边（Top 30）

| # | 边 | 边际 ms | 边际行数 | 影响 TU 数 |
|---:|---|---:|---:|---:|
| 1 | `prelude.h` → `windows.h` | 3424.3 | 3,663,984 | 24 |
| 2 | `windows.h` → `winbase.h` | 1213.5 | 2,059,512 | 24 |
| 3 | `prelude.h` → `doctest_fwd.h` | 528.8 | 60,923 | 24 |
| 4 | `streambuf` → `xiosbase` | 524.9 | 110,760 | 24 |
| 5 | `is_nan.cpp` → `prelude.h` | 497.1 | 257,801 | 1 |
| 6 | `xiosbase` → `xlocale` | 427.8 | 98,856 | 24 |
| 7 | `debugger.cpp` → `prelude.h` | 427.4 | 257,801 | 1 |
| 8 | `approx.cpp` → `prelude.h` | 424.6 | 257,801 | 1 |
| 9 | `contains.cpp` → `prelude.h` | 424.1 | 257,801 | 1 |
| 10 | `main.cpp` → `prelude.h` | 420.8 | 257,801 | 1 |
| 11 | `path.cpp` → `prelude.h` | 417.9 | 257,801 | 1 |
| 12 | `expression.cpp` → `prelude.h` | 411.3 | 257,801 | 1 |
| 13 | `windows.h` → `winuser.h` | 378.3 | 387,000 | 24 |
| 14 | `prelude.h` → `mutex` | 345.9 | 69,384 | 24 |
| 15 | `cwchar` → `wchar.h` | 159.0 | 24,528 | 24 |
| 16 | `prelude.h` → `iomanip` | 157.4 | 62,400 | 24 |
| 17 | `cstdlib` → `stdlib.h` | 152.1 | 37,920 | 24 |
| 18 | `prelude.h` → `vector` | 150.7 | 104,664 | 24 |
| 19 | `prelude.h` → `algorithm` | 136.4 | 259,509 | 23 |
| 20 | `cstdio` → `stdio.h` | 125.7 | 58,752 | 24 |
| 21 | `mutex` → `thread` | 122.5 | 25,824 | 24 |
| 22 | `windows.h` → `winnls.h` | 116.2 | 84,384 | 24 |
| 23 | `winbase.h` → `winerror.h` | 110.9 | 1,561,248 | 24 |
| 24 | `xlocale` → `xlocinfo` | 100.2 | 16,992 | 24 |
| 25 | `windows.h` → `wincon.h` | 95.7 | 35,040 | 24 |
| 26 | `xstring` → `__msvc_string_view.hpp` | 82.6 | 48,000 | 24 |
| 27 | `cstring` → `string.h` | 78.1 | 17,136 | 24 |
| 28 | `math.h` → `corecrt_math.h` | 73.5 | 24,048 | 24 |
| 29 | `yvals.h` → `crtdbg.h` | 72.6 | 21,120 | 24 |
| 30 | `windows.h` → `winreg.h` | 72.6 | 41,328 | 24 |

口径：边际秒数 = 该 TU 内删掉这条边后不再被解析的文件的自耗时之和；
边际行数 = 同一集合的行数之和。两者对同一批边各自排序，可直接对比。
限制：边身份按 (parent, child) 归一，同文件内两条相同 include 合并计一条。