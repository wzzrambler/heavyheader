# 证据层：一条结论站在哪一级证据上（2026-10-20）

## 1. 为什么要分层

供给侧漏报诊断（§9.19.7）发现：8 条落地收益里 **7 条被文本判据判成
`in_use` / `unknown`**，却全都过了汇编等价闸门。也就是说——**判据说不清的地方，
编译器说得清**。

但旧界面把两者混在同一句「建议删除」里：一条 usage=`plausibly_unused` 的候选
和一条 usage=`unknown` 但汇编指纹一致的候选，输出长得几乎一样。读者无从分辨
哪句是证据、哪句是推断。

于是把每条结论按证据强度分成三档，并且规定：**不许跨档引用**。

| 档位 | 含义 | 要求 |
|---|---|---|
| `verified` | 汇编指纹一致 **且** 收益过噪声线（+ 复测保留） | 可删，且值得删 |
| `equivalent` | 汇编指纹一致，收益不可测量 | 程序不变已证，省不出时间 |
| `compiles_only` | 只证明删了能编过 | **不许说「程序没变」** |

闸门在 CLI 的 `verify` 里默认开，但库调用默认关、MSVC 也给不出汇编——这几种
情况下只能停在 `compiles_only`。**省下的两次编译不能变成一句没量过的承诺。**

## 2. 实测：Lua 34 TU，送测池 20 条

选池复用 `timing_funnel.select_pool`，与归档的 25%、`repeat_probe` 的 40%
**同一个分母**。协议 `runs=3 / confirm=2 / --check-codegen`。

跑了**两遍**，结果不一样，这是本轮最该记住的一点：

| 遍次 | verified | equivalent | 说明 |
|---|---:|---:|---|
| 第 1 遍 | 5 | 15 | |
| 第 2 遍 | 9 | 11 | 同池、同协议 |

**同协议下最高档的条数在 5 与 9 之间摆动**——与「25% vs 40%」同源：2% 阈值
附近单遍判定等于抽签。所以 `verified` 的条数只能当**量级**用，不能当精确数。

### 文本判据 × 证据层（第 2 遍）

| usage | verified | equivalent |
|---|---:|---:|
| `unknown` | 8 | 7 |
| `in_use` | 1 | 1 |
| `plausibly_unused` | **0** | 3 |

**站在最高档的 9 条里，推荐集贡献 0 条**（第 1 遍的 5 条同样全是 `unknown`）。
这把 §9.19.7 的方向性证据变成了硬数字：工具明确推荐的那一层，在 Lua 上一条
最高档结论都没产出。零假阳性是拿这个漏报换来的。

### `equivalent` 档没有带来新建议

11 条 `equivalent` 全部 `lean=keep`。原因是它们全是系统头（stdio / stdlib /
string），被十几个 TU 共用，不满足维护收益判据里的「全项目唯一使用者」。
**这一档在 Lua 上的作用是让「编译通过」这句话不再含糊，而不是新增行动建议。**

doctest 上同样如此：5 条 `equivalent` 全是项目内头，但净收益只有 20–28 行，
远低于 1,000 行的判据，`lean` 也全是 `keep`。

## 3. 闸门有效性：拦下的那几条是不是真有害

**只放行不拦截的闸门等于没有闸门。** 新层级的安全性全押在闸门上，所以必须
反向取证——被拦下的候选，删掉之后到底变了什么（`experiments/gate_forensics.py`，
与 `codegen` 同源：同一输出路径、`_unit_options` / `_patched_options`、
`codegen.fingerprint`）。

### doctest 149 TU：7 条全部拦对

| TU | 头 | 汇编变化 | 删掉之后消失了什么 |
|---|---|---|---|
| default.cpp | foo.h | 3,136 → 141 行 | `??__EDOCTEST_ANON_VAR_33@@YAXXZ` |
| return42.cpp | foo.h | 3,136 → 141 行 | `??__EDOCTEST_ANON_VAR_25@@YAXXZ` |
| context.cpp | junit.h | 45,284 → 45,119 行 | `callq ?registerReporterImpl@detail@doctest…` |
| generators.cpp | header.h | 47,460 → 44,939 行 | `DOCTEST_ANON_SUITE_19` 的匿名变量 |
| asserts_used_outside_of_tests.cpp | header.h | 6,514 → 3,572 行 | 同上 |
| enums.cpp | header.h | 15,576 → 12,499 行 | 同上 |
| main.cpp | header.h | 120,929 → 118,154 行 | 同上 |

