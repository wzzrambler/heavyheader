"""把编译器的「删了编不过」翻译成结构化的阻断原因。

删掉一条 include 后编译失败，绝大多数工具到这里就结束了：记一句
``compile-error``，然后把候选标成「保留」。但对使用者来说，这句话没回答
最重要的问题——**是什么挡住了，有没有别的办法**。

同一个失败至少有四种完全不同的含义：

* 缺一个**类型声明**，而源码只把它当指针/引用用 → 换成前向声明就能删掉
  （IWYU 与 YALLA 都在这条路上，YALLA 实测过 24.5× 的局部收益）；
* 缺一个类型**定义**（按值使用、访问成员、继承、sizeof） → 前向声明救不了；
* 缺一个**宏或函数/枚举** → 前向声明在语言上不成立；
* 报错根本不是「缺声明」（语法、歧义、模板实例化） → 与删除无关。

本模块只做**归因**：解析报错、取出符号与位置、判定它属于哪一类。它不下
「应该怎么改」的最终结论——那一步只能靠真的改一遍再编译（``verify`` 的
职责），这也是本项目一贯的分工：归因给假设，实测给结论。
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import Path

from .forward import declared_types, pointer_only_usage

#: 编译器报错里的「符号缺失」句式。顺序即优先级：越具体的越先匹配。
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"'([^']+)' was not declared in this scope"), "missing"),
    (re.compile(r"'([^']+)' does not name a type"), "missing"),
    (re.compile(r"'([^']+)' has not been declared"), "missing"),
    (re.compile(r"unknown type name '([^']+)'"), "missing"),
    (re.compile(r"use of undeclared identifier '([^']+)'"), "missing"),
    (re.compile(r"incomplete type '([^']+)' used"), "incomplete"),
    (re.compile(r"'([^']+)' is not a member of"), "member"),
    (re.compile(r"error C2065: '([^']+)'"), "missing"),
    (re.compile(r"error C2653: '([^']+)'"), "missing"),
    (re.compile(r"error C2027: use of undefined type '([^']+)'"), "incomplete"),
    (re.compile(r"error C2079: '([^']+)' uses undefined"), "incomplete"),
)

#: ``path:line:col: error:``（GCC/Clang）或 ``file(line): error C2065:``（MSVC）。
#: **不用 ``^`` 锚定**：调用方给的消息可能带前缀（``移除后编译失败：``），
#: 也可能整条是 stderr 的若干行拼接。锚死在这里会让位置信息全部丢失。
_LOCATION_GCC = re.compile(r"(?P<file>[^\s:][^:\n]*?):(?P<line>\d+):(?:\d+:)?\s*(?:fatal )?error:")
_LOCATION_MSVC = re.compile(r"(?P<file>[^\s(][^(\n]*?)\((?P<line>\d+)\)\s*:\s*(?:fatal )?error")

#: 报告里不该出现临时目录：``…/heavyheader-probe-xxx/util.cpp`` → ``util.cpp``。
#: 至少两级分隔符才算路径，避免把 ``a.cpp`` 或 ``error:`` 误伤。
_PATH_TOKEN = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\s:;'\"()]+){2,}")
_FAILED_PREFIX = re.compile(r"^(?:移除后编译失败|编译失败)[：:]\s*")


class BlockerKind(str, enum.Enum):
    """缺失的是哪一类东西——决定「还有没有别的办法」。"""

    MISSING = "missing"
    INCOMPLETE = "incomplete"
    MEMBER = "member"
    UNKNOWN = "unknown"


class Remedy(str, enum.Enum):
    """归因给出的处置方向（不是结论，结论仍要编译验证）。"""

    #: 符号是类型且只以指针/引用出现 → 前向声明可能成立
    FORWARD_DECLARE = "forward-declare"
    #: 符号是类型，但需要完整定义 → 前向声明不成立
    NEEDS_DEFINITION = "needs-definition"
    #: 符号不是该头声明的类型（宏、函数、枚举、或间接引入） → 前向声明不适用
    NOT_A_TYPE = "not-a-type"
    #: 看不出是缺声明（语法错误、模板实例化等）
    UNRELATED = "unrelated"


@dataclass(frozen=True, slots=True)
class Blocker:
    """一条失败里的首个阻断符号。"""

    symbol: str
    kind: BlockerKind
    file: str = ""
    line: int | None = None
    raw: str = ""

    def describe(self) -> str:
        where = f"{Path(self.file).name}:{self.line}" if self.line else ""
        head = f"阻断符号 `{self.symbol}`"
        if where:
            head += f"（{where}）"
        return head


@dataclass(frozen=True, slots=True)
class Attribution:
    """阻断归因：符号 + 处置方向 + 一句话理由。"""

    blocker: Blocker | None
    remedy: Remedy
    detail: str

    def sentence(self) -> str:
        if self.blocker is None:
            return f"编译失败与缺失声明无关：{self.detail}"
        return f"{self.blocker.describe()}——{self.detail}"


def clean_error(text: str) -> str:
    """把 verify 拼出来的消息整理成可读、可解析的一行。

    做三件事：去掉「移除后编译失败：」这类重复前缀；把临时目录路径压成文件名
    （``…/heavyheader-probe-xxxx/util.cpp`` → ``util.cpp``）；压缩空白。
    """
    cleaned = _FAILED_PREFIX.sub("", str(text or ""))
    cleaned = _PATH_TOKEN.sub(lambda m: Path(m.group(0)).name, cleaned)
    return " ".join(cleaned.split())


def parse_blockers(stderr: str) -> list[Blocker]:
    """按出现顺序取出所有「符号缺失」类报错，同一符号只留第一次。"""
    found: list[Blocker] = []
    seen: set[tuple[str, BlockerKind]] = set()
    file = ""
    line: int | None = None
    for raw_line in (stderr or "").splitlines():
        text = raw_line.strip()
        if not text:
            continue
        located = _LOCATION_GCC.match(text) or _LOCATION_MSVC.match(text)
        if located:
            file = Path(located.group("file").strip()).name
            line = int(located.group("line"))
            text = text[located.end() :].strip()
        if "error" not in raw_line.lower():
            continue
        for pattern, kind_name in _PATTERNS:
            match = pattern.search(raw_line)
            if not match:
                continue
            kind = BlockerKind(kind_name)
            key = (match.group(1), kind)
            if key in seen:
                break
            seen.add(key)
            found.append(
                Blocker(
                    symbol=match.group(1),
                    kind=kind,
                    file=file,
                    line=line,
                    raw=raw_line.strip()[:200],
                )
            )
            break
    return found


def attribute(
    stderr: str,
    header: Path | None = None,
    source_text: str | None = None,
    *,
    extra_headers: tuple[Path, ...] | list[Path] = (),
    system_header: bool = False,
) -> Attribution:
    """把一段编译错误归因成「缺什么 + 有没有别的办法」。

    判定顺序刻意保守：先确认符号确实是**这个头声明的类型**，再看源码里对它的
    用法。任何一步不成立就说「前向声明不适用」，宁可少给一条建议，也不给一条
    编译不过的建议。
    """
    blockers = parse_blockers(stderr)
    if not blockers:
        detail = " ".join((stderr or "").split())[:120] or "（编译器没有给出可解析的错误）"
        return Attribution(blocker=None, remedy=Remedy.UNRELATED, detail=detail)

    first = blockers[0]
    if first.kind is BlockerKind.MEMBER:
        return Attribution(
            blocker=first,
            remedy=Remedy.NEEDS_DEFINITION,
            detail="访问了某个类型的成员，需要完整定义，前向声明不成立",
        )
    if first.kind is BlockerKind.INCOMPLETE:
        return Attribution(
            blocker=first,
            remedy=Remedy.NEEDS_DEFINITION,
            detail="该处需要完整类型（按值使用/继承/sizeof），前向声明不成立",
        )

    # 系统/第三方头里的类型（std::string 之类）不允许使用者前置声明，
    # 语言规则上就不成立，先挡住——比给出一个编译不过的建议好。
    if system_header:
        return Attribution(
            blocker=first,
            remedy=Remedy.NOT_A_TYPE,
            detail="该头是系统/第三方头；C++ 不允许用户前置声明标准库成员，"
            "前向声明不适用于这类符号",
        )

    # 符号可能来自被删头**传递闭包里的**某个头，不只是它自己那几行。
    # 只扫被删的那个文件会把 std::string 之类误判成「不是类型」。
    candidates = [header] if header is not None else []
    candidates.extend(extra_headers)
    types: list[str] = []
    for path in candidates:
        if path is not None and Path(path).is_file():
            types.extend(declared_types(Path(path)))
    if first.symbol not in types:
        return Attribution(
            blocker=first,
            remedy=Remedy.NOT_A_TYPE,
            detail="这个符号不是被删 include 提供（含其传递闭包）的类型——宏、函数、"
            "枚举或宏展开的结果，前向声明不适用",
        )

    if source_text is None:
        return Attribution(
            blocker=first,
            remedy=Remedy.NEEDS_DEFINITION,
            detail="它是该头声明的类型；未读到源码，无法判断用法",
        )
    if pointer_only_usage(source_text, first.symbol):
        return Attribution(
            blocker=first,
            remedy=Remedy.FORWARD_DECLARE,
            detail="该类型在源码里只以指针/引用出现，换成前向声明可能就删得掉",
        )
    return Attribution(
        blocker=first,
        remedy=Remedy.NEEDS_DEFINITION,
        detail="该类型在源码里以需要完整定义的方式使用，前向声明不成立",
    )
