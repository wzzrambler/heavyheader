"""Usage heuristic: is a header plausibly still needed?

The cost model in :mod:`heavyheader.attrib.graph` answers *how much* a header
costs.  It says nothing about whether the code still needs it, and a report
that conflates the two is worse than no report: the first person who follows
its advice and breaks the build will never trust the tool again.

This module adds the missing half with a deliberately cheap and deliberately
honest test:

    If the translation unit's source mentions none of the identifiers the
    header declares, the header is *plausibly* unused.

Why it is only a heuristic, stated plainly:

* It is textual, not semantic.  A header pulled in purely for a side effect
  (``<iostream``> for stream initialisation, a header that only defines
  macros used by another header) declares identifiers nobody writes down.
* It cannot see through aliases, ``using`` declarations, ADL, or a type that
  reaches the source through a template argument.
* A header whose identifiers all happen to be common English words will look
  "used" when it is not.

So the result is reported as ``plausibly_unused`` / ``in_use`` /
``unknown`` -- never as "safe to delete".  The authoritative answer comes
from :mod:`heavyheader.verify`, which actually removes the include and
recompiles.  That is the whole point of having both.

Scope: what "this include" means
--------------------------------
The first version of this module compared the source against the *candidate
header's own text* only.  That is the wrong unit of analysis, and the
controlled experiment (``experiments/control.py``) caught it: the tool
proposed removing ``doctest_compatibility.h`` from a test unit that uses
``TEST_CASE`` dozens of times, because ``TEST_CASE`` is declared in
``doctest.h`` -- one level down, inside the closure that disappears with the
include.  Five out of five suggestions failed to compile, all for this
reason.

What a deletion actually removes is the candidate's *exclusive closure*: the
headers that become unreachable once this ``#include`` is gone (see
:func:`heavyheader.attrib.graph.find_removal_candidates`).  So the question is
not "does the source name something this file declares" but "does the source
name something **anything in the disappearing set** declares".  Callers pass
that set in as ``closure``; the verdict is the answer for the whole set, and
``provided_by`` says which file actually owns the matched symbol so the
report can point at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ..includes import strip_comments

__all__ = [
    "UsageVerdict",
    "UsageSignal",
    "IdentifierIndex",
    "analyse_usage",
    "identifiers_in",
    "definitions_in",
    "conditional_mentions",
    "side_effects_in",
    "side_effect_kinds_in",
]


class UsageVerdict(Enum):
    PLAUSIBLY_UNUSED = "plausibly_unused"
    IN_USE = "in_use"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class UsageSignal:
    """What the textual analysis concluded about one include."""

    verdict: UsageVerdict
    shared_identifiers: tuple[str, ...] = ()
    header_identifier_count: int = 0
    #: The file inside the disappearing set that declares the matched symbol.
    #: ``None`` when nothing matched (or when the caller passed no closure).
    #: Reported verbatim: "``TEST_CASE`` 由 ``doctest.h`` 提供" is actionable,
    #: "这个头在用" is not.
    provided_by: Path | None = None
    #: 命中了、但因为在消失集合里声明得过于普遍而**不能**当作使用证据的
    #: 标识符。留着它是为了把「为什么判不了」说清楚，而不是扔一个
    #: 「无法判定」让人猜。
    ambient_identifiers: tuple[str, ...] = ()
    #: 命中了、但**在保留下来的头文件里也有供给**的标识符。这类命中不构成
    #: 「删了就会缺」的证据：删掉这条 include 之后，符号还能从别处拿到。
    #: 实测（doctest，60 条候选）里旧的判定把 60/60 全算「正在使用」，而真值
    #: 有 48% 能编译通过——差别全在这一栏。
    cross_provided: tuple[str, ...] = ()
    #: 命中了、但源码**自己就提供**的标识符（本文件定义的函数、宏、类型、
    #: 以及这些函数的形参名）。这类命中不构成依赖：删掉头文件之后，名字是
    #: 源码自己给出的，不是头文件给的。实测（zlib ``inffast.c -> inffast.h``）：
    #: ``inflate_fast`` 与形参 ``start`` 都定义在本文件里，旧判定据此判「在用」，
    #: 真值是删得掉。
    self_provided: tuple[str, ...] = ()
    #: 命中了、但源码只在 ``#ifdef`` / ``#ifndef`` / ``#if defined(...)``
    #: 这类**存在性判断**里提到它。这类命中也不构成硬依赖：宏消失只会换一个
    #: 分支被编译，不会凭空产生一个未声明的名字。实测（zlib
    #: ``zutil.c -> gzguts.h``）：``NO_GZCOMPRESS`` 只出现在 ``#ifdef`` 里，
    #: 删掉之后该分支被跳过，编译照样通过。
    conditional_only: tuple[str, ...] = ()
    #: 消失集合里的头文件在**名字空间作用域**执行的动作（不是声明）。
    #: 这类 include 哪怕源码一个名字都没提到也不能删：它靠副作用起作用。
    #: 实测（doctest ``context.cpp -> junit.h``）：``junit.h`` 末尾有一句
    #: ``DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter);``，删掉之后
    #: 编译通过、少解析一个文件，但 JUnit reporter 的静态注册整个消失——
    #: 汇编里 ``??__EDOCTEST_ANON_REPORTER_VAR_100`` 那段不见了。
    side_effects: tuple[str, ...] = ()
    #: 消失集合里**疑似**靠执行起作用的语句：一句宏调用，文本层看不出它展开成
    #: 声明还是展开成带动态初始化的对象。这类候选不给「可删」结论、也不直接
    #: 判「在用」，而是进「无法判定」队列，由 verify 的汇编等价闸门裁定——
    #: 证据从「文本猜的」换成「编译器说的」，这是唯一不靠白名单的升级路径。
    suspected_side_effects: tuple[str, ...] = ()
    #: 判定没有可复述的证据时（例如头文件只提供宏、或读不到）用一句话说明
    #: 为什么判不了。空字符串 = 由其他字段推导理由，见 ``_usage_reason``。
    note: str = ""
    #: ``(宏名, 体里用到的名字)``：源码用的是幸存头里的宏，这个名字只在宏体里
    #: 出现。理由要能写成「删了它，``lua_getlocaledecpoint`` 展开出来的
    #: ``localeconv`` 就没了」，否则读者只看到「在用」却找不到依据。
    macro_expansion: tuple[str, str] = ()
    #: 消失集合提供的名字里，有可能被 ``##`` **拼出来**的那几个。
    #: 源码写的是 ``MANT_DIG``，头文件提供的是 ``DBL_MANT_DIG``，中间靠
    #: ``#define l_floatatt(n) (DBL_##n)`` 接上——两边文本里都不存在
    #: ``DBL_MANT_DIG`` 这个词，只有编译器拼得出来。
    pasted: tuple[str, ...] = ()
    #: 命中了、但**可达集里没有任何头**以任何形态（声明、定义、别名）提供
    #: 的名字。它们来自编译命令行（``-DDOCTEST_CONFIG_DISABLE``）或源码之外
    #: 的世界——删除任何一条 include 都带不走它们，所以不构成保留证据。
    #: doctest 实测：``handler.cpp`` 的候选此前全靠这种幻影命中判「在用」，
    #: 真值是 4 条全可删。
    unprovided: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return {
            UsageVerdict.PLAUSIBLY_UNUSED: "疑似未使用",
            UsageVerdict.IN_USE: "正在使用",
            UsageVerdict.UNKNOWN: "无法判定",
        }[self.verdict]


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Words that carry no information about whether a header is used: language
# keywords, preprocessor noise, and the handful of tokens that appear in
# essentially every header.
_IGNORED = frozenset(
    """
    alignas alignof and and_eq asm auto bitand bitor bool break case catch char
    char8_t char16_t char32_t class compl concept const consteval constexpr
    constinit const_cast continue co_await co_return co_yield decltype default
    delete do double dynamic_cast else enum explicit export extern false float
    for friend goto if inline int long mutable namespace new noexcept not
    not_eq nullptr operator or or_eq private protected public register
    reinterpret_cast requires return short signed sizeof static static_assert
    static_cast struct switch template this thread_local throw true try
    typedef typeid typename union unsigned using virtual void volatile
    wchar_t while xor xor_eq
    define defined elif else endif error ifdef ifndef include line pragma
    undef warning once
    std size_t ptrdiff_t nullptr_t
    """.split()
)

#: 「重命名」而不是「声明」：``#define inflate_fast z_inflate_fast`` 只是给符号
#: 起了个别名，真正提供声明的仍然是 ``inffast.h``。实测（zlib）：删掉
#: ``inffast.h`` 编译失败，但 ``zconf.h`` 里这行重命名让「另有供给者」成立，
#: 于是把一条真依赖判成了「可删」。``#pragma map(inflate_fast, ...)`` 同理。
_RENAME_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*$")
_RENAME_PRAGMA = re.compile(r"^\s*#\s*pragma\s+map\s*\(\s*([A-Za-z_]\w*)")

#: 展开目标是这些词时，这一行**不是**重命名——它是在定义一个真正的宏。
#:
#: ``#define local static`` / ``#define FAR far`` 这类宏提供的就是宏名本身：
#: 源码里写的 ``local`` 完全由它给出，别的头有没有声明 ``static`` 根本无关。
#: 把宏名从供给集合里剥掉，等于说「这个头不提供 local」，于是唯一证据消失，
#: 一条真依赖被判成可删（zlib ``zutil.c -> gzguts.h``：两个头都定义 ``local``，
#: 剥掉之后它反而成了「唯一供给者」）。
#:
#: 对照真正的重命名 ``#define inflate_fast z_inflate_fast``：目标是**另一个
#: 标识符**，别名不能证明目标在删除后还有声明——那种仍然要剥。
_RENAME_TARGET_IS_A_MACRO = frozenset(
    """
    static extern inline const volatile register auto restrict
    far near __inline __forceinline __stdcall __cdecl
    """.split()
)

# 编译器内建：这些标识符**永远不由某个头文件提供**，而是编译器自己预定义，
# 或者是语言扩展关键字。把它们算作「使用证据」会造成系统性假阴性。
#
# 实测来源：zlib 的 ``zutil.h -> <stdlib.h>`` 被判「正在使用」，证据是
# ``_MSC_VER``、``__STDC_VERSION__``、``__attribute__``（由 ``malloc.h``
# 提供）——这三个都不可能由 ``<stdlib.h>`` 被删除而消失：前两个是编译器
# 预定义宏，第三个是 GCC 扩展关键字。闸门因此拦下了一条**经复测确认省时
# 2.125%** 的改动（`experiments/results/g2-confirm-picks-zlib-20261018.json`）。
#
# 判据是「删掉包含它的头之后，这个标识符还会不会存在」。编译器预定义的一定
# 还在，所以不能算依赖。
_BUILTIN = frozenset(
    """
    __GNUC__ __GNUC_MINOR__ __GNUC_PATCHLEVEL__ __clang__ __clang_major__
    _MSC_VER _MSC_FULL_VER __STDC__ __STDC_VERSION__ __STDC_HOSTED__
    __cplusplus __OBJC__ __ASSEMBLER__
    __attribute__ __attribute __declspec __extension__ __inline __restrict
    __asm__ __volatile__ __forceinline __thread __typeof__ typeof
    """.split()
)


def identifiers_in(text: str) -> set[str]:
    """Extract meaningful identifiers from C++ source text."""
    found: set[str] = set()
    for match in _IDENTIFIER.finditer(text):
        word = match.group(0)
        if word in _IGNORED or word in _BUILTIN or len(word) < 2:
            continue
        # ALL_CAPS tokens are almost always macros; keep them but they are
        # noisy, so cap how many we collect.
        found.add(word)
    return found


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


_INCLUDE_DIRECTIVE = re.compile(r"\s*#\s*include\b")


def _strip_noise(text: str) -> str:
    """Blank out comments, string/char literals and ``#include`` lines.

    Preserving the line count matters because callers match verdicts and
    reports back to line numbers.

    Why ``#include`` lines are blanked and not just their quotes: the file
    name in an angle-bracket include is a bare word, and the header it points
    at very often declares an identifier with that same name -- ``<regex>``
    declares ``std::regex``, so the text of ``#include <regex>`` contributes
    the identifier ``regex`` to the *including* file.  The usage test then
    finds a match it created itself and reports "in use" for a header nobody
    uses.  That is not a theoretical concern: it is why the injected-include
    recall experiment (``experiments/g2_recall.py``) scored 0/1 on header
    sites until this was fixed.

    Other preprocessor lines are deliberately **kept**: a header pulled in
    only for a macro that guards an ``#ifdef`` is a real dependency, and
    blanking those lines would turn it into a confident "unused".
    """
    out: list[str] = []
    in_block = False
    for line in text.splitlines():
        if not in_block and _INCLUDE_DIRECTIVE.match(line):
            out.append(" " * len(line))
            continue
        result: list[str] = []
        index = 0
        while index < len(line):
            if in_block:
                end = line.find("*/", index)
                if end == -1:
                    result.append(" " * (len(line) - index))
                    index = len(line)
                else:
                    result.append(" " * (end + 2 - index))
                    index = end + 2
                    in_block = False
                continue
            pair = line[index : index + 2]
            if pair == "/*":
                in_block = True
                result.append("  ")
                index += 2
                continue
            if pair == "//":
                result.append(" " * (len(line) - index))
                index = len(line)
                continue
            char = line[index]
            if char in "\"'":
                closing = line.find(char, index + 1)
                if closing == -1:
                    result.append(" " * (len(line) - index))
                    index = len(line)
                else:
                    result.append(" " * (closing + 1 - index))
                    index = closing + 1
                continue
            result.append(char)
            index += 1
        out.append("".join(result))
    return "\n".join(out) + "\n"


def _declares_nothing(text: str) -> bool:
    """True when a header contains no declarations of its own.

    A header made up purely of ``#include`` directives, ``#define`` macros and
    comments is an *aggregation* header: it exists to hand other headers to
    whoever includes it.  Its "identifiers" are the names of the things it
    forwards, so the usage test would report it as plausibly unused whenever
    the direct includer happens not to name them -- a false positive on one of
    the most common header patterns in large C++ codebases.
    """
    stripped = _strip_noise(text)
    for line in stripped.splitlines():
        code = line.strip()
        if not code:
            continue
        if code.startswith("#"):
            continue
        return False
    return True


# --------------------------------------------------------------------------
# 源码自己提供了哪些名字 / 哪些名字只是被问了一句「在不在」
# --------------------------------------------------------------------------
#
# 前两轮修的都是「供给方是谁」（另有供给者、重命名宏），这一轮修的是另一半：
# 命中本身成不成立。命中只说明「源码里出现了这个名字」，但出现有两种截然
# 不同的方式——源码**定义**它，或者源码**需要**它。只有后者才是依赖。
#
# 实测（zlib / doctest 两个工程共 6 条「判在用、实际删得掉」的保守误判）里，
# 全部 6 条都属于下面两类之一，没有例外。
#
# ``_strip_noise`` 只保留代码本体：注释与字符串被抹成空格，``#include`` 行整行
# 抹掉（否则 ``#include <regex>`` 自己会贡献 ``regex`` 这个命中）。
_CONDITIONAL_DIRECTIVE = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b")

_DEFINE_NAME = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)")
_TAG_NAME = re.compile(r"^\s*(?:class|struct|union|enum)\s+([A-Za-z_]\w*)")
_NAMESPACE_NAME = re.compile(r"^\s*namespace\s+([A-Za-z_]\w*)")
_USING_ALIAS = re.compile(r"^\s*using\s+([A-Za-z_]\w*)\s*=")
_TYPEDEF_NAME = re.compile(r"^\s*typedef\b[^;=]*?\b([A-Za-z_]\w*)\s*;")
_BRACE_ALIAS = re.compile(r"^\s*\}\s*([A-Za-z_]\w*)\s*;")
_ENUM_BODY = re.compile(r"\benum\b[^{;=]*\{([^}]*)\}", re.S)

#: ``名字 (`` —— 可能是函数定义、函数声明，也可能只是一次调用。
_CALL_OR_DEF = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
#: 定义/声明之前只允许出现「类型样式」的记号。这个限制是关键：``cond ? f(x) : g(y)``
#: 里的 ``f(`` 前面有 ``?``，``p->m(`` 前面有 ``.``，都会被排除掉——把它们当成
#: 定义会把真实的依赖误杀掉（方向错的那一边）。
_TYPEISH_PREFIX = re.compile(r"^[A-Za-z_:<>~&*\s,\[\]]*$")
#: 函数体 ``{``、构造函数初始化列表 ``:``、以及 ``const`` / ``noexcept`` / ``->`` /
#: ``= 0`` / ``= delete`` 这些声明尾巴。
_DEF_TAIL = re.compile(
    r"\s*(?:\{|const\b|noexcept\b|override\b|final\b|requires\b|throw\b|volatile\b|->|=)"
)

#: 语句开头是这些词时，紧跟其后的标识符不是「类型 + 变量名」，不能算定义。
#: ``return g_cs;`` 是最典型的反例：漏掉这张表会把一次「使用」误判成「定义」，
#: 于是把真依赖放过了——那正是我们要修的反方向错误。
_NOT_A_TYPE = frozenset(
    """
    return if while for switch case else do goto break continue sizeof
    new delete throw catch using namespace typedef template typename class
    struct enum union operator public private protected try default alignas
    alignof static_assert constexpr consteval constinit decltype noexcept
    static const inline extern register volatile mutable thread_local auto
    and or not xor bitand bitor compl typeid dynamic_cast static_cast
    const_cast reinterpret_cast co_return co_await co_yield this nullptr
    true false
    """.split()
)

_OBJECT_DECL = re.compile(
    r"(?:^|[;{}])[\s;{}]*"
    r"(?:(?:const|volatile|static|extern|register|inline|constexpr|constinit"
    r"|thread_local|mutable|unsigned|signed|struct|enum|union"
    # 宏名当类型限定符：``local const ct_data static_ltree[] = …``（zlib
    # trees.h）、``DOCTEST_THREAD_LOCAL std::vector<…> g_cs;``（doctest）。
    # 不认这一形态，trees.h / crc32.h 里整批表定义就没有声明者，四条真依赖
    # 被判成「另有供给 → 疑似未使用」。正则靠回溯保证最后一个标识符落在
    # 声明名的位置；``return y;`` 这类语句由 _NOT_A_TYPE 拦住。
    r"|[A-Za-z_]\w*)\s+)*"
    r"([A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*(?:\s*<[^;{}()]*>)?)"
    r"[ \t\*&]+"
    r"([A-Za-z_]\w*)"
    r"[ \t]*(?:\[[^\]]*\])*"
    r"[ \t]*(?:=|;|,|\(|\{|\))",
    re.M,
)


def _matching_paren(text: str, start: int) -> int:
    """``start`` 处是 ``(``，返回与之配对的 ``)`` 的下标；找不到返回 -1。"""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_params(text: str) -> list[str]:
    """按顶层逗号切分形参表——嵌套的括号与模板尖括号不切。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    angle = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "<":
            angle += 1
        elif char == ">":
            angle -= 1
        if char == "," and depth == 0 and angle == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    parts.append("".join(buf))
    return parts


