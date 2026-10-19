# 语料计时选型：spdlog TU 层验证（2026-10-19）

## 背景

G2 闭环在 doctest/zlib/json 上的计时采纳率是 1/13、1/16、0/16——瓶颈在语料的
冗余度上限，不在排序。本轮任务：换头文件更重的语料，验证计时口径能否稳定命中。

## 筛选（`experiments/corpus_screen.py`，纯扫描不计时）

四个候选（fmt / nlohmann-json / spdlog / cpp-httplib，配置取自
`benchmarks/run_all.py`）：

| 工程 | TU ok | site 闸门池 | TU 层疑似未使用 |
|---|---:|---:|---:|
| fmt | 3 | 0 | 0 |
| nlohmann-json | 25 | 0 | 0 |
| spdlog | 8 | 0 | **2** |
| cpp-httplib | 18 | 0 | 0 |

**头文件 site 层在维护良好的库里没有可删项**（est% 高达 17–69% 的热点 site
全部被判 in_use，且判得对——winsock2.h、json.hpp 这些删了就是编译错误）。
唯一的计时信号在 spdlog 的 **TU 层**（.cpp 自己的 include 冗余）。

## 计时验证（`experiments/corpus_timing.py`，交替 min-of-3、噪声线 1%、improved 需复测）

usage 分层排序的 TU 臂，三次独立运行（各自重新扫描、编译、测量）：

| 排名 | 候选 | run 1 | run 2 | run 3 |
|---|---|---|---|---|
| 1 | `example.cpp → ranges.h` | compile-error | compile-error | compile-error |
| 2 | `async.cpp → async.h` | no-change（+1.13%） | **improved +2.39%** | **improved +2.70%** |
| 3 | `cfg.cpp → helpers-inl.h` | **improved +77.84%** | **improved +78.44%** | **improved +78.35%** |

* `cfg.cpp` 的收益三次复现（聚合 566 → 125 ms 量级，差距远超抖动）；
  reach=1：这条独占子树只有 cfg.cpp 付钱。
* `async.cpp` 在噪声线边缘（+1.1% → +2.4% → +2.7%），如实标注为「不稳定改善」。
* 排名第 1 的 `ranges.h` 判了「疑似未使用」但删除编译失败——诊断结论：**已知
  边界（AST 区）的又一实例，非可修新根因**。example.cpp 格式化 `std::vector`
  （第 224 行），区间格式化的重载模板在 `fmt/ranges.h` 里；删掉后错误不是
  「未声明的标识符」而是 `implicit instantiation of undefined template
  'fmt::detail::type_is_unformattable_for<vector<int>, char>'`——**由重载决议
  选中的模板实例化**，源码里没有任何标识符唯一指向这个头（`format`、`vector`
  都有别的供给者），文本层无信号可抓。同 doctest `context_scope.h` 的保守
  误判同类。防护照旧：verify 实测拦截，用户不会被误导。

## 结论

1. **计时口径的推荐语料组合**：doctest / zlib（已有）+ **spdlog TU 层**——
   top-3 内 2 条经复测的 improved，P@3 改善率 67%，三次独立复现。
2. **头文件 site 层不要再烧预算**：四个维护良好的库闸门池全 0；site 层的
   冗余模式（prelude.h 式聚合头）在成熟库里已被清完。
3. **聚合规模教训**：spdlog 8 TU 聚合只有 566 ms，1% 噪声线 = 5.7 ms，
   计时统计在这个量级意义有限——大额收益（78%）可辨，临界收益（2.4%）
   需要更大聚合才能稳定判定。若要再抬，选聚合在秒级以上的工程。

## 复现

```bash
python experiments/corpus_screen.py spdlog          # 筛选
python experiments/corpus_timing.py --case spdlog --top 3 --arm tu   # 计时
```

数据：`corpus-timing-spdlog-tu8-20261019.json`（run 1，8 候选全测）、
`corpus-timing-spdlog-tu3-20261019.json`（run 3；run 2 与 run 3 同名被覆盖，
其读数 78.443% / 2.390% 以本报告记录为准）。
