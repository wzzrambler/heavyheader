# G2 补充：闸门交白卷时给了什么结论（2026-10-18，json）

- 工程：json，25 个 TU，编译器 `C:/Program Files/LLVM/bin/clang++.exe`。
- 头文件级候选：**0 条**（放行 0 / 拦下 369）。
- 全项目解析量：4,240,012 行。

## 成本集中在哪

| 头文件 | 归属 | 归因行数 | 占比 |
|---|---|---:|---:|
| `tests\thirdparty\doctest\doctest.h` | project | 177,650 | 4.2% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\stl_algo.h` | system | 147,400 | 3.5% |
| `include\nlohmann\json.hpp` | project | 131,450 | 3.1% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\basic_string.h` | system | 127,400 | 3.0% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\type_traits` | system | 109,975 | 2.6% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\cmath` | system | 97,950 | 2.3% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\stl_tree.h` | system | 86,625 | 2.0% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\stl_iterator.h` | system | 78,400 | 1.8% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\tuple` | system | 77,800 | 1.8% |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\hashtable.h` | system | 76,400 | 1.8% |

前 10 个头文件占 **26.2%**；项目自有头文件占 **18.7%**。

## 收益最高但被拦下的

| 位置 | include | 放大后行数 | 影响 TU | 原因 | 证据 |
|---|---|---:|---:|---|---|
| `include\nlohmann\adl_serializer.hpp` | `include\nlohmann\detail\conversions\from_json.hpp` | 1,582,925 | 25 | owner_uses | 该文件使用了 BasicJsonType、adl_serializer、from_json、identity_tag、nlohmann（由 from_json.hpp 提供） |
| `include\nlohmann\detail\input\binary_reader.hpp` | `include\nlohmann\detail\input\input_adapters.hpp` | 1,139,275 | 25 | owner_uses | 该文件使用了 JSON_ASSERT、JSON_HEDLEY_NON_NULL、JSON_HEDLEY_UNLIKELY、NLOHMANN_JSON_NAMESPACE_BEGIN、NLOHMANN_JSON_NAMESPACE_END（由 input_adapters.hpp 提供） |
| `include\nlohmann\detail\input\input_adapters.hpp` | `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\istream` | 1,030,200 | 25 | owner_uses | 该文件使用了 advance、base、empty、enable_if、enable_if_t（由 stl_construct.h 提供） |
| `include\nlohmann\json.hpp` | `include\nlohmann\adl_serializer.hpp` | 972,850 | 25 | owner_uses | 该文件使用了 AllocatorType、ArrayType、BinaryType、BooleanType、CompatibleType（由 macro_scope.hpp 提供） |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\istream` | `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\ios` | 931,775 | 25 | system_site | — |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\basic_ios.h` | `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\locale_facets.h` | 803,100 | 25 | system_site | — |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\locale_facets.h` | `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\streambuf_iterator.h` | 692,400 | 25 | system_site | — |
| `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\iterator` | `C:\mingw64\lib\gcc\x86_64-w64-mingw32\16.2.0\include\c++\bits\streambuf_iterator.h` | 692,400 | 25 | system_site | — |

## 下一步建议

- 可行性闸门放行了 0 条改动（共审视 369 条收益非零的 include 行）。这是结论不是故障：本轮没有一条「删掉仍能编译」的候选，工具不会为了给出建议而降低这道门槛。
- 归因行数里只有 18.7% 落在项目自有头文件上，其余来自系统/第三方头。删除 include 行对这部分没有用——能做的是减少**包含**它们的项目头数量（拆分伞头、改前向声明），或用 `--pch` 把它们预编译掉。这两项本工具都只给估算，收益仍需实测。
- 68 条被拦下的原因是「拥有该行的文件自己就在用这些符号」。这是真实依赖：删掉它编译不过，工具判对了。要动这类成本得换手段——把大头文件拆小（`--forward-decl` 找可改前向声明的位置）、把不常用的实现挪到 .cpp，而不是删这一行。
- 2 条文本判定给不出结论：命中的标识符（`config`、`value` 这类）在几十个头里都出现，不能当作依赖证据，也不能当作没在用。这些需要人工看一眼，或用语义工具（本工具不假装替你判）。
- 299 条位于系统/第三方头文件内部。改别人的头文件不是本项目能承担的决策，故不列入候选（可加 `--include-system-sites` 强制列出，仅供查看）。
- 本诊断不构成删除建议：任何删除都必须先过 `verify` 重新编译全部受影响单元的实测，估算省下的行数或秒数都不能作为安全性依据。
