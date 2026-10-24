# PCH 维护税实测：把「省多少」和「改一次要付多少」放在一起（2026-10-24）

## 0. 为什么补这一项

PCH 建议此前只报「省多少行」，把代价写在一条 caveat 里（「未计入 PCH 失效」）。
一个只报收益不报代价的建议不可采纳：把改动频繁的头放进 PCH，等于让每次改它都
触发全量重编。本轮把代价量化进 `PCHRecommendation`：

- `rebuild_header` / `rebuild_units`：闭包里被最多单元包含的**项目头**，以及它
  触达的单元数。平局时取自身行数更大的那个——用户实际会改的是大且常改的头
  （`json.hpp`），不是顺带被拉进来的小适配头。
- `rebuild_seconds`：这些单元一次重编的耗时合计（逐单元实测墙钟之和；缺失时退化
  到 `-ftime-report` 的 TOTAL，**不记 0**——记 0 会让维护税看起来是免费的）。
- 系统头（`bits/stl_algo.h` 这类）不参与：谁都改不了它，算进来只会把风险说大。

## 1. 实测：nlohmann/json v3.11.3，63 个编译单元

```
heavyheader scan <json> --compile-commands build/compile_commands.json --pch --pch-top 5
```

## 预编译头（PCH）收益估算

建议将下列 5 个头文件纳入 PCH。它们的传递闭包共 **159,995 行**，其中 **63/63** 个编译单元已全部包含它们（覆盖率 100%）。

| 头文件 | 行数 | 被引用 TU |
|---|---:|---:|
| `include\nlohmann\json.hpp` | 5,258 | 63 |
| `include\nlohmann\detail\input\binary_reader.hpp` | 3,009 | 63 |
| `include\nlohmann\detail\output\serializer.hpp` | 988 | 63 |
| `include\nlohmann\adl_serializer.hpp` | 55 | 63 |
| `include\nlohmann\detail\conversions\from_json.hpp` | 497 | 63 |

**估算净收益：9,919,690 行（占全项目头文件解析量 91.2%）——值得做。**

**维护税：**闭包里被最多单元包含的是 `include\nlohmann\json.hpp`（63/63 个单元，100%）。改动它会让整份 .gch 作废、这些单元全部重编，按本次扫描的编译方式计约 **144.9 s**（扫描若用完整编译，这个数会更高）。这个头几乎是全工程的公共依赖，改它本来也要全量重编——放进 PCH 不额外增加风险。

> 这是估算，不是承诺。四条限制：
> - 基于行数代理，不是秒数。模板重型头文件的实际成本会被低估。
> - 上限估计：真实 PCH 必须是每个使用它的 TU 的第一个 include，且各 TU 间字节一致。include 顺序不一致的项目实际收益会更低。
> - 维护税（rebuild_seconds）只计受影响单元的重编耗时，不含 .gch 自身的重建时间；它衡量的是「改一次要付多少」，不是「每次构建都要付」。
> - 本工具不生成 PCH，只做估算。

## 2. 判读

- 维护税覆盖 **63/63（100%）**，但 `json.hpp` 本来就被所有单元包含——改它无论如何
  都要全量重编，**放进 PCH 不额外增加风险**。报告里这句是自动判定的（阈值 90%）。
- 这个语料是「公共依赖型」：候选头全触达。**被部分单元包含**的头才产生真实增量税，
  该场景由 `tests/test_pch.py` 的两个用例锁住（5 个单元里 3 个包含 → 税只算那 3 个）。
- 耗时口径：本次按默认 `-fsyntax-only` 编，144.9 s 是「语法检查级重编」的合计；
  完整构建实测 292 s CPU（`experiment-20261017.md`），所以真实维护税不低于这个数。
  报告已写明「扫描若用完整编译，这个数会更高」。

## 3. 复现

```bash
python -c "import sys; sys.path.insert(0,'experiments'); from build import write_compile_commands; \
from pathlib import Path; r=Path('experiments/_work/json').resolve(); \
write_compile_commands(r, r/'build'/'compile_commands.json')"
heavyheader scan <json-abs> --compile-commands <json-abs>/build/compile_commands.json \
  --pch --pch-top 5 --jobs 8 --output pch.md
```

注意：`write_compile_commands` 必须传**绝对路径**，否则相对路径与 cwd 叠加、
所有单元编译失败（本次实测踩到，63/63 全红）。
