# HeavyHeader 构建成本报告

- 项目根目录：`C:\Users\31879\AppData\Local\Temp\upstream\lua-5.4.7`
- 编译器：`C:/Program Files/LLVM/bin/clang.exe`（clang）
- 编译单元：34 成功 / 0 失败
- 头文件依赖边：2,504
- 全项目头文件解析量：**1,090,930 行**
- 唯一文件行数：342,901 行
- **Blowup Factor：3.2×**（平均每行被解析的次数；与 Header Hero 同定义，见 `docs/competitors.md`）
- 累计编译耗时：22.49 s

## 成本热点 Top 15

按「归因行数」排序，即 `头文件行数 × 引用它的编译单元数`——
它衡量的是这个头文件让整个项目多解析了多少行代码。

| 头文件 | 行数 | 被引用 TU | 归因行数 | 传递闭包 | 最大深度 |
|---|---:|---:|---:|---:|---:|
| `src\luaconf.h` | 802 | 34 | 27,268 | 10 | 3 |
| `src\lua.h` | 523 | 34 | 17,782 | 18 | 2 |
| `src\lobject.h` | 813 | 19 | 15,447 | 7 | 2 |
| `src\llimits.h` | 380 | 21 | 7,980 | 8 | 3 |
| `src\lstate.h` | 408 | 19 | 7,752 | 11 | 1 |
| `src\lauxlib.h` | 301 | 14 | 4,214 | 11 | 0 |
| `src\lgc.h` | 202 | 16 | 3,232 | 0 | 1 |
| `src\lopcodes.h` | 405 | 7 | 2,835 | 0 | 1 |
| `src\ltm.h` | 103 | 19 | 1,957 | 0 | 2 |
| `src\lmem.h` | 93 | 19 | 1,767 | 8 | 3 |
| `src\lprefix.h` | 45 | 34 | 1,530 | 0 | 0 |
| `src\ldo.h` | 87 | 16 | 1,392 | 0 | 0 |
| `src\lzio.h` | 66 | 19 | 1,254 | 0 | 2 |
| `src\lvm.h` | 141 | 8 | 1,128 | 0 | 0 |
| `src\ldebug.h` | 64 | 17 | 1,088 | 22 | 0 |

## 实验性：增量构建税（未校准，勿据此决策）

上一节回答「整个项目为它付了多少解析量」；这一节试答「**你改它一行，下一次增量构建要等多久**」。这是编辑循环里的实际痛感，也是现有竞品没有给出的视角——但它目前**只是原型**：受控实验（`experiments/incremental_tax.py`）显示预测比实测**偏高约 1.7×**，因为扫描的 `--full-compile` 会为每个单元额外链接一次，而真实增量构建只编译不链接。校准完成前，本表不得作为结论引用。

| 头文件 | 重编 TU | 预计等待 | 占单次全量构建 |
|---|---:|---:|---:|
| `src\luaconf.h` | 34 | 22.49 s | 100% |
| `src\lua.h` | 34 | 22.49 s | 100% |
| `src\lprefix.h` | 34 | 22.49 s | 100% |
| `src\llimits.h` | 21 | 13.51 s | 60% |
| `src\lobject.h` | 19 | 12.20 s | 54% |
| `src\lstate.h` | 19 | 12.20 s | 54% |
| `src\ltm.h` | 19 | 12.20 s | 54% |
| `src\lmem.h` | 19 | 12.20 s | 54% |
| `src\lzio.h` | 19 | 12.20 s | 54% |
| `src\ldebug.h` | 17 | 11.01 s | 49% |

> 口径：扫描期每个 TU 实测耗时之和（默认 `-fsyntax-only`，不含链接）。它是一个**预测**，预测误差与验证方法见 `docs/methodology.md` 的「增量构建税」一节。`0` 表示该单元没有可用计时，不是「瞬间完成」。

## 成本候选（按预计收益排序）

> **这张表说的是「贵」，不是「能删」。** 「独占闭包」指删掉这条 include 后，只有它会停止被解析的头文件集合——如果某个头文件还能从别的 include 到达，删掉当前这条并不省钱。这是本表与普通 lint 工具的区别。
>
> 「结论」列是明确判断（建议删除/保留/需人工判断），依据见下一节；「未实测」表示这一轮没有跑 `--probe`，此时不给结论——没测过就下判断是编造。