_PASTE_PREFIX = re.compile(r"([A-Za-z_]\w*)\s*##")
_PASTE_SUFFIX = re.compile(r"##\s*([A-Za-z_]\w*)")
_DEFINE_HEAD = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)\s*(\(([^)]*)\))?")
_CONDITIONAL_IFDEF = re.compile(r"^\s*#\s*ifdef\s+([A-Za-z_]\w*)")
_CONDITIONAL_IFNDEF = re.compile(r"^\s*#\s*ifndef\s+([A-Za-z_]\w*)")
_COMPLEX_IF = re.compile(r"^\s*#\s*if\b")
_CONDITIONAL_END = re.compile(r"^\s*#\s*(?:endif|else|elif)\b")
_CONDITIONAL_ELSE = re.compile(r"^\s*#\s*(?:else|elif)\b")


def _blocking_conditional_regions(
    text: str, defined: frozenset[str], *, unverifiable: bool = True
) -> list[tuple[int, int]]:
    """**不能当作自供给**的条件区间（字符下标，半开）。

    两类区间进不了自供给：

    * **判死了的**：``#ifdef X``（X 不在命令行 ``-D`` 里）与 ``#ifndef X``
      （X 在 ``-D`` 里）。分支根本不编译，那里的定义不存在——名字实际来自
      头文件（zlib ``crc32.c`` 的 ``#ifdef DYNAMIC_CRC_TABLE`` 分支里定义了
      ``x2n_table``，本构建该分支没有被编译；按自供给算，真依赖凭空消失，
      判成可删）。
    * **判不了死活的**：复杂的 ``#if`` 表达式（``defined(A) || !defined(B)``
      的 B 往往由包含链里的头定义，命令行判不了）。zlib ``trees.c`` 的
      ``static_ltree`` 声明就藏在 ``#if defined(GEN_TREES_H) || !defined(STDC)``
      里——按自供给算同样漏掉真依赖。方向保守：这些名字保留为证据；若
      幸存头确实也声明了它们，删除依然安全（走「另有供给」）。

    ``#else`` 翻转当前半段的活死并分开记录。头文件侧**不要**用这个函数：
    头文件整个被 include guard 包着，全判死就什么声明都不剩了。

    ``unverifiable=False`` 时只返回**判死了**的区间（``#if`` 表达式求不了值的
    不算）。用在「源码里这个标识符算不算被用到」上：``#ifdef ZSTD_MULTITHREAD``
    没定义时里面的引用确实不存在，但 ``#if defined(__cplusplus)`` 这种求不了值
    的不能当死区——``__cplusplus`` 由编译器给，命令行里永远看不到它。
    """
    regions: list[tuple[int, int]] = []
    # 每层：[当前半段起点, 当前半段是否死, 祖先是否死, 守卫可求值?]
    stack: list[list] = []
    offset = 0

    def close_half(upto: int) -> None:
        if not stack:
            return
        entry = stack[-1]
        # 判死、或守卫根本求不了值（复杂 #if）→ 这半段的定义不可信。
        if entry[1] or (unverifiable and not entry[3]):
            regions.append((entry[0], upto))
        entry[0] = upto

    for line in text.splitlines(keepends=True):
        ifdef = _CONDITIONAL_IFDEF.match(line)
        ifndef = _CONDITIONAL_IFNDEF.match(line)
        inherited = bool(stack) and (stack[-1][1] or stack[-1][2])
        if ifdef:
            stack.append([offset, ifdef.group(1) not in defined, inherited, True])
        elif ifndef:
            stack.append([offset, ifndef.group(1) in defined, inherited, True])
        elif _COMPLEX_IF.match(line):
            stack.append([offset, False, inherited, False])
        elif _CONDITIONAL_ELSE.match(line) and stack:
            # 当前半段到此为止；#else 后的新半段翻转活死（#elif 判不了，按活）。
            close_half(offset)
            entry = stack[-1]
            entry[1] = (not entry[1]) if entry[3] else False
        elif _CONDITIONAL_END.match(line) and stack:
            close_half(offset)
            stack.pop()
        offset += len(line)
    for entry in stack:  # 未闭合的条件块算到文件尾
        if entry[1] or entry[2] or (unverifiable and not entry[3]):
            regions.append((entry[0], offset))
    return regions


