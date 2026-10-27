"""把 include-what-you-use 的输出与本工具的扫描结果对上。

**分工**：IWYU 判**正确性**（这条 include 该不该出现，AST 级），本工具判**成本**
（它让你多等几秒、删了会不会出事）。两边都主张删的，才是「既站得住、又能省」
的那几条——这是合并的唯一目的，不是为了比谁更准。

**格式出处**：下面的正则取自 IWYU 自己的 ``fix_includes.py``（master 分支），
不是照输出猜的。该脚本就是解析这份输出的官方实现，与它对齐意味着
IWYU 改格式时我们至少和官方脚本同步失效，而不是各自漂移。

**已知边界（必须在报告里说）**：本项目构建不出 IWYU 二进制（需按 LLVM 版本
源码构建），因此这份解析**没有经过端到端实测**——它验证于按官方格式构造的
样本。端到端跑通之前，合并结论只可作为线索。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import AdviceVerdict, ProjectScan
from .crosscheck import (
    basename_of,
    group_by_tu,
    match_candidate,
    points_to_removal,
    verdict_of,
)

__all__ = [
    "IwyuComparison",
    "IwyuPair",
    "IwyuRecord",
    "IwyuSuggestion",
    "compare_with_scan",
    "parse_iwyu_log",
]

# --- 段头正则：逐条对应 fix_includes.py 的 IWYUOutputParser -----------------
#
# _ADD_SECTION_RE / _REMOVE_SECTION_RE / _TOTAL_SECTION_RE / _SECTION_END_RE /
# _NO_EDITS_RE。段头里那个 (.*) 是文件名。
_ADD_SECTION_RE = re.compile(r"^(.*) should add these lines:$")
_REMOVE_SECTION_RE = re.compile(r"^(.*) should remove these lines:$")
_TOTAL_SECTION_RE = re.compile(r"^The full include-list for (.*):$")
_SECTION_END_RE = re.compile(r"^---$")
_NO_EDITS_RE = re.compile(r"^\((.*) has correct #includes/fwd-decls\)$")

# 行号注释：``// lines 3-3``。fix_includes.py 记为闭区间 [start, end]。
_LINE_NUMBERS_COMMENT_RE = re.compile(r"\s*// lines ([0-9]+)-([0-9]+)")
# include 行。分组 1 是带 <> 或 "" 的文件名。
_INCLUDE_RE = re.compile(r"\s*#\s*include\s+([<\"][^\">]+[>\"])")
# 整行注释（add 段用：那里没有行号元数据要保留）。
_COMMENT_RE = re.compile(r"\s*//.*")

#: remove 段每行以 ``- `` 开头（fix_includes.py: "the 'remove' lines all
#: start with '- '"）。
_REMOVE_PREFIX = "- "

ADD = "add"
REMOVE = "remove"


@dataclass(slots=True)
class IwyuSuggestion:
    """IWYU 对一个源文件的一条建议。"""

    source: str
    action: str
    #: 去掉 ``- `` 前缀与行号注释后的正文；前向声明时是声明文本本身。
    text: str
    #: ``#include`` 行才有：``<vector>`` 或 ``"foo.h"``，含引号/尖括号。
    include: str | None
    #: ``// lines N-M`` 给出的闭区间；没有则 None。
    lines: tuple[int, int] | None

    @property
    def is_include(self) -> bool:
        return self.include is not None

    @property
    def include_name(self) -> str:
        """去掉 ``<>`` / 引号后的裸名字，用于按文件名匹配。"""
        return (self.include or "").strip("<>\"'")


@dataclass(slots=True)
class IwyuRecord:
    """IWYU 针对一个源文件的完整建议。"""

    source: str
    add: list[IwyuSuggestion] = field(default_factory=list)
    remove: list[IwyuSuggestion] = field(default_factory=list)
    #: ``The full include-list`` 段里的 include 裸名，IWYU 认为「最终该留哪些」。
    full_include_list: list[str] = field(default_factory=list)
    no_edits: bool = False
    #: 本记录实际出现过哪些段（``add`` / ``remove`` / ``total``）。
    sections: set[str] = field(default_factory=set)

    @property
    def is_complete(self) -> bool:
        """是否走完了「add → remove → total → ---」这一整套。

        残缺记录不能当完整记录用：构建日志被截断、IWYU 中途崩掉都会留下
        一个只有 add 段的记录，此时「IWYU 没建议删」是假的——它只是没说到
        那里。合并时必须分开处理，否则漏报会被读成「IWYU 认为没问题」。
        """
        return self.no_edits or "total" in self.sections


@dataclass(slots=True)
class IwyuPair:
    """一条 IWYU 删除建议与本工具对应候选的对位结果。"""

    source: str
    #: IWYU 给的 include 写法（``"foo.h"`` / ``<vector>``）。
    include: str
    #: 怎么对上的：行号一致 / 只按文件名 / 没对上。
    matched_by: str
    #: 本工具这一侧的结论；``None`` 表示这一轮没有跑 ``--probe``（未实测）。
    our_verdict: str | None
    our_usage_label: str | None
    estimated_lines_saved: int | None
    #: 我们这一侧是否也指向「删」。
    we_agree: bool
    #: 本工具这一侧是否存在对应候选。
    has_candidate: bool = False


@dataclass(slots=True)
class IwyuComparison:
    records: list[IwyuRecord] = field(default_factory=list)
    pairs: list[IwyuPair] = field(default_factory=list)
    #: 我们的候选里没有被任何 IWYU 建议覆盖到的条数。
    our_unmatched: int = 0
    #: 解析过程中跳过的、不属于任何段的日志行数（构建系统的噪音）。
    ignored_lines: int = 0
    #: 残缺记录条数——它们的「没建议删」不能当结论。
    incomplete_records: int = 0

    @property
    def iwyu_removals(self) -> int:
        return sum(1 for p in self.pairs if p.include)

    @property
    def agreement(self) -> int:
        return sum(1 for p in self.pairs if p.we_agree)

    @property
    def conflicts(self) -> int:
        """IWYU 说删、我们判 KEEP 的——值得人看一眼的分歧。"""
        return sum(
            1
            for p in self.pairs
            if p.our_verdict == AdviceVerdict.KEEP.value and not p.we_agree
        )

    def to_dict(self) -> dict:
        return {
            "records": len(self.records),
            "incomplete_records": self.incomplete_records,
            "iwyu_removal_suggestions": self.iwyu_removals,
            "agreed": self.agreement,
            "conflicts": self.conflicts,
            "our_unmatched_candidates": self.our_unmatched,
            "ignored_log_lines": self.ignored_lines,
            "pairs": [
                {
                    "source": p.source,
                    "include": p.include,
                    "matched_by": p.matched_by,
                    "has_candidate": p.has_candidate,
                    "our_verdict": p.our_verdict,
                    "estimated_lines_saved": p.estimated_lines_saved,
                    "we_agree": p.we_agree,
                }
                for p in self.pairs
            ],
        }


def parse_iwyu_log(text: str) -> tuple[list[IwyuRecord], int]:
    """解析 IWYU 的文本输出（通常来自 ``2> iwyu.out``）。

    返回 ``(记录列表, 被忽略的行数)``。忽略计数不是装饰：**一份日志若一行
    都没识别出来，它不是「0 条建议」，而是根本不是 IWYU 输出**——拿错文件
    跑出一张空表，比报错更容易让人误信。调用方必须检查这个数。

    与 ``fix_includes.py`` 的一处**有意不同**：它用严格状态机
    （add→remove→total→---），任一段缺失就报错；这里允许段缺失与乱序，
    因为真实构建日志里 IWYU 的输出会和编译器告警、构建系统回显交错。
    代价是可能把一份被截断的日志也解析出来——所以每条记录都带
    ``is_complete``，残缺的绝不冒充完整。
    """
    records: list[IwyuRecord] = []
    current: IwyuRecord | None = None
    section: str | None = None
    ignored = 0

    def close() -> None:
        nonlocal current, section
        if current is not None:
            records.append(current)
        current, section = None, None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        if _SECTION_END_RE.match(line):
            close()
            continue

        no_edits = _NO_EDITS_RE.match(line)
        if no_edits:
            close()
            current = IwyuRecord(source=no_edits.group(1).strip(), no_edits=True)
            records.append(current)
            current = None
            continue

        for name, pattern in (
            ("add", _ADD_SECTION_RE),
            ("remove", _REMOVE_SECTION_RE),
            ("total", _TOTAL_SECTION_RE),
        ):
            matched = pattern.match(line)
            if not matched:
                continue
            source = matched.group(1).strip()
            if current is None or current.source != source:
                close()
                current = IwyuRecord(source=source)
            section = name
            current.sections.add(name)
            break
        else:
            if current is None or section is None:
                ignored += 1
                continue
            _consume(current, section, line)

    close()
    return records, ignored


def _consume(record: IwyuRecord, section: str, line: str) -> None:
    """把一行归入当前段。"""
    body = line
    if section == REMOVE and body.startswith(_REMOVE_PREFIX):
        body = body[len(_REMOVE_PREFIX) :]

    lines: tuple[int, int] | None = None
    line_match = _LINE_NUMBERS_COMMENT_RE.search(body)
    if line_match:
        lines = (int(line_match.group(1)), int(line_match.group(2)))

    include_match = _INCLUDE_RE.match(body)
    include = include_match.group(1) if include_match else None

    if section == "total":
        if include:
            record.full_include_list.append(include.strip("<>\"'"))
        # total 段的前向声明行不参与成本合并，忽略即可。
        return

    clean = _COMMENT_RE.sub("", body).strip()
    if not clean:
        return

    target = record.add if section == "add" else record.remove
    target.append(
        IwyuSuggestion(
            source=record.source,
            action=section,
            text=clean,
            include=include,
            lines=lines,
        )
    )


def compare_with_scan(
    records: list[IwyuRecord],
    scan: ProjectScan,
    *,
    ignored_lines: int = 0,
) -> IwyuComparison:
    """把 IWYU 的删除建议与本工具的候选对上。

    匹配优先用**行号**（IWYU 给 ``// lines N-M``，我们的候选也记着行号），
    行号对不上才退回文件名（basename）。哪种都记在 ``matched_by`` 里——
    按文件名匹配是模糊的，同名不同目录会串，读者有权知道他是被哪种口径
    给的结论。
    """
    # 对位口径与 clang-include-cleaner 共用一份（attrib/crosscheck.py）：
    # 两个后端的「我们对上了」必须是同一个意思，否则数字不可比。
    candidates = [c for c in scan.candidates if not c.is_header_site]
    by_tu = group_by_tu(scan.candidates)

    comparison = IwyuComparison(records=list(records), ignored_lines=ignored_lines)
    comparison.incomplete_records = sum(1 for r in records if not r.is_complete)

    matched_candidates: set[int] = set()
    for record in records:
        ours = by_tu.get(basename_of(record.source), [])
        for suggestion in record.remove:
            if not suggestion.is_include:
                continue
            hit, how = match_candidate(ours, suggestion.include_name, suggestion.lines)
            if hit is not None:
                matched_candidates.add(id(hit))
            comparison.pairs.append(
                IwyuPair(
                    source=record.source,
                    include=suggestion.include or "",
                    matched_by=how,
                    has_candidate=hit is not None,
                    our_verdict=verdict_of(hit),
                    our_usage_label=getattr(hit, "usage_label", None) if hit else None,
                    estimated_lines_saved=(
                        hit.estimated_lines_saved if hit is not None else None
                    ),
                    we_agree=hit is not None and points_to_removal(hit),
                )
            )

    comparison.our_unmatched = sum(
        1 for c in candidates if id(c) not in matched_candidates
    )
    return comparison


# 对位口径已移到 attrib/crosscheck.py，由两个后端共用。下面这些名字保留
# 下来只是为了不让外部 import 突然断掉（它们曾经是这里的私有实现）。
_basename = basename_of
_match = match_candidate
_verdict = verdict_of
_points_to_removal = points_to_removal