| 编译单元 | include | 独占闭包 | 预计省行数 | 预计占比 | 使用状态 | 判据 | 结论 |
|---|---|---:|---:|---:|---|---|---|
| `src\llex.c` | `ucrt\locale.h` | 1 | 184 | 1.4% | 疑似未使用 | — | 未实测 |
| `src\llex.c` | `src\lparser.h` | 1 | 171 | 1.3% | 疑似未使用 | firstchar 由本文件自己定义 | 未实测 |
| `src\ltm.c` | `src\lvm.h` | 1 | 141 | 1.1% | 疑似未使用 | v2 由本文件自己定义 | 未实测 |
| `src\ldo.c` | `src\ltable.h` | 1 | 63 | 0.4% | 疑似未使用 | — | 未实测 |
| `src\lgc.c` | `src\ldebug.h` | 1 | 64 | 0.4% | 疑似未使用 | ci 另有供给者 | 未实测 |
| `src\lcode.c` | `src\lstring.h` | 1 | 57 | 0.4% | 疑似未使用 | extra 由本文件自己定义 | 未实测 |
| `src\lbaselib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_base 由本文件自己定义 | 未实测 |
| `src\lcorolib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_coroutine 由本文件自己定义 | 未实测 |
| `src\ldblib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_debug 由本文件自己定义 | 未实测 |
| `src\liolib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_io 由本文件自己定义 | 未实测 |
| `src\lparser.c` | `src\ldebug.h` | 1 | 64 | 0.5% | 疑似未使用 | msg、what 由本文件自己定义 | 未实测 |
| `src\lstring.c` | `src\ldebug.h` | 1 | 64 | 0.5% | 疑似未使用 | — | 未实测 |
| `src\lmathlib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_math 由本文件自己定义 | 未实测 |
| `src\loslib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_os 由本文件自己定义 | 未实测 |
| `src\lstrlib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_string 由本文件自己定义 | 未实测 |
| `src\ltablib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_table 由本文件自己定义 | 未实测 |
| `src\lundump.c` | `src\ldebug.h` | 1 | 64 | 0.5% | 疑似未使用 | msg 由本文件自己定义 | 未实测 |
| `src\lutf8lib.c` | `src\lualib.h` | 1 | 52 | 0.3% | 疑似未使用 | luaopen_utf8 由本文件自己定义 | 未实测 |
| `src\loadlib.c` | `um\windows.h` | 139 | 293,142 | 94.3% | 无法判定 | init、len 由本文件自己定义 | 未实测 |
| `src\lua.c` | `um\windows.h` | 139 | 293,142 | 94.0% | 无法判定 | NULL 只在幸存头文件的条件编译区里声明，本次编译看不到它——删不删由编译验证裁定 | 未实测 |


### 待验证队列：222 条等 `verify` 裁定

顺序即 `heavyheader verify --top N` 的测量顺序：**疑似未使用**先测
（静态判定已经指向删除），**无法判定**次之（静态给不出结论，但反事实
实验能裁——Lua 语料上这一层 32/32 条实测可删），**在用**默认不测
（要测得加 `--ungated-sites`）。

- 疑似未使用 **18** 条 · 无法判定 **63** 条 · 在用 **141** 条

| 编译单元 | include | 预计省 (s) | 使用状态 | 判据 |
|---|---|---:|---|---|
| `src\llex.c` | `ucrt\locale.h` | 0.0015 | 疑似未使用 | — |
| `src\llex.c` | `src\lparser.h` | 0.0013 | 疑似未使用 | firstchar 由本文件自己定义 |
| `src\ltm.c` | `src\lvm.h` | 0.0006 | 疑似未使用 | v2 由本文件自己定义 |
| `src\ldo.c` | `src\ltable.h` | 0.0005 | 疑似未使用 | — |
| `src\lgc.c` | `src\ldebug.h` | 0.0005 | 疑似未使用 | ci 另有供给者 |
| `src\lcode.c` | `src\lstring.h` | 0.0004 | 疑似未使用 | extra 由本文件自己定义 |
| `src\lbaselib.c` | `src\lualib.h` | 0.0003 | 疑似未使用 | luaopen_base 由本文件自己定义 |
| `src\lcorolib.c` | `src\lualib.h` | 0.0003 | 疑似未使用 | luaopen_coroutine 由本文件自己定义 |
| `src\ldblib.c` | `src\lualib.h` | 0.0003 | 疑似未使用 | luaopen_debug 由本文件自己定义 |
| `src\liolib.c` | `src\lualib.h` | 0.0003 | 疑似未使用 | luaopen_io 由本文件自己定义 |

## 实测状态

**本轮没有做实测。** 上表全部是估算——按行数折算的预计收益，不是秒数，也不代表这些 include 删掉之后还能编译。

两个档位可选：`--probe-compile` 只验「删了还能不能编」（每候选一次编译）；`--probe` 在它之上再测耗时（两侧各 N 次取最小，判 improved 后还要复测）。

## 缺少 include guard 的头文件（必赚项）

这些头文件在**同一个编译单元内被重复解析**。对带 guard 的头文件来说这在物理上不可能发生，所以这是「缺少 guard」的确定性信号。补上 `#pragma once` 或 guard 宏永远安全，且收益立竿见影。

| 头文件 | 解析次数 | 涉及 TU | 多解析次数 |
|---|---:|---:|---:|
| `include\__stddef_offsetof.h` | 147 | 34 | 113 |
| `include\__stddef_ptrdiff_t.h` | 147 | 34 | 113 |
| `include\__stddef_size_t.h` | 147 | 34 | 113 |
| `include\__stddef_wchar_t.h` | 147 | 34 | 113 |
| `include\stddef.h` | 147 | 34 | 113 |
| `shared\poppack.h` | 66 | 2 | 64 |
| `include\stdarg.h` | 61 | 34 | 27 |
| `shared\pshpack1.h` | 24 | 2 | 22 |
| `shared\pshpack8.h` | 20 | 2 | 18 |
| `um\mmsyscom.h` | 16 | 2 | 14 |
| `shared\pshpack2.h` | 12 | 2 | 10 |
| `shared\pshpack4.h` | 10 | 2 | 8 |
| `shared\guiddef.h` | 6 | 2 | 4 |
| `um\WinNls.h` | 4 | 2 | 2 |
| `um\prsht.h` | 4 | 2 | 2 |

---

由 [HeavyHeader](https://github.com/wzzrambler/heavyheader) 生成 · 指标定义见 `docs/methodology.md`