全是 doctest 的**自动注册机制**：删掉 include 之后，测试用例 / reporter 的静态
注册整个消失——**编译通过、少解析一个文件，但那些测试从此不运行**。这是最典型的
「能编过但已不是同一个程序」，闸门一条没漏。

### Lua 34 TU：拦下 1 条，同样是真雷

`lua.c → windows.h`：少解析 **293,142 行**（全局最大的一条），删掉后汇编少了
3 行：

```
.section .drectve,"yni"
.ascii  " /DEFAULTLIB:uuid.lib"
.ascii  " /DEFAULTLIB:uuid.lib"
```

这是 `windows.h` 里的 `#pragma comment(lib, "uuid.lib")` 注入的链接器指令。
**编译期完全看不见，链接期才炸。** 只判「编译通过 + 少解析」的话，这条会被当成
最好的候选推荐出去。

### 已知案例（存档）

`<iomanip>`：删掉后编译通过，目标文件从 21,289 字节缩到 10,183 字节——重载
解析变了。

## 4. 修掉的两个 bug（都是取证过程中挖出来的）

取证一开始对不上账：精度数据说 doctest 只有 1 条「静默改程序」，`verify` 却说
有 6 条。查下去是两个 bug。

**bug 1：`codegen.compare` 找不到同目录相对包含的头文件。**
它把两份源码 stage 到 workdir 再编，而 `#include "foo.h"` 的查找顺序是「先当前
文件所在目录」，于是头文件凭空消失、指纹拿不到。修复：把源码原目录补进
`include_dirs`。

**bug 2：`verify` 把「拿不到指纹」当成「代码变了」。**
`Fingerprint(ok=False)` 经过 `same_as()` 得到 `False`，与「指纹不同」共用同一个
分支——于是「这台机器给不出汇编」被报成「发现危险改动」。修复：任一侧 `!ok` 时
报 `status="error"` 并写明「无法证明程序未变」，`codegen_identical` 记为 `None`
（没验过就不能站上 `equivalent`）。

修完之后 `checked=True, identical=False`——指纹真拿到了，且确实不同。

## 5. 连带修正：doctest 的两个数字要改

闸门能拿到指纹之后，doctest 149 TU 的精度数据（20261020n）变了：

| | 修复前 | 修复后 |
|---|---:|---:|
| 真能删且无害（useful） | 11 | **5** |
| 静默改程序（silent_change） | 1 | **7** |
| codegen 取值 | 6 条 `unknown` | 6 条 `changed` |

**「零假阳性」是语料相关的，而且只在编译层成立。** doctest 推荐层 6 条里
**2 条（`foo.h` ×2）实际有害**——它们删了能编过、也少解析，但测试用例的静态
注册消失。Lua 上推荐层 18/18 仍然全过（汇编层也过），所以这个结论不能反过来
说成「推荐层会造假」。准确的说法是：

> 文本判据的零假阳性只在「删了还能编过」这个真值上被验证过；换成「程序等价」
> 这个更强的真值，doctest 上 6 条推荐里有 2 条不成立。

## 6. 不许越界的三条声明

1. **`verified` 的条数带抖动**（同池同协议 5 与 9）。引用时给量级，不给精确数，
   也不说「工具找到了 9 条可删」。
2. **`equivalent` 不等于「建议删除」**。它只说「删了程序不变」，收益那一半仍然
   是「不可测量」。两个语料上这一档的 `lean` 全是 `keep`。
3. **闸门自己也可能不可用**。`codegen_identical is None` 就是没验过，此时结论
   退回 `compiles_only`，不许声称等价。

## 7. 复现

```bash
python experiments/evidence_tiers.py \
    --precision-json experiments/results/candidate-precision-lua-20261020n.json \
    --project <dir> --db <compile_commands.json> --compiler clang \
    --runs 3 --confirm 2 --out experiments/results/evidence-tiers-lua-20261020.json

python experiments/gate_forensics.py \
    --precision-json experiments/results/candidate-precision-doctest-20261020n.json \
    --project <dir> --db <build/compile_commands.json> --compiler clang \
    --out experiments/results/gate-forensics-doctest-20261020.json
```

数据：`evidence-tiers-lua-20261020.json`、`evidence-tiers-doctest-20261020.json`、
`gate-forensics-{lua,doctest}-20261020.json`。
