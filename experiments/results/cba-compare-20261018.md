# CBA vs HeavyHeader 同任务对照（2026-10-18）

- 同一批 25 个 TU，clang++ 23.1.3，`-ftime-trace`
- 编译失败：0（无）

## Top-10 对照

| # | ClangBuildAnalyzer（按时间） | HeavyHeader（按归因行数） |
|---|---|---|
| 1 | json.hpp | doctest.h |
| 2 | algorithm | json.hpp |
| 3 | doctest_compatibility.h | binary_reader.hpp |
| 4 | sstream | hedley.hpp |
| 5 | set | binary_writer.hpp |
| 6 | fstream | lexer.hpp |
| 7 | make_test_data_available.hpp | to_chars.hpp |
| 8 | deque | json_pointer.hpp |
| 9 | list | serializer.hpp |
| 10 | test_utils.hpp | type_traits.hpp |

**重合 1/10**：json.hpp

## 解读边界

- 两边指标不同：CBA 是编译时间，我们是解析行数。重合度只说明
  「是否指向同一批热点头文件」，不能说明数值高低。
- 不重合项需要逐个看：可能是时间与行数确实不同序（模板重头），
  也可能是我们的归因口径问题。

## CBA 原始输出（前 2000 字符）

```
Analyzing build trace from 'C:\Users\31879\AppData\Local\Temp\cba-998rmclu\capture.bin'...
**** Time summary:
Compilation (25 times):
  Parsing (frontend):           32.0 s
  Codegen & opts (backend):      6.3 s

**** Files that took longest to parse (compiler frontend):
  2860 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u19.o
  2215 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u21.o
  2132 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u16.o
  1975 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u5.o
  1880 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u9.o
  1575 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u20.o
  1523 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u6.o
  1474 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u13.o
  1454 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u2.o
  1246 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u14.o

**** Files that took longest to codegen (compiler backend):
  1132 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u21.o
   677 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u5.o
   638 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u19.o
   611 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u9.o
   488 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u13.o
   422 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u6.o
   359 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u16.o
   194 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u20.o
   180 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u22.o
   179 ms: C:/Users/31879/AppData/Local/Temp/cba-998rmclu/u4.o

**** Templates that took longest to instantiate:
  4288 ms: nlohmann::basic_json<>::parse<const char *> (25 times, avg 171 ms)
  3777 ms: nlohmann::detail::parser<nlohmann::basic_json<>, nlohmann::detail::i... (25 times, avg 151 ms)
  2803 ms: nlohmann::basic_json<>::basic_json (76 times, avg 36 ms)
  2728 ms: nlohmann::detail::parser<nlohmann::basic_json<>, nlohmann::detail::i... (25 times,
```