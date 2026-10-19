# 上游落地战役：把实测过的优化变成真实 PR

> 这是场景落地的最后一公里。实验证据落在我们克隆的副本上是不够的——
> 这个场景的终点是**真实工程真的接受这个改动**。本目录保存两个 PR 级补丁
> （可直接 `git apply`）与英文 PR 正文，提交由维护者本人执行（见 §3）。

## 1. 状态

| # | 目标工程 | 改动 | 实测依据 | 状态 |
|---|---|---|---|---|
| 1 | doctest | 删 `doctest/parts/private/prelude.h` 的 `#include <unordered_set>`（附 assemble.py 重新生成的 `doctest.h`） | 24 TU 全部编译通过；聚合 20556 → 19937 ms（**−3.01%**），复测通过 | ✅ [doctest#1212](https://github.com/doctest/doctest/pull/1212)（2026-10-19 提交）|
| 2 | zlib | 删 `zutil.h` 第 29 行的 `#  include <stdlib.h>` | 9 个受影响 TU 全部编译通过；3314 → 3244 ms（**−2.125%**），复测通过 | ✅ [zlib#1344](https://github.com/madler/zlib/pull/1344)（2026-10-19 提交，base=develop）|
| 3 | nlohmann/json | 测试目标加 PCH 治理提案（非 include 删除类改动，涉构建系统，见 §5） | 完整构建墙上 **−7.1%** / 编译 CPU **−11.4%**，63 套件 **4,264,414 条断言逐个一致** | 📄 提案文档 |

补丁在 2026-10-19 对当天 fork 的上游 master/develop `git apply --check` 干净套用，
分支已推送（`wzzrambler/doctest@drop-unused-unordered-set`、`wzzrambler/zlib@drop-unused-stdlib`）。

原始数据：`experiments/results/g2-confirm-picks-{doctest,zlib}-20261018.json`、
`experiments/results/experiment-20261017.md`。

## 2. 两个补丁的自洽性检查（提交前已做）

- 均基于 **2026-10-19 拉取的上游 HEAD**（doctest `master`、zlib `develop` Codeload
  源码包）生成，并已对**全新解包的同一源码树**做过 `patch -p1 --dry-run`，干净套用；
- doctest 的 `doctest.h` 由官方 `scripts/assemble.py` 重新生成，且生成器在当前树上
  已验证**幂等**（改动前重跑与 HEAD 一致）才做改动；
- 各自的「为什么这个 include 未被使用」都重新人工核过（不是只信工具）：
  doctest 侧唯一用无序容器的测试自己 include 了这两个头；zlib 侧唯一真用 `malloc`
  的 `zutil.c` 自己 include 了 `<stdlib.h>`，库内分配走 `zcalloc`/`zcfree`。

## 3. 如何提交（需要维护者本人账号）

```bash
# 以 doctest 为例（zlib 同理，改仓库与补丁名）
gh repo fork doctest/doctest --remote
gh repo sync doctest
git checkout -b drop-unused-unordered-set
git apply contrib/upstream/patches/doctest-drop-unused-unordered_set.patch
# doctest 需要跑生成器确认；zlib 直接构建：
cd doctest && python scripts/assemble.py && git diff --stat   # 应只有两个文件
# 本地构建 + 跑一遍测试（PR 正文只承诺编译与耗时，测试由你的环境确认）
gh pr create --repo doctest/doctest --title "Remove unused #include <unordered_set> from prelude" \
    --body-file contrib/upstream/pr-bodies/doctest.md
```

**不要跳过本地验证**：补丁在生成时就已验证过可套用与可编译，但 PR 应带着
「我在本机跑过」的证据，而不是只贴补丁。

## 4. 诚实的边界（写进每条 PR 正文）

- 验证覆盖的是**编译与构建耗时**：doctest 的 24 个编译单元 / zlib 的 9 个受影响单元；
  未覆盖下游使用者（依赖传递包含 `<unordered_set>` 的用户需要自己 include）与其他
  平台工具链；
- 测量协议：每侧 min-of-3、B/P/B/P 交替、TU 级噪声线 2% / 聚合 1%、improved 强制
  复测——协议细节与负面结果见
  [`../../docs/benchmark-report.md`](../../docs/benchmark-report.md) §9；
- 本机的测量不能外推成「所有平台都快这么多」，PR 正文给的是本机可复现的数字。

## 5. 第三个候选：nlohmann/json 的 PCH 提案

doctest / zlib 是「删一行」类改动；json 的 63 TU 实验是「该往哪使劲」类
（−7.1% 墙上 / −11.4% CPU，断言逐个一致）。它改动的是上游构建系统
（`tests/CMakeLists.txt` 一侧的 PCH 配置），需要维护者接受构建约定，
且实测有一条通用约束：**在 include 之前改配置宏的 16/63 个单元不能用 PCH**
（否则改变头文件语义，被断言门禁抓到过）。因此它先以提案形式保存：
`experiments/results/experiment-20261017.md`（含逐单元对照与失败单元清单），
待前两条 PR 有回音后再决定是否推进。

## 6. 无论结果的战役价值

三个工程的可采纳率是 1/16、1/16、0/16（[`../../docs/benchmark-report.md`](../../docs/benchmark-report.md) §9）——
「自动优化」不成立，这一点在任何对外材料里都不翻案。战役的价值不在命中率，
在于把「可验证」三个字落到**别人看得见的地方**：一个被合并的 PR 是外部验证，
一个有依据的拒绝（zlib 维护者历来保守）同样是可写进复盘的证据。结果无论
合并与否，都更新本状态表与 [`../../SUBMISSION.md`](../../SUBMISSION.md)。
