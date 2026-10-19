# 候选精度专项（第二轮）：先把尺子修对，再改算法

上一轮（[`candidate-precision-20261019.md`](candidate-precision-20261019.md)）把
「空头承诺」从 23/60 打到 1/38。这一轮一开始是想继续压保守误判（判「在用」、
实际删得掉的那几条），结果**先发现是尺子本身错了**：基准算作「真有用」的改动里，
有 3 条其实会静默改变程序。

## 一、真值只有两条轴是不够的

原基准的两条真值：

1. 删掉这条 include 后**编不编得过**；
2. `-H` 事件数差了多少（真少解析了几个文件）。

两条都不回答「编译出来的是不是同一个程序」。把代码等价闸门（`-S` 汇编指纹，
见 [`codegen-gate-cost-20261019.md`](codegen-gate-cost-20261019.md)）接成第三条轴
（`--check-codegen`）之后，立刻抓到三类「看起来是完美优化」的改动：

| 工程 | 候选 | 编得过 | 少解析 | 汇编指纹 |
|---|---|---|---|---|
| zlib | `zutil.c -> gzguts.h` | ✓ | 15 个文件 | `aac1b5f8…` → `9b6bde46…` **变了** |
| doctest | `context.cpp -> junit.h` | ✓ | 1 | `726f855b…` → `15f120e4…` **变了** |
| doctest | `context.cpp -> xml.h` | ✓ | 1 | **变了** |

第一条的原因是被激活的条件编译分支变了（`NO_GZCOMPRESS` 不再定义，
`zlibCompileFlags()` 的返回值随之改变）；后两条更直接——`junit.h` / `xml.h`
末尾各有一句名字空间作用域的注册宏：

```cpp
DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter);
```

删掉这行 include，编译通过、少解析一个文件，**JUnit reporter 的静态注册整段消失**
（汇编里 `??__EDOCTEST_ANON_REPORTER_VAR_100` 那段不见了，用
`experiments/conditional_gate_check.py` 可以逐条复核）。

**结论：这三条在旧基准里都算「有用」，其中 doctest 的两条还排在第 1、2 位。**
按旧尺子继续优化算法，等于逼着算法去追它们。

## 二、差点改错的一处（记下来比结论重要）

顺着证据链，我先把「只出现在 `#ifdef`/`#ifndef` 里的宏」降级成「不算依赖」——
理由很顺：宏消失只是换一个分支，不会凭空产生未声明的名字。zlib 那条立刻从
「在用」变成「疑似未使用」，基准数字变好看了。

然后用代码等价闸门复核：汇编变了。**这个改动会被推荐给用户，而它悄悄改了程序。**
已回退（判定仍然是 `in_use`），改成把这件事单独记进 `UsageSignal.conditional_only`
并在报告里写明「删除不会编译失败，但会改变被编译的分支」。
回归测试 `test_macro_used_only_in_ifdef_stays_in_use` 锁住这个方向。

顺带：仓库里本来就有一条 `test_macro_guards_still_count_as_a_use` 在守这件事，
第一版改动把它跑红了——**红灯是对的**。

## 三、四处根因与修法

1. **消失集合里混进了「别的直接 include」**（`graph.find_removal_candidates`）。
   `closure_of` 按定义不含起点，所以 `shared` 必须显式把其它直接包含的头加回去。
   漏掉时，zlib `zutil.c -> gzguts.h` 把 `zutil.h`（另一条直接 include）算成会消失，
   于是两个头都定义的 `local` 被判成「消失集合唯一供给 → 在用」。
2. **源码自己提供的名字被当成依赖**（新增 `usage.definitions_in`）。
   `inffast.c` 自己定义 `inflate_fast(strm, start)`，`filters.cpp` 自己定义
   `wildcmp` / `matchesAny`（`filters`、`caseSensitive`、`wild` 是形参名）——
   旧判定看到源码里满是这些名字就判「在用」。
   判据刻意保守：定义前必须有「类型样式」的前缀，所以 `TEST_CASE(one) {`
   这种宏调用**不会**被认成定义（宏名是头文件给的）。
