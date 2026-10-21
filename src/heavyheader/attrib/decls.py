"""What a header *declares* -- as opposed to which words appear in its text.

Why this module exists
----------------------
:func:`heavyheader.attrib.usage.identifiers_in` treats every token in a header
as a name that header provides.  Two measured failures come from that:

* ``void *malloc(size_t size);`` made ``size`` look like something
  ``<stdlib.h>`` declares.  zlib's ``zutil.h`` was therefore judged "in use"
  and the feasibility gate blocked ``zutil.h -> <stdlib.h>`` -- a change that
  was later verified to save **2.125%** (9/9 units still compiled).  A
  parameter name is not a symbol.
* ``class basic_string { ... at(...) ... }`` made ``at`` look like something
  ``<string>`` declares.  doctest's ``prelude.h -> <unordered_set>`` was
  blocked by a *consumer* file that mentions ``at`` -- a member name of a
  different class in a different header.

Both directions have one root cause: member names, parameter names and local
variables are not provided by the header they are written in.  A name only
counts if it appears in **declaration scope**:

* parenthesis depth 0 -- excludes parameter names and call arguments;
* no enclosing class body or function body -- excludes members and locals;
* preprocessor lines skipped -- excludes macro *parameters*, while
  ``#define NAME`` still counts, because a macro is a declaration wherever it
  sits.

Why the brace classification looks at the whole statement
--------------------------------------------------------
libstdc++ splits the class head and its opening brace across two lines::

    class basic_string
    {

Classifying a bare ``{`` on its own line defaults to "transparent" (the safe
direction), which would let all 900 members of ``basic_string`` through.  The
probe measured exactly that: ``at``/``push_back`` survived until the
classification was changed to look from the last ``;``/``{``/``}`` instead of
from the start of the line.  The name set then drops to **4-17%** of the token
set on real libstdc++ headers, and the members disappear.

The asymmetry is deliberate: guessing "transparent" when unsure only costs a
few extra names (back to the old behaviour), while guessing "opaque" on a
macro wrapper such as ``_BEGIN_C_DECLS {`` makes the whole header declare
nothing -- and a header that declares nothing looks *unused*, which is the
dangerous failure direction.
"""

from __future__ import annotations

import re

__all__ = ["declared_names", "IGNORED", "BUILTIN"]

# 不携带「这个头提供了什么」信息的词：语言关键字与预处理噪声。
IGNORED = frozenset(
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

# 编译器内建：这些名字**永远不由某个头文件提供**，而是编译器预定义，或者是
# 语言扩展关键字。把它们算作「使用证据」会造成系统性假阴性。
#
# 实测来源：zlib 的 ``zutil.h -> <stdlib.h>`` 曾被判「正在使用」，证据是
# ``_MSC_VER``、``__STDC_VERSION__``、``__attribute__``。判据是「删掉包含它的
# 头之后，这个标识符还会不会存在」：编译器预定义的一定还在，所以不能算依赖。
BUILTIN = frozenset(
    """
    __GNUC__ __GNUC_MINOR__ __GNUC_PATCHLEVEL__ __clang__ __clang_major__
    _MSC_VER _MSC_FULL_VER __STDC__ __STDC_VERSION__ __STDC_HOSTED__
    __cplusplus __OBJC__ __ASSEMBLER__
    __attribute__ __attribute __declspec __extension__ __inline __restrict
    __asm__ __volatile__ __forceinline __thread __typeof__ typeof
    """.split()
)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|::|->|&&|\|\||[{}\[\]();<>]")
_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)")
_EXTERN_C = re.compile(r'extern\s*"C"')
_CLASS_LIKE = re.compile(r"\b(?:class|struct|union|enum)\b")
_NAMESPACE_LIKE = re.compile(r"\bnamespace\b|__extern_c")


def _prepare(text: str) -> tuple[str, set[str]]:
    """Blank comments, literals and preprocessor lines; collect macro names.

    ``extern "C"`` is rewritten to a placeholder *before* string literals are
    blanked, otherwise the linkage block becomes an opaque brace and every
    declaration inside it disappears (measured: ``free`` and ``abort``
    vanished from ``<stdlib.h>``).

    Continuation lines of a ``#define`` are blanked too: the body of a macro
    like ``#define _malloca(size) ...`` contains bare parameter names at
    parenthesis depth 0, and those were the original source of ``size``.
    """
    macros: set[str] = set()
    kept: list[str] = []
    in_block = False
    in_directive = False
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
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
    """Classify a ``{`` as ``ns`` (transparent) or ``block`` (hides names).

    Transparent: namespaces, ``extern "C"``, macro wrappers such as
    ``_BEGIN_C_DECLS``, initialiser lists.  Opaque: class/enum bodies and
    function bodies (``{`` immediately after a closing parenthesis).
    """
    if _NAMESPACE_LIKE.search(head):
        return "ns"
    if _CLASS_LIKE.search(head):
        return "block"
    if head.rstrip().endswith(")"):
        return "block"
    return "ns"


def declared_names(text: str) -> set[str]:
    """The names a header provides.

    ``extern void *malloc(size_t __size);`` -> ``malloc``
    ``class foo { int at(int); };``         -> ``foo``
    ``#define ZLIB_VERSION "1.3"``          -> ``ZLIB_VERSION``
    """
    cleaned, macros = _prepare(text)
    names: set[str] = set(macros)

    stack: list[str] = []
    paren = 0
    # Where the current statement started.  The brace classification needs the
    # whole statement, because the class head and its ``{`` are often on
    # different lines.
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
        if token == ";":
            stmt_start = match.end()
            continue
        if token in ("::", "->", "&&", "||", "[", "]", "<", ">"):
            continue
        if paren == 0 and all(level == "ns" for level in stack):
            if token in IGNORED or token in BUILTIN or len(token) < 2:
                continue
            names.add(token)

    return names