def _macro_params(line: str) -> set[str]:
    """``#define M(a, b) ...`` 的形参名集合；无参宏返回空集。"""
    match = _DEFINE_HEAD.match(line)
    if not match or not match.group(2):
        return set()
    return {
        piece.strip()
        for piece in (match.group(3) or "").split(",")
        if piece.strip()
    }


def paste_affixes(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """宏体里用 ``##`` 拼接出来的**字面**前缀与后缀。

    ``#define l_floatatt(n) (DBL_##n)`` 会把源码里写的 ``MANT_DIG`` 拼成
    ``DBL_MANT_DIG``——一个源码和头文件里**都没有写过**的名字。文本层因此完全
    看不到这条依赖：Lua 的 ``lcode.c -> float.h`` 就这样被判成「疑似未使用」，
    删掉之后 ``use of undeclared identifier 'DBL_MANT_DIG'``。

    只取 ``##`` 旁边是**字面量**的那一半：``##`` 紧挨着的若是本宏的**形参**
    （``DBL_ ## n`` 里的 ``n``），拼出来的词随调用点变化，不构成固定词缀，
    收进来只会制造噪声。
    """
    prefixes: set[str] = set()
    suffixes: set[str] = set()
    for line in _strip_noise(text).splitlines():
        if not line.lstrip().startswith("#"):
            continue
        params = _macro_params(line)
        for match in _PASTE_PREFIX.finditer(line):
            if match.group(1) not in params:
                prefixes.add(match.group(1))
        for match in _PASTE_SUFFIX.finditer(line):
            if match.group(1) not in params:
                suffixes.add(match.group(1))
    return frozenset(prefixes), frozenset(suffixes)


def definition_offsets(
    text: str, prototypes: bool = False
) -> dict[str, int]:
    """同 :func:`definitions_in`，但给出每个名字**最早的定义位置**（字符下标）。

    ``prototypes`` 为真时把 ``void f(int);`` 这种**函数原型**也算声明。头文件
    里几乎全是原型，不算就一个供给者都认不出来；源文件里不能开——``return
    foo(a);`` 的前缀是 ``return``，会被误认成声明。


    位置不是可选项。doctest 的 ``exception_translator.cpp`` 在第 19、24 行定义
    ``getExceptionTranslators`` / ``translateActiveException``，却在**第 14 行**
    就用到了它们——那两处使用必须靠头文件里的声明才编得过。只看「有没有定义」
    会把这条真依赖判成可删（实测：删掉 ``#include "exception_translator.h"``
    后报 ``use of undeclared identifier``）。

    所以调用方要拿这个位置和「首次出现的位置」比：定义晚于首次使用，说明
    那个名字在使用点上还没有声明，头文件不能删。
    """
    stripped = _strip_noise(text)
    out: dict[str, int] = {}

    def add(name: str, offset: int) -> None:
        if name not in out or offset < out[name]:
            out[name] = offset

    offset = 0
    for line in stripped.splitlines(keepends=True):
        for pattern in (
            _DEFINE_NAME,
            _TAG_NAME,
            _NAMESPACE_NAME,
            _USING_ALIAS,
            _TYPEDEF_NAME,
            _BRACE_ALIAS,
        ):
            match = pattern.match(line)
            if match:
                add(match.group(1), offset + match.start(1))
        offset += len(line)

    for match in _ENUM_BODY.finditer(stripped):
        for piece in match.group(1).split(","):
            names = _IDENTIFIER.findall(piece)
            if names:
                add(names[0], match.start(1))

    for match in _OBJECT_DECL.finditer(stripped):
        if match.group(1) in _NOT_A_TYPE:
            continue
        add(match.group(2), match.start(2))

    for match in _CALL_OR_DEF.finditer(stripped):
        name = match.group(1)
        start = max(stripped.rfind(char, 0, match.start()) for char in ";{}\n")
        prefix = stripped[start + 1 : match.start()]
        # 前缀不能为空：``TEST_CASE(one) {`` 这种宏调用展开后就是函数定义，
        # 但宏名是**头文件给的**——把它认成定义会把真依赖误杀（``compat.h``
        # 往下拉出 ``#define TEST_CASE``，删掉它就编不过了）。
        if not prefix.strip() or "#" in prefix or not _TYPEISH_PREFIX.match(prefix):
            continue
        open_paren = match.end() - 1
        close = _matching_paren(stripped, open_paren)
        if close < 0:
            continue
        tail = stripped[close + 1 : close + 40]
        is_declaration = (
            _DEF_TAIL.match(tail)
            or tail.lstrip().startswith(":")
            or (prototypes and tail.lstrip().startswith(";"))
        )
        if not is_declaration:
            continue
        add(name, match.start(1))
        # 形参名也算本文件提供：``unsigned start`` 里的 ``start`` 与头文件无关。
        # 只取每段最后一个标识符（``const std::exception& e`` → ``e``），避免把
        # 形参的**类型名**误当成自供给。
        for piece in _split_params(stripped[open_paren + 1 : close]):
            names = _IDENTIFIER.findall(piece)
            if names:
                add(names[-1], match.start(1))

    return out


def definitions_in(text: str) -> set[str]:
    """源码自身**提供**的标识符：本文件定义的宏、类型、函数，以及函数的形参名。

    这些名字不是依赖的证据。头文件里也声明 ``inflate_fast`` 说明不了什么——
    ``inffast.c`` 自己就在定义它；删掉 ``#include "inffast.h"`` 之后，名字由
    定义本身给出，编译器不需要那条声明。同理 ``wildcmp``、``matchesAny`` 在
    ``filters.cpp`` 里是定义，``filters`` / ``caseSensitive`` 是它的形参名，
    而旧判定把这六个名字全当成了「删掉就缺东西」的证据。

    判据刻意保守：只在形态明确时才认作定义（``名(...) {``、``#define``、
    ``typedef``、``struct/enum/class``、``类型 名字;``）。一次调用
    ``foo(a);`` 后面跟的是 ``;``，不会被认成定义；``cond ? f(x) : g(y)``
    里的 ``f`` 因为前缀含 ``?`` 也被排除。宁可少认一个定义，也不要把真依赖
    误判成「自己有」——那个方向的错误会让用户删掉一行真的在用的 include。
    """
    return set(definition_offsets(text))


def _first_occurrence(text: str) -> dict[str, int]:
    """每个标识符在源码里**第一次出现**的位置（字符下标）。"""
    first: dict[str, int] = {}
    for match in _IDENTIFIER.finditer(_strip_noise(text)):
        name = match.group(0)
        if name not in first:
            first[name] = match.start()
    return first


def conditional_mentions(text: str) -> set[str]:
    """只在 ``#if`` / ``#ifdef`` / ``#ifndef`` / ``#elif`` 里被提到的标识符。

    这类「命中」问的是「这个宏在不在」，不是「这个宏的值是什么」。宏随头文件
    一起消失时，编译器只是换一个分支编译——不会产生一个未声明的名字。zlib 的
    ``zutil.c`` 只在这种位置提到 ``NO_GZCOMPRESS``。

    注意「编得过」不等于「可以删」：换掉被激活的分支会**静默改变程序**
    （``zutil.c`` 删掉 ``gzguts.h`` 后汇编指纹确实变了）。所以调用方只把它
    当成「理由不同」的标记，不据此放行。

    只有在**全部**出现位置都是条件判断时才算：宏一旦在别处被真正用到
    （展开成代码），就仍然是硬依赖。
    """
    conditional: set[str] = set()
    elsewhere: set[str] = set()
    for line in _strip_noise(text).splitlines():
        names = identifiers_in(line)
        if _CONDITIONAL_DIRECTIVE.match(line):
            conditional |= names
        else:
            elsewhere |= names
    return conditional - elsewhere


#: 名字空间作用域里「会做事情」的语句：``NAME(...);`` 这种没有返回类型前缀的
#: 调用/宏展开。``void f(int);`` 有前缀 ``void``，是声明，不算。
_SIDE_EFFECT_CALL = re.compile(r"^([A-Za-z_]\w*)\s*\(")
#: 这些宏只影响编译器的诊断行为，不改变生成的代码。把它们算成副作用会把一批
#: 本来可以删的 include 永久锁死——方向错的保守和不保守一样没用。
_NOT_A_SIDE_EFFECT = re.compile(
    r"SUPPRESS|WARNING|PRAGMA|IGNORE|DEPRECATED|PUSH$|POP$|CLANG|GCC|MSVC", re.I
)
#: 带初始化的对象定义（``int counter = 0;``）。``const`` / ``constexpr`` 不算：
#: 它们编译期就定了，删掉不改变程序。
#:
#: 旧实现是一条大正则，只认 ``static X y = …`` 这种「一个类型词 + 名字」的
#: 形态——``static int alpha_id = register_alpha();`` 因为中间多了一个 ``int``
#: 就匹配不上。那是一条**静默漏报**：动态初始化的对象恰恰是最该拦下的副作用，
#: 漏掉它等于把「删了程序少一块」的候选当推荐发出去。改成函数判据（见
#: :func:`_defines_an_object`）。
_ASSIGNMENT = re.compile(r"(?<![=!<>+\-*/%&|^])=(?!=)")
#: 声明式的尾巴：``ident(...)`` 或 ``ident[...]`` 收尾。抹掉开头的属性宏之后
#: 还剩这么一段，说明这句是**声明**，不是执行。
#:
#: 这条判据的来历：Windows UCRT 的系统头里到处是带 SAL 标注的声明——
#: ``_Success_(return != 0) _ACRTIMP char* __cdecl gets_s(char*, rsize_t);``。
#: 旧口径只看「以 ``名字(`` 开头且没有返回类型前缀」，于是整条 ``gets_s`` 的
#: 声明被当成副作用，Lua 语料上 13 条「删了没事」的大额候选（stdio.h / stdlib.h）
#: 因此被判成「正在使用」永久锁死。声明不执行任何东西：属性宏改的只是编译器
#: 的诊断与优化提示，删掉头文件后程序行为不变。
_DECLARATOR = re.compile(r"\b[A-Za-z_]\w*\s*(?:\([^;]*\)|\[[^\]]*\])\s*$")


def _after_call(code: str, start: int) -> str | None:
    """``code`` 里从 ``start`` 起那次调用的**右括号之后**的文本。

    括号不配对时返回 ``None``——看不清就不下结论，让这条继续按副作用处理。
    """
    depth = 0
    for position in range(start, len(code)):
        char = code[position]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return code[position + 1 :]
    return None


_NAMESPACE_OPEN = re.compile(r"\bnamespace\b[^;{]*\Z")


def _top_level_statements(text: str) -> list[str]:
    """名字空间作用域里以 ``;`` 结尾的语句。

    「名字空间作用域」不等于「大括号深度 0」：``namespace doctest { ... }``
    本身带一层括号，而 doctest 的注册宏正是写在命名空间**内部**的——按深度 0
    取会一条都取不到（第一版就是这样，junit.h 的
    ``DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter);`` 直接漏掉）。
    所以只把**非** namespace 的括号当成块。
    """
    out: list[str] = []
    buf: list[str] = []
    stack: list[bool] = []  # True = 这一层是 namespace
    #: 上一行是还没结束的预处理指令（以 ``\`` 续行）。多行 ``#define`` 的**函数体**
    #: 会被当成语句读进来，Lua ``lstate.h`` 的
    #: ``#define gco2ts(o) check_exp(…, &((cast_u(o))->ts))`` 就是这么被误判成
    #: 「名字空间作用域执行了一句 check_exp(...)」的。
    preprocessor = False
    for line in text.splitlines():
        if preprocessor or line.lstrip().startswith("#"):
            preprocessor = line.rstrip().endswith("\\")
            continue
        for char in line:
            if char == "{":
                stack.append(_NAMESPACE_OPEN.search("".join(buf)) is not None)
                buf = []
                continue
            if char == "}":
                if stack:
                    stack.pop()
                buf = []
                continue
            if char == ";" and all(stack):
                out.append("".join(buf))
                buf = []
                continue
            buf.append(char)
        buf.append(" ")
    return out


def side_effects_in(text: str) -> tuple[str, ...]:
    """头文件在名字空间作用域里**执行**的动作——不是声明，是副作用。

    为什么必须有这一项：usage 判定问的是「源码有没有提到这个头里的名字」，
    而有一类 include 从头到尾不提供任何被提到的名字，它靠**执行**起作用。
    doctest 的 ``reporters/junit.h`` 末尾一句
    ``DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter);`` 就属于这一类：
    删掉它编译通过、还真的少解析一个文件，看起来是一条干净的胜利——直到
    汇编对比显示 ``??__EDOCTEST_ANON_REPORTER_VAR_100``（JUnit reporter 的
    静态注册）整段消失。旧口径把它排在候选第 1 位。

    判据：
    * 语句以 ``名字(`` 开头且**没有**返回类型前缀 → 宏调用/函数调用；
    * 带 ``=`` 初始化的非 const 对象定义 → 真定义了一个东西。

    第二刀（2026-10-20）：开头那次调用之后**如果还剩下一条声明**，这句就是
    声明而不是执行。UCRT 的 ``_Success_(...) _ACRTIMP char* gets_s(...)`` 与
    ``_CRT_INSECURE_DEPRECATE_GLOBALS(...) _ACRTIMP char** _get_pgmptr(void)``
    都属于这一类——SAL 属性宏只是给编译器的提示，函数声明本身不执行任何东西。

    两种证据强度分开返回（见 :func:`side_effect_kinds_in`）：对象定义是**确定**
    的执行，调用形态只是**疑似**——文本层看不出一句宏调用展开成声明还是展开成
    带动态初始化的对象。
    """
    return tuple(text for _kind, text in side_effect_kinds_in(text))


def side_effect_kinds_in(text: str) -> tuple[tuple[str, str], ...]:
    """名字空间作用域里的可疑语句，按证据强度分成 ``("object"|"call", 语句)``。

    * ``object``：带初始化的对象定义。``static X y = register_it();`` 这种写法
      在加载时就会执行，文本层**确定**它是副作用——删了程序真的少一块。
    * ``call``：一句以宏名开头的调用，后面什么都没有。它可能展开成静态注册
      （doctest 的 ``DOCTEST_REGISTER_REPORTER``），也可能展开成一串声明
      （UCRT 的 ``__DEFINE_CPP_OVERLOAD_SECURE_FUNC_0_1(errno_t, strcat_s, …)``）。
      文本层分不清，所以只记为**疑似**，由编译器裁定（见 verify 的汇编等价闸门）。

    这个分层不是文字游戏：UCRT 的系统头里满是后一种形态，一律按「在用」处理会
    把整个 stdio.h / stdlib.h / string.h 候选池永久锁死——Lua 语料上那是 13 条
    「删了确实没事」的大额候选，其中 5 条实测能省 2.6%~3.3%。
    """
    found: list[tuple[str, str]] = []
    # 只抹注释、保留字符串字面量：报告里要原样显示这句做了什么
    # （``DOCTEST_REGISTER_REPORTER("junit", 0, JUnitReporter);`` 比
    # ``DOCTEST_REGISTER_REPORTER( , 0, JUnitReporter);`` 有用得多）。
    for statement in _top_level_statements(strip_comments(text)):
        code = statement.strip()
        if not code:
            continue
        call = _SIDE_EFFECT_CALL.match(code)
        if call and not _NOT_A_SIDE_EFFECT.search(call.group(1)):
            if not _is_declaration(code, call):
                found.append(("call", code[:70]))
                continue
        if _defines_an_object(code):
            found.append(("object", code[:70]))
    return tuple(found[:6])


def _defines_an_object(code: str) -> bool:
    """这句是不是「带初始化的对象定义」。

    判据：顶层有一个赋值号，赋值号左边只由类型/名字/指针符号组成（没有括号，
    所以不是函数声明或函数调用），也不是 ``const`` / ``typedef`` / ``using``。
    对象在加载时就要初始化——初始化式只要不是字面量，就是一次执行。
    """
    if re.match(r"^(?:const\b|constexpr\b|typedef\b|using\b)", code):
        return False
    match = _ASSIGNMENT.search(code)
    if match is None:
        return False
    head = code[: match.start()]
    if "(" in head or ")" in head:
        return False
    return re.match(r"^[A-Za-z_][\w\s\*&:<>,\[\]]*$", head) is not None


def _is_declaration(code: str, call: re.Match[str]) -> bool:
    """开头的宏调用之后是否还跟着一条声明（→ 这句本身是声明）。

    ``=`` 出现在尾巴里就当定义处理（``REGISTER(x) int y = f();`` 那种形态），
    保持「宁可算副作用」的方向。
    """
    rest = _after_call(code, call.start())
    if rest is None or "=" in rest:
        return False
    return _DECLARATOR.search(rest) is not None


_PP_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)(\([^)]*\))?(.*)$")


