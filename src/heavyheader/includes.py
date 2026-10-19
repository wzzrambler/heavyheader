"""在源码里定位某条 ``#include`` 的**行号**。

为什么需要单独一层：编译器不给我们行号。GCC / Clang 的 ``-H`` 只有缩进与路径
（``. /usr/include/stdio.h``），MSVC 的 ``/showIncludes`` 同样只有层级。也就是说
「这个头在第几行被包含」这件事，编译器从来没说过——扫描期因此把每条候选都标成
「行号未知」，报告里读者无法定位到具体那一行。

于是只能自己从源码里找。定位必须**保守**：同名头（``util/thing.h`` 与
``other/thing.h``）同时存在时，随便挑一行会让人删掉另一个文件，而结论看起来
完全可信。所以这里分两级匹配，并且把「有歧义」当成一个明确结果返回，让调用方
自己决定是报错还是拒绝。

这一层不认识预处理器：不展开宏、不求值条件编译。它只把 ``#include`` 的文本当
路径后缀来比。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

__all__ = [
    "BOM",
    "INCLUDE_RE",
    "strip_bom",
    "strip_comments",
    "path_matches",
    "find_include_lines",
    "find_include_line",
]

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*(?P<delim>[<"])(?P<target>[^>"]+)[>"]')

#: UTF-8 BOM。Windows 编辑器（VS、Notepad、vcpkg 抓下来的一批文件）默认会给
#: 源码加它。它不是 ``\s``，所以 ``^\s*#\s*include`` 匹配不到带 BOM 的首行——
#: 一个真实工程的第一个 include 就这样被判成「找不到该注释哪一行」。
_BOM = "\ufeff"
#: 公开别名：调用方需要「去掉 BOM 但不动行数」时直接用它。
BOM = _BOM


def strip_bom(text: str) -> str:
    """去掉行首 BOM，**不改变行数**。

    行号是定位 include 的唯一坐标，删掉首行会让后面所有行号错一位。
    """
    return text[len(_BOM) :] if text.startswith(_BOM) else text


def strip_comments(source: str) -> str:
    """把注释替换成等长空白，保持行结构不变。

    行数必须不变，否则编译器报的行号就对不上我们编辑的文件。只处理整行与行尾
    注释——字符串里的 ``//`` 会让完整词法分析器误判，但它出现在 ``#include``
    行上的概率可以忽略。
    """
    out: list[str] = []
    in_block = False
    for line in source.splitlines():
        if not in_block and "/*" not in line and "//" not in line:
            out.append(line)
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
            result.append(line[index])
            index += 1
        out.append("".join(result))
    return "\n".join(out) + "\n"


def normalise(text: str) -> str:
    """统一路径分隔符、去掉 ``./`` 前缀，便于比较。"""
    return text.strip().replace("\\", "/").lstrip("./")


def path_matches(target: str, header: Path) -> bool:
    """``#include`` 的文本是否指向这个头文件（强匹配）。

    比只比文件名强得多：``#include "other/thing.h"`` 的文本是路径，与候选头的
    完整路径做后缀比较即可，于是 ``util/thing.h`` 与 ``other/thing.h`` 能被区分。
    """
    wanted = str(header).replace("\\", "/")
    target_norm = normalise(target)
    return bool(target_norm) and (
        wanted == target_norm or wanted.endswith("/" + target_norm)
    )


def _match_lines(
    source_text: str, predicate: Callable[[str], bool]
) -> list[int]:
    return [
        number
        for number, line in enumerate(
            strip_comments(strip_bom(source_text)).splitlines(), start=1
        )
        if (match := INCLUDE_RE.match(line)) and predicate(match.group("target"))
    ]


def find_include_lines(source_text: str, header: Path) -> list[int]:
    """每条 ``#include`` 指向 ``header`` 的 1-based 行号。

    两级匹配：先用完整路径后缀（强），只有强匹配定不下来时才退回文件名（弱）。
    返回**全部**候选行，由调用方决定——同名冲突时随便挑一行，删掉的可能是另一个
    文件，而结论看起来完全可信。
    """
    strong = _match_lines(source_text, lambda target: path_matches(target, header))
    if strong:
        return strong
    return _match_lines(source_text, lambda target: Path(target).name == header.name)


def find_include_line(source_text: str, header: Path) -> int | None:
    """唯一的那个行号；有歧义或找不到时返回 ``None``（**不猜**）。"""
    matches = find_include_lines(source_text, header)
    return matches[0] if len(matches) == 1 else None
