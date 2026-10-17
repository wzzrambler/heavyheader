# 回顾性检验：新排序把已实测的 17 个结局放在第几位

> 由 `experiments/rank_check.py` 生成。**零次构建试验**——用的是 `control-20261017.json` 里已经测出来的结局当答案，只看新排序会怎么排。
> 改过 `attrib/graph.py` 或 `attrib/usage.py` 之后就跑它。

工程：**nlohmann/json v3.11.3**　编译器：`g++ (x86_64-win32-seh-rev1, Built by MinGW-Builds project) 16.2.0`

## 新排序的候选空间

- 候选（(单元, include) 二维空间）：**222** 个；按实验口径每头只留一条：**18** 个
- 用法判定分布：in_use 218 个、plausibly_unused 4 个
- 扫描耗时：**69.4 s**

名次的分母是**完整列表**（工具真正排序的空间，含系统头）。

## 3 个已知有效改动排在哪

| 单元 | include | 单测编译时间降幅 | 新排名 | 名次分位 | 用法判定 |
|---|---|---:|---:|---:|---|
| `unit-windows_h` | `C:/mingw64/x86_64-w64-mingw32/include/windows.h` | +39.6% | 52 | 23% | in_use |
| `unit-algorithms` | `C:/mingw64/lib/gcc/x86_64-w64-mingw32/16.2.0/include/c++/algorithm` | +9.1% | 66 | 30% | in_use |
| `unit-cbor` | `iomanip` | +8.5% | — | — | — |

- 落在前 10%：**0/3**；前 25%：**1/3**；不在候选空间里：**1** 个

## 13 个已知失败的候选有没有被压下去

| 单元 | include | 新排名 | 用法判定 | 为什么（工具给的） |
|---|---|---:|---|---|
| `unit-algorithms` | `nlohmann/json.hpp` | 48 | in_use | 独占闭包含大文件 json.hpp（5258 行） |
| `unit-conversions` | `include/nlohmann/json.hpp` | 65 | in_use | 独占闭包含大文件 stl_algo.h（5896 行） |
| `unit-algorithms` | `doctest_compatibility.h` | 87 | in_use | CHECK、CHECK_THROWS_WITH_AS、SECTION 由 doctest.h 提供（在本 includ… |
| `unit-bjdata` | `set` | 127 | in_use | begin、count、empty 由 stl_multiset.h 提供（在本 include 的下游），删除会编译… |
| `unit-binary_formats` | `fstream` | 144 | in_use | 独占闭包含大文件 fstream（1374 行） |
| `unit-bson` | `sstream` | 146 | in_use | 独占闭包含大文件 sstream（1426 行） |
| `unit-conversions` | `C:/mingw64/lib/gcc/x86_64-w64-mingw32/16.2.0/include/c++/deque` | 159 | in_use | begin、end、equal 由 deque.tcc 提供（在本 include 的下游），删除会编译失败 |
| `unit-regression2` | `tests/thirdparty/doctest/doctest_compatibility.h` | 167 | in_use | CHECK、CHECK_NOTHROW、CHECK_THROWS_AS 由 doctest.h 提供（在本 inclu… |
| `unit-bjdata` | `test_utils.hpp` | 177 | in_use |  |
| `unit-binary_formats` | `make_test_data_available.hpp` | 187 | in_use |  |
| `unit-custom-base-class` | `C:/mingw64/lib/gcc/x86_64-w64-mingw32/16.2.0/include/c++/sstream` | 201 | in_use | Ptr、at、binary 由 gthr-default.h 提供（在本 include 的下游），删除会编译失败 |
| `unit-custom-base-class` | `C:/mingw64/lib/gcc/x86_64-w64-mingw32/16.2.0/include/c++/set` | 202 | in_use | array、copy、ptr 由 stl_pair.h 提供（在本 include 的下游），删除会编译失败 |
| `unit-constructor1` | `list` | 211 | in_use | begin、cbegin、cend 由 list.tcc 提供（在本 include 的下游），删除会编译失败 |

- 被判定为「正在使用 / 无法判定」而压后的：**13/13**（另有 0 个根本不在候选空间里，说明它们的闭包已被别的 include 覆盖，删了也省不下时间）

## 怎么读这张表

**排名靠前不等于已经验证。** 它仍然只是排序，最后的判定权在 `verify`：那一层会真的注释掉这一行、重编、测时间。这份检验只回答一个问题——**在花任何一次构建试验之前，工具把已知的好改动和已知的坏改动分开了吗。**

“—”表示这个 (单元, include) 根本不在新候选空间里，通常是该 include 被判定为「删掉也省不下任何东西」（它的闭包已经被别的 include 覆盖）。