def _logical_lines(text: str):
    r"""把以 ``\`` 续行的预处理语句拼成一整行。宏体的跨行形态在系统头里遍地都是。"""
    buffer = ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
            continue
        buffer += line
        yield buffer
        buffer = ""
    if buffer:
        yield buffer


_CONDITIONAL_DIRECTIVE = re.compile(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b")


def _conditional_directive_spans(text: str) -> list[tuple[int, int]]:
    """所有条件指令行的字符区间。这些行上的标识符**始终**参与编译。"""
    spans: list[tuple[int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        if _CONDITIONAL_DIRECTIVE.match(line):
            spans.append((offset, offset + len(line)))
        offset += len(line)
    return spans


def macro_bodies_in(text: str) -> dict[str, frozenset[str]]:
    """头文件里每个宏**展开体**提到的标识符：``lua_getlocaledecpoint`` →
    ``{localeconv, decimal_point}``。

    为什么必须有这一项：源码写的是 ``lua_getlocaledecpoint()``（定义在幸存的
    ``luaconf.h`` 里），真正的依赖 ``localeconv`` 只出现在宏体里。只看「源码提没
    提到这个名字」的判定会把它判成「没用过 <locale.h>」——然后编译失败。实测
    （Lua 5.4.7 ``liolib.c → locale.h``、``lobject.c/lstrlib.c/llex.c`` 同一条）
    正是这个形态：宏体里的名字在源码里一个都找不到。

    参数名不算：``#define sq(x) ((x)*(x))`` 的 ``x`` 是形参，不是依赖。
    """
    out: dict[str, set[str]] = {}
    for logical in _logical_lines(text):
        match = _PP_DEFINE.match(logical)
        if not match:
            continue
        name, params, body = match.group(1), match.group(2) or "", match.group(3)
        ids = set(_IDENTIFIER.findall(strip_comments(body)))
        if params:
            ids -= set(_IDENTIFIER.findall(params))
        ids.discard(name)
        out.setdefault(name, set()).update(ids)
    return {key: frozenset(value) for key, value in out.items()}


class IdentifierIndex:
    """Per-scan cache of ``path -> identifiers``.

    Why a cache and not a module-level dict: one scan evaluates hundreds of
    candidates whose closures overlap heavily (every unit that includes
    ``json.hpp`` sees the same thirty detail headers), so caching turns the
    work into "read each header once".  A module-level cache would keep the
    whole stdlib's identifier sets alive for the lifetime of the process,
    which is exactly wrong for the long-running dashboard.

    An unreadable file is cached as an empty set rather than retried, so one
    broken header cannot make a scan quadratic.
    """

    __slots__ = (
        "_headers",
        "_sources",
        "_providers",
        "_renamed",
        "_defined",
        "_supplied",
        "_conditional",
        "_sidefx",
        "_pastes",
        "_aliases",
        "_macros",
        "_live",
        "_defined_ids",
        "_live_src",
    )

    def __init__(self) -> None:
        self._headers: dict[Path, frozenset[str]] = {}
        self._sources: dict[Path, frozenset[str]] = {}
        self._providers: dict[Path, frozenset[str]] = {}
        self._renamed: dict[Path, frozenset[str]] = {}
        self._defined: dict[Path, frozenset[str]] = {}
        self._supplied: dict[tuple[Path, frozenset[str]], frozenset[str]] = {}
        self._conditional: dict[Path, frozenset[str]] = {}
        self._sidefx: dict[Path, tuple[str, ...]] = {}
        self._pastes: dict[Path, tuple[frozenset[str], frozenset[str]]] = {}
        self._aliases: dict[Path, tuple[tuple[str, str], ...]] = {}
        self._macros: dict[Path, dict[str, frozenset[str]]] = {}
        self._live: dict[tuple[Path, frozenset[str]], frozenset[str]] = {}
        self._defined_ids: dict[Path, frozenset[str]] = {}
        self._live_src: dict[tuple[Path, frozenset[str]], frozenset[str]] = {}
        self._defined_ids: dict[Path, frozenset[str]] = {}

    def header_ids(self, path: Path) -> frozenset[str]:
        cached = self._headers.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        ids = frozenset(identifiers_in(_strip_noise(text))) if text else frozenset()
        self._headers[path] = ids
        return ids

    def header_provider_ids(self, path: Path) -> frozenset[str]:
        """这个头**声明/定义**了哪些名字（去掉重命名宏）。

        只用于**幸存头**一侧（判断「别的头是否也能提供 X」）。两处语义：

        * ``#define X Y`` 与 ``#pragma map(X, ...)`` 只是别名，别名不能证明
          展开目标 Y 在删除后仍然有声明——按"能提供"算会把真实依赖判成可删
          （zlib 的 ``inflate_fast`` 就是这样）；
        * **「提到」不等于「提供」**。Lua 的 ``ldo.h`` 在另一个宏的展开体里
          **调用** ``luaC_checkGC``，而真声明在 ``lgc.h``；按「文本出现过」
          算供给，``ldebug.c -> lstring.h`` 就被判成「另有供给 → 疑似未使用」，
          删掉之后 ``call to undeclared function 'luaC_checkGC'``。
          所以这里只认**声明形态**（``#define``、函数原型/定义、类型与对象声明），
          原型也算（头文件里几乎全是原型）。

        **不要**把它用在被删闭包那一侧：闭包里的 ``#define X Y`` 是在**定义**
        宏 X，宏随头一起消失，X 就是它提供的（fcntl.h 的 ``O_BINARY``）。
        那一侧必须用 :meth:`header_ids`。
        """
        cached = self._providers.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        if text is None:
            providers: frozenset[str] = frozenset()
        else:
            declared = frozenset(definition_offsets(text, prototypes=True))
            renamed = self._renamed_names(path)
            providers = declared - renamed
        self._providers[path] = providers
        return providers

    def header_paste_affixes(self, path: Path) -> tuple[frozenset[str], frozenset[str]]:
        """这个头里 ``##`` 拼接用的字面词缀（见 :func:`paste_affixes`）。"""
        cached = self._pastes.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        affixes = paste_affixes(text) if text is not None else (frozenset(), frozenset())
        self._pastes[path] = affixes
        return affixes

    def header_aliases(self, path: Path) -> tuple[tuple[str, str], ...]:
        """这个头里的重命名别名 ``(别名, 目标)``（``#define X Y`` 形态）。

        供 graph.py 做**别名链解析**。两种命运，方向相反：

        * 幸存头里的 ``#define X Y``：X 这个名字在删除后**还在**，但只有
          目标 Y 也活着它才可用——Y 在幸存侧仍有声明时 X 算「有供给」
          （doctest 的 ``#define DOCTEST_SUPPRESS_…_POP  …_COMMON_…_POP``
          链条）；Y 的声明只活在被删闭包里时 X 等于没了（zlib 的
          ``inflate_fast``）。
        * 被删闭包里的 ``#define X Y``：X 这个名字本身随头一起消失——
          哪怕目标是到处都有的系统宏（minigzip 的 ``O_BINARY`` →
          ``_O_BINARY``），删掉定义者编译就报 ``undeclared``。
        """
        cached = self._aliases.get(path)
        if cached is not None:
            return cached
        text = _read(path) or ""
        pairs: list[tuple[str, str]] = []
        for line in _strip_noise(text).splitlines():
            match = _RENAME_DEFINE.match(line)
            if match and match.group(2) not in _RENAME_TARGET_IS_A_MACRO:
                pairs.append((match.group(1), match.group(2)))
        out = tuple(pairs)
        self._aliases[path] = out
        return out

    def _renamed_names(self, path: Path) -> frozenset[str]:
        cached = self._renamed.get(path)
        if cached is not None:
            return cached
        text = _read(path) or ""
        names: set[str] = set()
        for line in _strip_noise(text).splitlines():
            match = _RENAME_DEFINE.match(line)
            if match and match.group(2) in _RENAME_TARGET_IS_A_MACRO:
                continue  # ``#define local static``：这是宏定义，不是别名
            pragma = _RENAME_PRAGMA.match(line)
            if pragma:
                names.add(pragma.group(1))
            elif match:
                names.add(match.group(1))
        frozen = frozenset(names)
        self._renamed[path] = frozen
        return frozen

    def source_ids(self, path: Path) -> frozenset[str] | None:
        """``None`` (not an empty set) when the source cannot be read.

        The distinction matters: "read it, found no identifiers" and "could not
        read it" lead to different verdicts, and collapsing them would turn a
        permissions problem into a confident "unused".
        """
        cached = self._sources.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        if text is None:
            return None
        ids = frozenset(identifiers_in(_strip_noise(text)))
        self._sources[path] = ids
        return ids

    def header_side_effects(self, path: Path) -> tuple[str, ...]:
        """这个头在名字空间作用域里执行的动作（见 :func:`side_effects_in`）。"""
        return tuple(text for _kind, text in self.header_side_effect_kinds(path))

    def header_side_effect_kinds(self, path: Path) -> tuple[tuple[str, str], ...]:
        """按证据强度分层的可疑语句（见 :func:`side_effect_kinds_in`）。"""
        cached = self._sidefx.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        effects = side_effect_kinds_in(text) if text is not None else ()
        self._sidefx[path] = effects
        return effects

    def macro_bodies(self, path: Path) -> dict[str, frozenset[str]]:
        """这个头的宏展开体里提到的标识符（见 :func:`macro_bodies_in`）。"""
        cached = self._macros.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        bodies = macro_bodies_in(text) if text is not None else {}
        self._macros[path] = bodies
        return bodies

    def header_live_provider_ids(
        self, path: Path, defined_macros: frozenset[str] = frozenset()
    ) -> frozenset[str]:
        """幸存头里**这次编译真的会看到**的声明。

        与 :meth:`header_provider_ids` 的差别只有一处：把声明落在
        「本次编译不参与」的条件区间里的名字剔掉。实测（doctest
        ``test_traversal.cpp → traversal.h``）：``doctest/doctest.h`` 里
        ``class TraversalState`` 整段包在
        ``#if defined(DOCTEST_CONFIG_IMPLEMENT) && !defined(...)`` 里，
        这个单元根本不编译那段——文本层却把它当「另有供给者」，于是把一条
        删了就编不过的候选推荐了出来。

        方向是**保守**的：少算一个供给者 → 更容易判「在用」→ 少推荐。
        条件表达式求不了值（``#if defined(A) || !defined(B)``）时也按不可信
        处理，理由与 ``_blocking_conditional_regions`` 相同。
        """
        key = (path, defined_macros)
        cached = self._live.get(key)
        if cached is not None:
            return cached
        text = _read(path)
        if text is None:
            live: frozenset[str] = frozenset()
        else:
            offsets = definition_offsets(text, prototypes=True)
            dead = _blocking_conditional_regions(text, defined_macros)
            live = frozenset(
                name
                for name in offsets
                if not any(low <= offsets[name] < high for low, high in dead)
            ) - self._renamed_names(path)
        self._live[key] = live
        return live

    def header_conditional_provider_ids(self, path: Path) -> frozenset[str]:
        """文本里有声明、但落在**本次编译不参与**的条件区间里的名字。

        它们既不能当「另有供给者」（那会把真依赖判成可删），也不能当
        「谁都不提供」（那会变成环境噪音、从证据里剔除，同样放行删除）。
        唯一保守的用法是：**当作「判不了」，按在用处理**。
        """
        return self.header_provider_ids(path) - self.header_live_provider_ids(path)

    def header_definition_ids(self, path: Path) -> frozenset[str]:
        """这个头**定义**了哪些名字（宏、类型、原型/定义），「只是提到」不算。

        用在**消失集合**那一侧：``lualib.h`` 里 ``LUAMOD_API int (luaopen_base)(…)``
        只是**使用**了 ``LUAMOD_API``（它由 ``luaconf.h`` 定义），把「提到」算成
        「提供」会让源码里的这次使用变成「只有这条 include 提供它」的证据，
        一条本来可删的 include 因此被判「在用」。实测（Lua）：lualib.h 的
        5 条候选全被这个幻影命中顶掉。

        与 :meth:`header_provider_ids` 的唯一差别是**不剥别名**：闭包里的
        ``#define X Y`` 是在定义宏 X，宏随头一起消失，X 就是它提供的
        （fcntl.h 的 ``O_BINARY``）。
        """
        cached = self._defined_ids.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        ids = (
            frozenset(definition_offsets(text, prototypes=True))
            if text is not None
            else frozenset()
        )
        self._defined_ids[path] = ids
        return ids

    def source_defined_ids(self, path: Path) -> frozenset[str]:
        """源码自己提供的名字（见 :func:`definitions_in`）。"""
        cached = self._defined.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        defined = (
            frozenset(definitions_in(text)) if text is not None else frozenset()
        )
        self._defined[path] = defined
        return defined

    def source_self_supplied_ids(
        self, path: Path, defined_macros: frozenset[str] = frozenset()
    ) -> frozenset[str]:
        """源码**自己就给得出**、且给的位置不晚于首次使用的名字。

        比 :meth:`source_defined_ids` 多两道判据：

        * **位置**：定义在**使用之后**时，使用点上这个名字还没有声明，
          头文件仍然是必需的（doctest ``exception_translator.cpp`` 第 14 行
          先用、第 19 行才定义 ``getExceptionTranslators``）。少了这道判据，
          那条候选被判「疑似未使用」，实测删掉后
          ``use of undeclared identifier``。
        * **判死了的条件分支**：定义落在 ``#ifdef X``（X 不在命令行 ``-D``
          里）这类**确定不编译**的区间里时不算——那个定义根本不存在，
          名字实际来自头文件（zlib ``crc32.c`` 的 ``#ifdef DYNAMIC_CRC_TABLE``
          分支里定义了 ``x2n_table``，本构建该分支没有被编译；按自供给算，
          真依赖凭空消失，判成可删）。判不了死活的分支保持旧口径——
          里面的定义照算自供给。头文件侧不做这一步：头文件整个被 include
          guard 包着，全判死就什么声明都不剩了。
        """
        key = (path, defined_macros)
        cached = self._supplied.get(key)
        if cached is not None:
            return cached
        text = _read(path)
        if text is None:
            supplied: frozenset[str] = frozenset()
        else:
            offsets = definition_offsets(text)
            first = _first_occurrence(text)
            regions = _blocking_conditional_regions(text, defined_macros)
            supplied = frozenset(
                name
                for name, where in offsets.items()
                if first.get(name, where) >= where
                and not any(lo <= where < hi for lo, hi in regions)
            )
        self._supplied[key] = supplied
        return supplied

    def source_live_ids(
        self, path: Path, defined_macros: frozenset[str] = frozenset()
    ) -> frozenset[str]:
        """源码里**这次编译真的会用到**的标识符。

        与 :meth:`source_ids` 的差别只有一处：**只出现在判死了的条件分支里**的
        名字不算用到。判据与 ``source_self_supplied_ids`` 同源，但方向相反——
        那边问「这个名字是不是本文件给的」，这边问「这个名字到底有没有被编译」。

        实测（zstd ``lib/common/pool.c → allocations.h``）：``ZSTD_customCalloc``
        与 ``ZSTD_customFree`` 的全部引用都在 ``#ifdef ZSTD_MULTITHREAD`` 里，
        而本构建没有定义它。旧判定按「源码提到 + 只有这个头提供」判「在用」，
        于是这条候选永远进不了推荐；真值是这个头**整个可删**——而且收益巨大：
        384 ms → 213 ms（−44.5%），解析文件 184 → 32。这是 zstd 上最大的一条。

        ``#if`` 表达式求不了值的区间**不算死区**（``unverifiable=False``）：
        ``#if defined(__cplusplus)``、``#ifdef _WIN32`` 这类由编译器或平台决定，
        命令行里看不到，按死区算会把真实使用抹掉。
        """
        key = (path, defined_macros)
        cached = self._live_src.get(key)
        if cached is not None:
            return cached
        text = _read(path)
        ids = self.source_ids(path) or frozenset()
        if text is None:
            live = frozenset()
        else:
            regions = _blocking_conditional_regions(
                text, defined_macros, unverifiable=False
            )
            if not regions:
                live = ids
            else:
                # 条件指令**本身**永远是被求值的：``#ifdef NO_GZCOMPRESS`` 里的
                # NO_GZCOMPRESS 决定走哪个分支，删掉定义会换分支（zlib
                # zutil.c → gzguts.h 就是这样静默改程序的）。所以只有落在死区
                # **且不在条件行上**的出现才不算「用到」。
                guard_spans = _conditional_directive_spans(text)
                stripped = _strip_noise(text)
                alive: set[str] = set()
                for match in _IDENTIFIER.finditer(stripped):
                    name = match.group(0)
                    if name in alive or name not in ids:
                        continue
                    position = match.start()
                    dead = any(low <= position < high for low, high in regions)
                    if not dead or any(
                        low <= position < high for low, high in guard_spans
                    ):
                        alive.add(name)
                live = frozenset(alive)
        self._live_src[key] = live
        return live

    def source_conditional_ids(self, path: Path) -> frozenset[str]:
        """源码只在条件编译判断里提到的名字（见 :func:`conditional_mentions`）。"""
        cached = self._conditional.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        names = (
            frozenset(conditional_mentions(text)) if text is not None else frozenset()
        )
        self._conditional[path] = names
        return names


def analyse_usage(
    source: Path,
    header: Path,
    closure: tuple[Path, ...] | list[Path] | set[Path] | None = None,
    index: IdentifierIndex | None = None,
    reachable_counts: dict[str, int] | None = None,
    paste_affixes: tuple[frozenset[str], frozenset[str]] | None = None,
    declarers: dict[str, frozenset[Path]] | None = None,
    conditional_declarers: dict[str, frozenset[Path]] | None = None,
    alias_defs: dict[str, tuple[tuple[Path, str], ...]] | None = None,
    macro_defs: dict[str, tuple[tuple[Path, frozenset[str]], ...]] | None = None,
    defined_macros: frozenset[str] | None = None,
) -> UsageSignal:
    """Compare the identifiers a TU uses with those that disappear with an include.

    ``paste_affixes`` 是**幸存头**里 ``##`` 拼接用的字面词缀
    ``(前缀集合, 后缀集合)``。给出它时，消失集合里能被拼出来的名字也算命中：
    源码写 ``MANT_DIG``、头文件提供 ``DBL_MANT_DIG``、中间靠
    ``#define l_floatatt(n) (DBL_##n)`` 接上的情形，否则一条都看不到。

    ``closure`` is the *exclusive* closure of the candidate include -- the
    headers that stop being parsed if it is removed -- and normally includes
    ``header`` itself.  Passing it is what makes the verdict correct for
    headers that forward other headers; omitting it keeps the old
    single-file behaviour, which is only right when the candidate is a leaf.

    ``defined_macros`` 是编译命令行 ``-D`` 给出的宏名集合，用于把 ``#ifdef X``
    / ``#ifndef X`` 求值成死/活分支：死分支里的定义不算源码自供给（zlib
    ``crc32.c`` 的 ``x2n_table``）。给不出就保持旧口径。

    ``reachable_counts`` maps an identifier to **how many headers in the whole
    reachable set declare it** (reachable = everything the unit parses now,
    including what the vanishing set contributes).  With it, a hit counts as
    evidence only when the vanishing set is its *sole* provider: a symbol that
    survives in another header cannot justify keeping this include.  Without
    it every mention counts, which on doctest gave 60 "in use" verdicts out of
    60 candidates while 48% of them actually compiled — the same mistake the
    closure itself was built to avoid, one level down.

    Note that a header which merely aggregates others (``#pragma once`` plus
    a wall of ``#include``) is *not* judged on its own text: with a closure it
    is judged on everything it brings in, which is precisely what a reader of
    the code means by "is this include used".
    """
    index = index or IdentifierIndex()

    # 只用**这次编译真的会用到**的名字：判死了的 ``#ifdef`` 分支里的引用不算。
    # 少了这一步，``#ifdef ZSTD_MULTITHREAD`` 里的 ``ZSTD_customCalloc`` 会让
    # whole-header 依赖 ``allocations.h`` 永远「在用」——实测那是 zstd 上最大的
    # 一条删除（−44.5%、解析文件 184→32）。
    source_ids = index.source_live_ids(source, defined_macros or frozenset())

    # 源码自己定义的名字不算依赖证据：删掉头文件之后，这些名字由源码自己给出。
    # 不加这一步，``inffast.c`` 因为自己定义了 ``inflate_fast`` 而永远判「在用」
    # ——它的每一行实现都在提醒判定「这个头被用着」。
    self_provided = index.source_self_supplied_ids(
        source, defined_macros if defined_macros is not None else frozenset()
    )
    wanted_ids = source_ids - self_provided

    extra = [Path(p) for p in (closure or ()) if Path(p) != header]
    # Header first: when several files in the set declare the same name, the
    # candidate itself is the most useful thing to name in the report.
    ordered = [header, *sorted(extra)]

    total = 0
    first_nonempty: Path | None = None
    # name -> (declaring header, how many headers of this set declare it)
    hits: dict[str, tuple[Path, int]] = {}
    headers_with_text = 0
    #: 命中了、但源码自己就提供的名字。留着写进结论，读者才知道判定为什么
    #: 没把它们算成依赖。
    self_hits: set[str] = set()
    #: (头文件, 语句) —— 消失集合里**确定**靠副作用起作用的头。
    effects: list[tuple[Path, str]] = []
    #: (头文件, 语句) —— 同上的**疑似**形态（宏调用，展开结果文本层看不到）。
    suspected: list[tuple[Path, str]] = []

    for path in ordered:
        # 闭包一侧用**完整** header_ids：这里的头是要被删掉的，``#define X Y``
        # 里的 X 是本头定义的宏，随头一起消失——它就是提供者（zlib 的
        # ``O_BINARY``：只在 fcntl.h 里定义，删掉后 ``O_BINARY`` 未声明，编译
        # 失败；若在这里剥掉重命名宏，唯一的证据就没了，判成可删）。
        # 幸存头一侧（``reachable_counts``，见 graph.py）才用
        # ``header_provider_ids``：那边 ``#define X Y`` 只是别名，不能证明
        # 展开目标 Y 在删除后仍然有声明。两侧语义不同，不能共用一个集合。
        ids = index.header_ids(path)
        if not ids:
            continue
        headers_with_text += 1
        total += len(ids)
        if first_nonempty is None:
            first_nonempty = path
        self_hits |= ids & source_ids & self_provided
        for kind, statement in index.header_side_effect_kinds(path):
            (effects if kind == "object" else suspected).append((path, statement))
        for name in index.header_definition_ids(path) & wanted_ids:
            # 第一次见到的声明者先记下；次数在整轮遍历完后才算得准。
            hits.setdefault(name, (path, 0))
            provider, count = hits[name]
            hits[name] = (provider, count + 1)

    def _pasted_names() -> list[str]:
        """消失集合里**可能被 ``##`` 拼出来**的名字。

        只在快要下「没在用」的结论时才算——这一步要扫整个消失集合，放在别处
        白烧时间。
        """
        if not paste_affixes:
            return []
        prefixes, suffixes = paste_affixes
        if not prefixes and not suffixes:
            return []
        found: set[str] = set()
        for path in ordered:
            for name in index.header_ids(path):
                for prefix in prefixes:
                    if (
                        len(name) > len(prefix)
                        and name.startswith(prefix)
                        and name[len(prefix) :] in source_ids
                    ):
                        found.add(name)
                for suffix in suffixes:
                    if (
                        len(name) > len(suffix)
                        and name.endswith(suffix)
                        and name[: -len(suffix)] in source_ids
                    ):
                        found.add(name)
        return sorted(found)[:8]

    if effects:
        # **副作用压过一切文本证据**：这个头靠「被包含时执行了什么」起作用，
        # 源码提不提到它的名字都无关。它必须留在候选之外——删掉之后编译照样
        # 通过，程序却悄悄少了一块（doctest 的 junit.h / xml.h / console.h
        # 三条 ``DOCTEST_REGISTER_REPORTER`` 就是全部候选里排最前的三条）。
        #
        # 只有 ``object`` 这一类进得来：带初始化的对象定义在加载时就会执行，
        # 文本层看得确凿。宏调用形态（``call``）走下面的「疑似」通道。
        return UsageSignal(
            verdict=UsageVerdict.IN_USE,
            header_identifier_count=total,
            provided_by=effects[0][0],
            side_effects=tuple(text for _path, text in effects[:3]),
            self_provided=tuple(sorted(self_hits)[:8]),
        )

    # **宏体里的依赖**：源码提到的是幸存头里的宏，真正的符号藏在宏的展开体里。
    # 判据只有一条——那个名字**只有**消失集合提供（幸存侧一个声明者都没有），
    # 宏又活下来继续展开它 → 删掉这条 include 就编不过。
    if macro_defs and declarers is not None:
        vanishing = {Path(p) for p in ordered}
        for macro in sorted(macro_defs):
            bodies = [
                body for path, body in macro_defs[macro] if path not in vanishing
            ]
            if not bodies:
                continue  # 宏自己随头消失，已经由下面的命中路径处理
            for token in sorted(set().union(*bodies)):
                if token in self_provided:
                    continue
                owners = declarers.get(token, frozenset())
                if owners & vanishing and not (owners - vanishing):
                    return UsageSignal(
                        verdict=UsageVerdict.IN_USE,
                        header_identifier_count=total,
                        shared_identifiers=(token,),
                        provided_by=sorted(owners & vanishing)[0],
                        macro_expansion=(macro, token),
                        self_provided=tuple(sorted(self_hits)[:8]),
                    )

    if hits:
        # **供给者唯一性**：命中一个标识符只说明 TU 用到它，不说明它来自
        # 这条 include。同一个名字往往还有别的头在提供——删掉这条之后它照样
        # 存在，那就不该把它算成「不能删」的理由。
        #
        # 实测（doctest，24 单元 60 条候选）：旧的判定只看「闭包里是否声明」，
        # 结果 60/60 全判「正在使用」，而真实编译有 29/60 能删。差别全在
        # 这一条上——闭包有 275 个头，TU 随便提一个 string 就算在用。
        sole: dict[str, tuple[Path, int]] = {}
        cross: dict[str, tuple[Path, int]] = {}
        #: 幸存侧只有条件编译区里的声明：既不能当「另有供给」，也不能当
        #: 「谁都不提供」，只能记成「判不了」（见下面的注释）。
        uncertain: dict[str, tuple[Path, int]] = {}
        #: 谁都不提供的命中：命令行 ``-D`` 或源码之外的世界给的，删除任何
        #: include 都带不走（doctest 的 ``DOCTEST_CONFIG_DISABLE``）。
        ambient: list[str] = []
        if declarers is not None and alias_defs is not None:
            # **基于集合的精确语义**。消失集合就是 ``ordered``（闭包 + 候选
            # 自己），名字 X 删除后的命运有四种，逐一判定：
            #
            # 1. 幸存侧还有真实声明者 → X 活着 → 命中不算证据；
            # 2. 声明者全在消失集合 → X 没了 → 在用（NO_GZCOMPRESS、
            #    luaC_checkGC）；
            # 3. X 只是别名且别名定义在消失集合 → 名字本身随头消失 → 在用
            #    （O_BINARY → _O_BINARY，目标再 ubiquitous 也没用）；
            # 4. X 是幸存头的别名：目标 Y 在幸存侧仍有声明者 → X 活着 →
            #    不算证据（doctest 的 SUPPRESS 链）；Y 也活不下去 → 在用
            #    （zlib inflate_fast）。
            # 谁都不提供（1–4 全不中）→ 环境噪音，从证据里剔除。
            vanishing = {Path(p) for p in ordered}
            for name, (provider, count) in hits.items():
                real = declarers.get(name, frozenset())
                if real - vanishing:
                    cross[name] = (provider, count)
                    continue
                if real:
                    sole[name] = (provider, count)
                    continue
                # 幸存侧只有**条件编译区**里的声明：本次编译看不到它，
                # 但也不能说它不存在。两头都不能当证据用——不算「另有供给」
                # （否则真依赖被放行：doctest test_traversal.cpp→traversal.h），
                # 也不算「谁都不提供」（否则这些名字掉进 ambient 被当噪音剔除）。
                # 唯一老实的结论是「判不了」，交给 verify 用编译裁决。
                if (conditional_declarers or {}).get(name):
                    uncertain[name] = (provider, count)
                    continue
                aliases_for_name = alias_defs.get(name, ())
                if any(h in vanishing for h, _t in aliases_for_name):
                    # 消失集合里有人定义了这个别名：名字随头消失。
                    sole[name] = (provider, count)
                    continue
                surviving_aliases = [
                    (h, t) for h, t in aliases_for_name if h not in vanishing
                ]
                if any(
                    declarers.get(t, frozenset()) - vanishing
                    for _h, t in surviving_aliases
                ):
                    # 幸存别名 + 目标在幸存侧仍有声明者 → 名字照常可用。
                    cross[name] = (provider, count)
                    continue
                if surviving_aliases:
                    # 幸存头保留了别名，但目标的声明全在消失集合里——
                    # 展开出来的名字编不过（zlib inflate_fast）。
                    sole[name] = (provider, count)
                    continue
                ambient.append(name)
        else:
            for name, (provider, count) in hits.items():
                total_providers = (
                    count
                    if reachable_counts is None
                    else reachable_counts.get(name, count)
                )
                if total_providers <= count:
                    sole[name] = (provider, count)
                else:
                    cross[name] = (provider, count)

        # **特异性**：命中一个标识符不等于这个 include 被使用。
        #
        # 实测数据（nlohmann/json 的 locale 候选）把这个问题摆得很清楚：
        # 源码确实「命中」了闭包里的标识符，但命中的是 `config`（108 个头
        # 里 40 个都声明它）、`value`（23 个）、`move`（20 个）——这类词是
        # 标准库的环境噪音，不是依赖证据。第一版判定因此把 218/222 个候选
        # 全判成「正在使用」，过度保守到几乎不再给任何建议。
        #
        # 规则：只有「在消失集合里足够罕见」的命中才算证据。阈值随集合大小
        # 浮动，但设下限 2——只有一两个头声明的名字才是具体依赖。
        threshold = max(2, headers_with_text // 25)
        specific = {
            name: provider
            for name, (provider, count) in sole.items()
            if count <= threshold
        }

        # **存在性判断仍然是依赖**，只是理由不同：宏只出现在 ``#ifdef`` /
        # ``#ifndef`` / ``#if defined(...)`` 里时，问的是「它在不在」。宏随头
        # 一起消失后编译器**照样编得过**——它只是换一个分支。所以这里没有把
        # 它降级成「疑似未使用」：那样做会让工具推荐一批「编译通过、但悄悄编
        # 了另一个程序」的改动。
        #
        # 实测（zlib ``zutil.c -> gzguts.h``）：``NO_GZCOMPRESS`` 只出现在
        # ``#ifdef`` 里，删除后编译通过、少解析 15 个文件，看起来是一条完美
        # 的优化——但汇编指纹从 ``aac1b5f8…`` 变成 ``9b6bde46…``：被激活的分支
        # 变了，``zlibCompileFlags()`` 的返回值随之改变。先按「可删」处理过一
        # 版，正是代码等价闸门把这个改动拦下来的。
        #
        # 所以：判定不变（IN_USE），但把这件事单独记进 ``conditional_only``
        # 并在报告里写明——读者至少知道这一条的理由是「换分支」，不是「缺声明」。
        conditional = index.source_conditional_ids(source)
        weak = tuple(sorted(n for n in specific if n in conditional)[:8])

        if specific:
            # 具体的命中：按声明者路径排序，保证输出稳定可复现。
            name = sorted(specific)[0]
            return UsageSignal(
                verdict=UsageVerdict.IN_USE,
                shared_identifiers=tuple(sorted(specific)[:8]),
                header_identifier_count=total,
                provided_by=specific[name],
                ambient_identifiers=tuple(
                    sorted(
                        n
                        for n, (_p, c) in sole.items()
                        if c > threshold
                    )[:8]
                ),
                cross_provided=tuple(sorted(cross)[:8]),
                self_provided=tuple(sorted(self_hits)[:8]),
                conditional_only=weak,
                unprovided=tuple(sorted(ambient)[:8]),
            )
        if sole or uncertain:
            # 有命中，但证据都不可用：要么过于普遍（sole），要么幸存侧的声明
            # 在条件编译区、本次编译看不到（uncertain）。这不是「在用」的证据，
            # 也不该算「没用」。
            return UsageSignal(
                verdict=UsageVerdict.UNKNOWN,
                shared_identifiers=tuple(sorted(sole or uncertain)[:8]),
                header_identifier_count=total,
                ambient_identifiers=tuple(sorted(sole)[:8]),
                note=(
                    ""
                    if sole
                    else (
                        f"{'、'.join(sorted(uncertain)[:2])} 只在幸存头文件的条件编译区里"
                        "声明，本次编译看不到它——删不删由编译验证裁定"
                    )
                ),
                cross_provided=tuple(sorted(cross)[:8]),
                self_provided=tuple(sorted(self_hits)[:8]),
                unprovided=tuple(sorted(ambient)[:8]),
            )
        # 命中的每一个名字，在保留下来的头文件里都还有供给——删掉这条
        # include 之后它们照样存在。这不是「证据不足」，是证据指向删除；
        # 但文本层看不到命名空间与重载，所以仍然只是 *疑似*，由 verify 定论。
        pasted = _pasted_names()
        if pasted:
            # 拼出来的名字两边文本里都不存在，只有编译器拼得出来。宁可判「在用」。
            return UsageSignal(
                verdict=UsageVerdict.IN_USE,
                header_identifier_count=total,
                cross_provided=tuple(sorted(cross)[:8]),
                self_provided=tuple(sorted(self_hits)[:8]),
                pasted=tuple(pasted),
                unprovided=tuple(sorted(ambient)[:8]),
            )
        return _plausibly_unused_or_suspected(
            total=total,
            cross=cross,
            self_hits=self_hits,
            ambient=ambient,
            suspected=suspected,
        )

    if total == 0:
        # Includes nothing, declares nothing, or could not be read.  Saying
        # "unused" here would be a guess dressed up as a finding.
        return UsageSignal(
            verdict=UsageVerdict.UNKNOWN,
            header_identifier_count=0,
            note="头文件里读不出任何可比较的名字（空文件、读不到，或全是条件编译）",
        )

    if not extra and first_nonempty is not None:
        text = _read(first_nonempty)
        if text is not None and _declares_nothing(text):
            # Aggregation/macro-only header evaluated on its own text alone:
            # its "identifiers" are the names of the headers it forwards, so a
            # naive comparison reports a false unused.  With a closure this
            # branch is never reached -- the closure is the real comparison.
            return UsageSignal(
                verdict=UsageVerdict.UNKNOWN,
                header_identifier_count=0,
                note=(
                    "这个头只做转发或只提供宏（没有函数/类型声明），"
                    "它的作用取决于后续头怎么用这些宏"
                ),
            )

    pasted = _pasted_names()
    if pasted:
        return UsageSignal(
            verdict=UsageVerdict.IN_USE,
            header_identifier_count=total,
            self_provided=tuple(sorted(self_hits)[:8]),
            pasted=tuple(pasted),
        )
    return _plausibly_unused_or_suspected(
        total=total,
        cross={},
        self_hits=self_hits,
        ambient=[],
        suspected=suspected,
    )


def _plausibly_unused_or_suspected(
    *,
    total: int,
    cross: dict[str, tuple[Path, int]],
    self_hits: set[str],
    ambient: list[str],
    suspected: list[tuple[Path, str]],
) -> UsageSignal:
    """文本层指向「可删」时的最后一关：有没有**疑似**副作用没结清。

    有就退成「无法判定」而不是给出推荐——文本层看不清那句宏调用展开了什么，
    而 verify 的汇编等价闸门看得清。这不是放宽，是把结论从「文本猜的」换成
    「编译器验的」：闸门默认在 verify 里开着，且对携带
    ``side_effect_suspected`` 的候选强制开启。
    """
    if suspected:
        return UsageSignal(
            verdict=UsageVerdict.UNKNOWN,
            header_identifier_count=total,
            provided_by=suspected[0][0],
            suspected_side_effects=tuple(text for _path, text in suspected[:3]),
            cross_provided=tuple(sorted(cross)[:8]),
            self_provided=tuple(sorted(self_hits)[:8]),
            unprovided=tuple(sorted(ambient)[:8]),
        )
    return UsageSignal(
        verdict=UsageVerdict.PLAUSIBLY_UNUSED,
        shared_identifiers=tuple(sorted(cross)[:8]),
        header_identifier_count=total,
        cross_provided=tuple(sorted(cross)[:8]),
        self_provided=tuple(sorted(self_hits)[:8]),
        unprovided=tuple(sorted(ambient)[:8]),
    )
