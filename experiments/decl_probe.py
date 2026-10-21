"""「这个头到底声明了什么」——按声明作用域取名字，而不是取正文里出现过的每个词。

问题
----
现在的 :func:`heavyheader.attrib.usage.identifiers_in` 把头文件正文里**出现过的
每个标识符**都当成「这个头提供的名字」。于是:

* ``void *malloc(size_t size);`` 让 ``size`` 成了 ``<stdlib.h>`` 的「声明」——
  zlib 的 ``zutil.h`` 因此被判「在用 size」，闸门漏掉了一条经复测省时 2.125%
  的改动（``experiments/results/g2-recall-off-zlib-20261018.json``）。
* ``class basic_string { ... at(...) ... }`` 让 ``at`` 成了 ``<string>`` 的
  「声明」——doctest 的 ``<unordered_set>`` 因此被判在用，一条真实收益被判丢。

两个方向的错误同一个根因：**参数名、成员名、局部变量名都不是这个头提供的符号**。

判据
----
只在「声明作用域」里取名字:

* 括号深度为 0（排除参数名与实参）;
* 大括号栈里没有类体/函数体（排除成员名、局部变量名）;
* 预处理指令行整体跳过（排除宏体里的 ``size`` 这类形参）;
* ``#define`` 的名字单独收集：宏在任何深度都是宏。

大括号的分类看**上下文**而不是「谁打开的」：宏包里常见 ``_BEGIN_C_DECLS {``
这种包裹，把它当类体会让整个头一个名字都取不到。

本文件先做测量原型，验证判据在真实头文件上是否站得住，再决定要不要搬进产品。
"""

from __future__ import annotations

import re
from pathlib import Path

_IGNORED = set(
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
    """.split()
)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|::|->|&&|\|\||[{}\[\]();<>]")
_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)")
_EXTERN_C = re.compile(r'extern\s*"C"')
_CLASS_LIKE = re.compile(r"\b(?:class|struct|union|enum)\b")
_NAMESPACE_LIKE = re.compile(r"\bnamespace\b|__extern_c")


def _prepare(text: str) -> tuple[str, set[str]]:
    """抹掉注释/字面量/预处理指令行，返回 (待扫描正文, 宏名集合)。"""
    macros: set[str] = set()
    kept: list[str] = []
    in_block = False
    in_directive = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            in_directive = line.rstrip().endswith("\\")
            match = _DEFINE.match(line)
            if match:
                macros.add(match.group(1))
            kept.append(" " * len(line))
            continue
        if in_directive:
            in_directive = line.rstrip().endswith("\\")
            kept.append(" " * len(line))
            continue

        # extern "C" 必须先保住：下一步会把字符串字面量抹掉。
        line = _EXTERN_C.sub(" __extern_c ", line)
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
                break
            char = line[index]
            if char in "\"'":
                closing = line.find(char, index + 1)
                if closing == -1:
                    break
                result.append(" " * (closing + 1 - index))
                index = closing + 1
                continue
            result.append(char)
            index += 1
        kept.append("".join(result))
    return "\n".join(kept), macros


def _brace_kind(head: str) -> str:
    """把 ``{`` 归类为 ``ns``（透明）或 ``block``（遮挡名字）。

    透明：namespace、``extern "C"``、宏包裹（``_BEGIN_C_DECLS`` 之类）、
    初始化列表。遮挡：类体/枚举体，以及函数体（``{`` 前紧挨右括号）。
    判反了的代价不对称：把类体当透明只是多收几个名字（回到现状），
    把宏包裹当遮挡会让整个头一个名字都收不到。
    """
    if _NAMESPACE_LIKE.search(head):
        return "ns"
    if _CLASS_LIKE.search(head):
        return "block"
    if head.rstrip().endswith(")"):
        return "block"
    return "ns"


def declared_names(text: str) -> set[str]:
    """头文件在**声明作用域**里出现的名字。

    典型取舍：
    ``extern void *malloc(size_t __size);`` → ``malloc``（``__size`` 在括号里）
    ``class foo { int at(int); };``         → ``foo``（``at`` 在类体里）
    ``#define ZLIB_VERSION "1.3"``          → ``ZLIB_VERSION``（宏永远算）
    """
    cleaned, macros = _prepare(text)
    names: set[str] = set(macros)

    stack: list[str] = []
    paren = 0
    # 一条语句的起点。判断「这个 ``{`` 是什么」必须看整条语句，而不是当前行：
    # libstdc++ 里 ``class basic_string`` 与 ``{`` 各占一行，只看行首会把它
    # 当成透明的 namespace，类体里 900 多个成员名就全被算成「这个头声明的」。
    stmt_start = 0

    for match in _TOKEN.finditer(cleaned):
        token = match.group(0)
        if token == "(":
            paren += 1
            continue
        if token == ")":
            paren = max(0, paren - 1)
            continue
        if token == "{":
            stack.append(_brace_kind(cleaned[stmt_start : match.start()]))
            stmt_start = match.end()
            continue
        if token == "}":
            if stack:
                stack.pop()
            stmt_start = match.end()
            continue
        if token in (";", "::", "->", "&&", "||", "[", "]", "<", ">"):
            if token == ";":
                stmt_start = match.end()
            continue
        if paren == 0 and all(level == "ns" for level in stack):
            if token in _IGNORED or len(token) < 2:
                continue
            names.add(token)

    return names


def compare(header: Path, probes: dict[str, bool]) -> list[tuple[str, bool, bool]]:
    from heavyheader.attrib.usage import identifiers_in

    text = header.read_text(encoding="utf-8", errors="replace")
    old = identifiers_in(text)
    new = declared_names(text)
    return [(name, name in old, name in new) for name in probes]


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    cases = [
        (
            "C:/mingw64/x86_64-w64-mingw32/include/stdlib.h",
            {"malloc": True, "free": True, "abort": True, "size": False, "at": False},
        ),
        (
            "C:/mingw64/x86_64-w64-mingw32/include/malloc.h",
            {"malloc": True, "_malloca": True, "size": False},
        ),
        (
            "C:/Program Files/LLVM/lib/clang/23/include/limits.h",
            {"UINT_MAX": True, "size": False},
        ),
    ]
    for path_text, probes in cases:
        path = Path(path_text)
        if not path.is_file():
            print(f"skip (missing): {path}")
            continue
        print(f"\n{path.name}")
        for name, in_old, in_new in compare(path, probes):
            expected = probes[name]
            flag = "OK" if in_new == expected else "**MISMATCH**"
            print(f"  {name:16s} 现状={in_old!s:5s} 声明级={in_new!s:5s} 期望={expected!s:5s} {flag}")
