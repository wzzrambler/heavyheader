"""「第二意见」工具的共享对位口径。

**为什么单独一个模块**：include-what-you-use 与 clang-include-cleaner 是两个
不同后端（前者判正确性、后者同样判正确性，本工具判成本），但它们进到本工具
之后走的是同一条路——**外部工具说这条 include 该删，我们这一侧判什么**。

如果每个后端各写一份对位，口径漂移只是时间问题：同一个候选在 IWYU 报告里
算「两边一致」，在 include-cleaner 报告里算「分歧」，读者没法判断这是两个
工具的真实差别，还是我们某一侧的匹配写歪了。数字一旦不可比，合并两次之
举本身就失去意义。所以这份口径只写一遍，两个后端共用。

**职责边界**：这里只做「对得上/对不上」与「我们是否也指向删」两件事，
不解释任何一家的输出格式——那属于各自后端模块。
"""

from __future__ import annotations

from pathlib import Path

from ..models import AdviceVerdict

__all__ = [
    "MATCHED_BY_LINE",
    "MATCHED_BY_NAME",
    "NOT_MATCHED",
    "basename_of",
    "group_by_tu",
    "match_candidate",
    "points_to_removal",
    "verdict_of",
]

#: ``matched_by`` 的三种取值。写成常量而不是散落在各处的字符串字面量：
#: 报告会直接把这个值印出来给人看，拼错一个字就是一句看不懂的话。
MATCHED_BY_LINE = "行号"
MATCHED_BY_NAME = "文件名"
NOT_MATCHED = "—"


def basename_of(path) -> str:
    """取文件名。

    对位按 basename 做，因为两边给的路径基准不同：编译数据库里是绝对
    路径，工具输出里可能是相对路径。
    """
    return Path(str(path)).name


def group_by_tu(candidates: list) -> dict[str, list]:
    """按编译单元（basename）分组候选，排除「头里的 include 点」。

    排除 ``is_header_site`` 的理由：外部工具只分析源文件（``.cpp``），
    它的建议永远指向某个 TU 里写的那一行 include；头内部的 include 点
    不在它的输出里，留着只会制造「我们对上了但其实是同名巧合」的假阳性。
    """
    grouped: dict[str, list] = {}
    for candidate in candidates:
        if candidate.is_header_site:
            continue
        grouped.setdefault(basename_of(candidate.tu), []).append(candidate)
    return grouped


def match_candidate(candidates: list, name: str, lines: tuple[int, int] | None = None):
    """在候选里找与「外部工具说要删的这条 include」对应的那一个。

    返回 ``(候选 | None, 匹配方式)``。

    **优先用行号**，行号对不上才退回文件名。这不是偏好问题：按文件名匹配
    是模糊的，同名不同目录会串（``util.h`` 这种名字几乎每个项目都有），
    而一份「我们对上了」的结论一旦错了，读者会以为删它是免费的。退回
    文件名时把 ``matched_by`` 记下来，读者有权知道他是被哪种口径给的结论。
    """
    if lines is not None:
        start, end = lines
        for candidate in candidates:
            if candidate.line is not None and start <= candidate.line <= end:
                return candidate, MATCHED_BY_LINE
    target = basename_of(name)
    for candidate in candidates:
        if basename_of(candidate.header) == target:
            return candidate, MATCHED_BY_NAME
    return None, NOT_MATCHED


def verdict_of(candidate) -> str | None:
    """本工具这一侧的结论；``None`` 表示这一轮没跑 ``--probe``（未实测）。

    ``None`` 与「判定为 KEEP」必须区分开：前者是「不知道」，后者是「测过
    了，删不得」。混同会把没有证据的地方渲染成有证据的结论。
    """
    if candidate is None:
        return None
    advice = getattr(candidate, "advice", None)
    return advice.verdict.value if advice is not None else None


def points_to_removal(candidate) -> bool:
    """本工具这一侧是否也指向「删」。

    ``advice`` 在时以它为准（三态判定，是本工具的正式结论）；没有
    ``advice``（这一轮没跑 ``--probe``）时退到静态的「疑似未使用」，
    报告里会说明这是静态口径而不是实测结论。
    """
    advice = getattr(candidate, "advice", None)
    if advice is not None:
        return advice.verdict is AdviceVerdict.REMOVE
    return candidate.usage_label == "疑似未使用"
