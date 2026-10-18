# 边级边际成本归因 · 可行性 spike

- 编译单元：24（成功 24）
- 边际秒数合计（Top 60 口径外的全部边也参与计算）：10.20s 自耗时

## 按边际秒数排序的 include 边（Top 30）

| # | 边 | 边际 ms | 边际行数 | 影响 TU 数 |
|---:|---|---:|---:|---:|
| 1 | `prelude.h` → `windows.h` | 3437.7 | 3,672,192 | 24 |
| 2 | `prelude.h` → `fstream` | 2274.3 | 931,848 | 24 |
| 3 | `fstream` → `__msvc_filebuf.hpp` | 1926.7 | 778,848 | 24 |
| 4 | `__msvc_filebuf.hpp` → `streambuf` | 1890.9 | 759,888 | 24 |
| 5 | `streambuf` → `xiosbase` | 1862.4 | 749,832 | 24 |
| 6 | `windows.h` → `winbase.h` | 1254.7 | 2,088,840 | 24 |
| 7 | `xiosbase` → `system_error` | 1123.5 | 515,784 | 24 |
| 8 | `prelude.h` → `doctest_fwd.h` | 795.0 | 187,200 | 24 |
| 9 | `windows.h` → `windef.h` | 754.5 | 715,944 | 24 |
| 10 | `system_error` → `stdexcept` | 748.4 | 413,160 | 24 |
| 11 | `windef.h` → `minwindef.h` | 725.0 | 709,224 | 24 |
| 12 | `stdexcept` → `xstring` | 723.5 | 409,008 | 24 |
| 13 | `xiosbase` → `xlocale` | 643.4 | 222,144 | 24 |
| 14 | `minwindef.h` → `winnt.h` | 628.2 | 623,688 | 24 |
| 15 | `prelude.h` → `new` | 563.2 | 156,504 | 24 |
| 16 | `new` → `exception` | 549.4 | 155,040 | 24 |
| 17 | `result.cpp` → `prelude.h` | 457.8 | 257,801 | 1 |
| 18 | `prelude.h` → `mutex` | 435.7 | 97,440 | 24 |
| 19 | `handler.cpp` → `prelude.h` | 434.1 | 257,801 | 1 |
| 20 | `debugger.cpp` → `prelude.h` | 434.0 | 257,801 | 1 |
| 21 | `prelude.h` → `cmath` | 432.0 | 109,296 | 24 |
| 22 | `exception` → `type_traits` | 430.3 | 133,824 | 24 |
| 23 | `traversal.cpp` → `traversal.h` | 429.2 | 257,855 | 1 |
| 24 | `traversal.h` → `prelude.h` | 426.1 | 257,801 | 1 |
| 25 | `exceptions.cpp` → `prelude.h` | 426.1 | 257,801 | 1 |
| 26 | `path.cpp` → `prelude.h` | 425.5 | 257,801 | 1 |
| 27 | `message.cpp` → `prelude.h` | 420.9 | 257,801 | 1 |
| 28 | `options.cpp` → `prelude.h` | 420.4 | 257,801 | 1 |
| 29 | `data.cpp` → `prelude.h` | 420.0 | 257,801 | 1 |
| 30 | `common.cpp` → `prelude.h` | 418.5 | 257,801 | 1 |

口径：边际秒数 = 该 TU 内删掉这条边后不再被解析的文件的自耗时之和；
边际行数 = 同一集合的行数之和。两者对同一批边各自排序，可直接对比。
限制：边身份按 (parent, child) 归一，同文件内两条相同 include 合并计一条。