3. **定义晚于使用时，声明仍然是必需的**（新增 `definition_offsets` + 位置比较）。
   doctest `exception_translator.cpp` 第 14 行先用 `getExceptionTranslators()`、
   第 19 行才定义它——只看「有没有定义」会把这条真依赖判成可删（实测删掉后
   `use of undeclared identifier`）。修法：只有「首次出现的位置不早于定义位置」
   的名字才算自供给。
4. **`#define local static` 不是重命名，是宏定义**。展开目标是关键字时，
   提供的就是宏名本身；当成别名剥掉会让 `local` 从供给集合里消失
   （zutil.h 与 gzguts.h 都这么定义 `local`）。真正的重命名
   （`#define inflate_fast z_inflate_fast`）仍然要剥。

## 四、副作用型 include：一类文本判定天生看不见的依赖

新增 `usage.side_effects_in`：扫头文件在**名字空间作用域**里执行的动作
（`NAME(...)` 这种没有返回类型前缀的调用/宏展开、带初始化的非 const 对象定义）。
命中就无条件判 `in_use`，并在报告里原样写出那句语句。

注意「名字空间作用域」不等于「大括号深度 0」：`namespace doctest { ... }` 本身
带一层括号，而 doctest 的注册宏正是写在命名空间**内部**的——按深度 0 取会一条都
取不到（第一版就是这样漏的）。

## 五、对照（同一把尺子：三条真值轴）

改前 = `a85f087`（上一轮结束时的提交），改后 = 本轮工作树；两边跑**同一份**
`experiments/candidate_precision.py`，都带 `--check-codegen`。

### doctest（24 TU，38 条候选）

| 指标 | 改前 | 改后 |
|---|---:|---:|
| P@5 | 0% | **60%** |
| P@10 | 0% | **30%** |
| P@20 | 5% | 15% |
| 首条真有用（排名） | 19 | **1** |
| `plausibly_unused` 的有用率 | 0%（2 条全是静默改程序） | **100%（3/3）** |
| 被判「有用」但会静默改程序 | 2（还排在第 1、2 位） | 0（拦住并说明理由） |

### zlib（34 TU，40 条候选）

| 指标 | 改前 | 改后 |
|---|---:|---:|
| P@5 | 0% | **20%** |
| P@10 | 0% | 10% |
| 首条真有用（排名） | 40（最后一条） | **1** |
| 首条能编译（排名） | 20 | 1 |
| 静默改程序 | 1（`zutil.c -> gzguts.h`，碰巧没排到前面） | 1（明确拦住并说明理由） |

zlib 只有 1 条真有用的改动，P@10 = 10% 是它的天花板（40 条里只有 1 条真能删）。

### 真值侧的变化（两个工程合计）

| | 旧真值（2 轴） | 新真值（3 轴） |
|---|---:|---:|
| doctest 「有用」 | 6 | **4** |
| zlib 「有用」 | 2 | **1** |

减少的 3 条不是算法退步，是**这 3 条本来就不该删**。

## 六、剩余边界（如实记录）

- doctest 还剩 1 条保守误判：`context_scope.cpp -> context_scope.h` 判「在用」，
  实测能删（排在第 36 位）。区分它需要知道「删掉之后哪个声明真的没了」——
  语义层信息，文本层到此为止。
- doctest 还剩 1 条空头承诺：`context.cpp -> reporter.h`（判 in_use、编得过、
  少解析 0 个文件）。
- `side_effects_in` 只认语句形态，看不出宏展开后到底是什么；带
  `SUPPRESS/WARNING/PRAGMA` 的宏被排除（它们不改变生成的代码），
  但一个展开成 `_Pragma` 的自定义宏名仍可能被误认成副作用——方向是保守的。

## 七、复现

```bash
python experiments/candidate_precision.py \
    --project <dir> --db <compile_commands.json> --compiler <clang++> \
    --tus 24 --check-codegen \
    --out experiments/results/candidate-precision-<name>.json

# 单条候选的等价性复核（比如想看清 junit.h 到底改了什么）
python experiments/conditional_gate_check.py \
    --db <compile_commands.json> --compiler <clang++> --file <src> --line <n>
```

结果文件：`experiments/results/candidate-precision-{doctest,zlib}-20261019b.json`
（改后）与 `…-before.json`（改前）。
