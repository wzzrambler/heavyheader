# 闸门的统计口径：只看 owner 还是看使用方（2026-10-18，json）

> **口径注记（2026-10-21 合入时补）**：本归档产自**声明级口径的实验分支**（`header_ids` 走 `attrib/decls.declared_names`）。该口径**未合入判定主线**——它改的是命中集合本身，换上去之后 2026-10-19/20 三轮精度工作的数字都要重跑才成立，实测会让 3 个精度测试回归（见 `docs/methodology.md` §6）。因此这里的候选池与主线 G2 不是同一口径，两组数字**不可混用**；本归档只用于支撑「下游引用对能否编译没有预测力」这个否定性结论。


- 工程：json，25 个 TU，可枚举 include 行 70 条。
- 放行条数：owner 口径 2 / 使用方(IN_USE) 5 / 使用方(+UNKNOWN) 3。

| 改动位置 | 头文件 | 行 | 受影响 TU | 累计行 | owner 判定 | 使用方人数 | 使用方口径 | 说明 |
|---|---|---:|---:|---:|---|---:|---|---|
| `tests\thirdparty\doctest\doctest_compatibility.h` | `doctest.h` | - | 25 | 179000 | in_use | 25 | 拦下 | 两口径都拦 |
| `tests\thirdparty\doctest\doctest_compatibility.h` | `iosfwd` | - | 25 | 202150 | in_use | 25 | 拦下 | 两口径都拦 |
| `tests\thirdparty\doctest\doctest.h` | `ciso646` | - | 25 | 53225 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `cstddef` | - | 25 | 4975 | plausibly_unused | 25 | 拦下 | **放行是错的**（使用方在用） |
| `include\nlohmann\json.hpp` | `functional` | - | 25 | 158450 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `iterator` | - | 25 | 9050 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `memory` | - | 25 | 280025 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `utility` | - | 25 | 8150 | plausibly_unused | 25 | 放行 | 两口径都放行 |
| `include\nlohmann\json.hpp` | `vector` | - | 25 | 145775 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `adl_serializer.hpp` | - | 25 | 972850 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `byte_container_with_subtype.hpp` | - | 25 | 2575 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `hash.hpp` | - | 25 | 3225 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `binary_reader.hpp` | - | 25 | 555250 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `parser.hpp` | - | 25 | 12975 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `internal_iterator.hpp` | - | 25 | 4175 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `iter_impl.hpp` | - | 25 | 18775 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `json_reverse_iterator.hpp` | - | 25 | 3250 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `json_custom_base_class.hpp` | - | 25 | 975 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `json_pointer.hpp` | - | 25 | 24700 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `json_ref.hpp` | - | 25 | 1950 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `binary_writer.hpp` | - | 25 | 49625 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `serializer.hpp` | - | 25 | 228525 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `ordered_map.hpp` | - | 25 | 8975 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\json.hpp` | `macro_unscope.hpp` | - | 25 | 5075 | unknown | 25 | 放行 | owner 拦下，使用方没人用（现状偏保守） |
| `include\nlohmann\json.hpp` | `algorithm` | - | 25 | 232425 | in_use | 25 | 拦下 | 两口径都拦 |
| `include\nlohmann\adl_serializer.hpp` | `abi_macros.hpp` | - | 25 | 2500 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\adl_serializer.hpp` | `from_json.hpp` | - | 25 | 1582200 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\adl_serializer.hpp` | `to_json.hpp` | - | 25 | 17925 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\from_json.hpp` | `array` | - | 25 | 45375 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\from_json.hpp` | `forward_list` | - | 25 | 67100 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\from_json.hpp` | `map` | - | 25 | 662200 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\from_json.hpp` | `unordered_map` | - | 25 | 193700 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\from_json.hpp` | `valarray` | - | 25 | 249850 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\from_json.hpp` | `exceptions.hpp` | - | 25 | 279425 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\from_json.hpp` | `identity_tag.hpp` | - | 25 | 525 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\exceptions.hpp` | `value_t.hpp` | - | 25 | 138700 | in_use | 28 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\exceptions.hpp` | `string_escape.hpp` | - | 25 | 1800 | in_use | 28 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\exceptions.hpp` | `position_t.hpp` | - | 25 | 925 | in_use | 28 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\exceptions.hpp` | `cpp_future.hpp` | - | 25 | 4275 | in_use | 28 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\exceptions.hpp` | `type_traits.hpp` | - | 25 | 80100 | in_use | 28 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\exceptions.hpp` | `string_concat.hpp` | - | 25 | 17375 | in_use | 28 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\exceptions.hpp` | `numeric` | - | 25 | 29825 | in_use | 28 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\value_t.hpp` | `cstdint` | - | 25 | 38250 | unknown | 29 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\value_t.hpp` | `macro_scope.hpp` | - | 25 | 164450 | in_use | 29 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\macro_scope.hpp` | `detected.hpp` | - | 25 | 2350 | in_use | 30 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\macro_scope.hpp` | `hedley.hpp` | - | 25 | 51125 | in_use | 30 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\macro_scope.hpp` | `version` | - | 25 | 68800 | in_use | 30 | 放行 | owner 拦下，使用方没人用（现状偏保守） |
| `include\nlohmann\detail\macro_scope.hpp` | `cassert` | - | 25 | 30125 | in_use | 30 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\meta\detected.hpp` | `void_t.hpp` | - | 25 | 600 | in_use | 31 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\meta\type_traits.hpp` | `limits` | - | 25 | 55975 | in_use | 29 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\meta\type_traits.hpp` | `iterator_traits.hpp` | - | 25 | 1525 | in_use | 29 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\meta\type_traits.hpp` | `begin.hpp` | - | 25 | 425 | in_use | 29 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\meta\type_traits.hpp` | `end.hpp` | - | 25 | 425 | in_use | 29 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\meta\type_traits.hpp` | `json_fwd.hpp` | - | 25 | 1875 | in_use | 29 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\string_concat.hpp` | `cstring` | - | 25 | 80675 | in_use | 29 | 放行 | owner 拦下，使用方没人用（现状偏保守） |
| `include\nlohmann\detail\conversions\to_json.hpp` | `iteration_proxy.hpp` | - | 25 | 6050 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\conversions\to_json.hpp` | `std_fs.hpp` | - | 25 | 725 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\input\binary_reader.hpp` | `input_adapters.hpp` | - | 25 | 1139275 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\input\binary_reader.hpp` | `json_sax.hpp` | - | 25 | 18175 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\input\binary_reader.hpp` | `lexer.hpp` | - | 25 | 40825 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\input\binary_reader.hpp` | `is_sax.hpp` | - | 25 | 3975 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\input\input_adapters.hpp` | `numeric` | - | 25 | 29825 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\input\input_adapters.hpp` | `istream` | - | 25 | 1030200 | in_use | 27 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\iterators\internal_iterator.hpp` | `primitive_iterator.hpp` | - | 25 | 3300 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\output\binary_writer.hpp` | `output_adapters.hpp` | - | 25 | 3675 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\output\serializer.hpp` | `iomanip` | - | 25 | 242825 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\output\serializer.hpp` | `to_chars.hpp` | - | 25 | 27950 | in_use | 26 | 拦下 | 两口径都拦 |
| `include\nlohmann\detail\macro_unscope.hpp` | `hedley_undef.hpp` | - | 25 | 3950 | unknown | 26 | 放行 | owner 拦下，使用方没人用（现状偏保守） |
| `tests\src\make_test_data_available.hpp` | `test_data.hpp` | - | 4 | 4 | in_use | 4 | 拦下 | 两口径都拦 |
| `tests\src\make_test_data_available.hpp` | `doctest.h` | - | 4 | 36940 | in_use | 4 | 拦下 | 两口径都拦 |

