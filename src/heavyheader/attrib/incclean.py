"""把 clang-include-cleaner 的结论与本工具的扫描结果对上。

**分工**（与 ``iwyu.py`` 同一条原则）：clang-include-cleaner 判**正确性**
（这条 include 该不该出现，AST 级），本工具判**成本**（它让你多等几秒、
删了会不会出事）。两边都主张删的，才是既站得住又能省的那几条。

**与 IWYU 那条路的关键差别：这一条路做过端到端实测。** 本项目的
``attrib/iwyu.py`` 明确标注「构建不出 IWYU 二进制，解析验证于构造样本」；
clang-include-cleaner 则在本机 LLVM 23.1.3 的安装里就有现成二进制
（``C:\\Program Files\\LLVM\\bin``），所以这里的解析是从**真实进程输出**
校准的，不是照文档猜的。

下列三条行为是实测得来的，改动本模块前请先知道它们：

1. **一次只能分析一个文件。** 传多个源文件它会直接报
   ``-print requires a single input file`` 且不给任何结果。所以批量分析必须
   逐单元起进程，代价是 N 个进程而不是 1 个。
2. **编译失败时它跳过该文件并返回退出码 1。** 被跳过的文件不产出任何
   建议——这**绝不等于**「它认为这个文件没问题」。本模块把这种文件记成
   ``skipped=True``，并在对位与报告里单独计数。
   退出码非 0 只说明「失败了」，**说不出为什么**：1 至少覆盖「源文件有编译
   错误」「编译数据库加载失败」「一次传了多个源文件」三种情形，处理方式
   完全不同（第一种要先修好编译，第三种是本模块的调用约束）。所以要给出
   可执行的下一步，仍然得解析文本——这也是本模块存 ``skip_reason`` 与
   ``diagnostics`` 的原因。
   （校准记录：早期一度写成「退出码仍是 0」，那是被 ``| head`` 的管道退出码
   骗了——管道的退出码是最后一个命令的。已实测更正。）
3. **输出里不含文件名。** 单文件运行只给 ``- "lonely.h" @Line:5`` 这样的
   行，归属哪个源文件完全由调用方保证（本模块的做法是逐单元调用、
   每次调用就地标注 source）。

**格式**（LLVM 23.1.3，``--print=changes``）：每行一条变更，``- `` 开头为
删除、``+ `` 开头为插入；本地头写作 ``"foo.h"``、系统头写作 ``<vector>``；
删除行带 ``@Line:N``，插入行不带（插入位置由工具自己决定）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..models import AdviceVerdict, ProjectScan
from .crosscheck import (
    basename_of,
    group_by_tu,
    match_candidate,
    points_to_removal,
    verdict_of,
)

__all__ = [
    "REMOVE",
    "ADD",
    "IncludeCleanerComparison",
    "IncludeCleanerEdit",
    "IncludeCleanerRecord",
    "analyse_database",
    "compare_with_scan",
    "find_binary",
    "parse_include_cleaner_output",
    "run_on_unit",
]

REMOVE = "remove"
ADD = "add"

#: 一条变更。``- "lonely.h" @Line:5`` / ``+ <utility>``。
_CHANGE_RE = re.compile(
    r'^([-+])\s+(?:"([^"]*)"|<([^>]*)>)(?:\s+@Line:(\d+))?\s*$'
)

#: 编译错误导致整个文件被跳过。
_SKIP_RE = re.compile(r"^Skipping file\s+(.*?)\s+due to compiler errors\.?\s*$")
#: 处理失败的收尾行。
_PROCESSING_RE = re.compile(r"^Error while processing\s+(.*?)\.\s*$")
#: 一次传了多个源文件。
_TOO_MANY_RE = re.compile(r"^-print requires a single input file\s*$")
#: 编译器诊断行：``path:line:col: error: ...``。
_DIAG_RE = re.compile(r"^(?:fatal )?(?:error|warning):|^\S+:\d+:\d+:\s+(?:fatal )?error:")

#: 找不到二进制时先看 PATH，再看这些常见安装位置。
_CANDIDATE_DIRS = (
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "LLVM", "bin"),
    "/usr/lib/llvm-18/bin",
    "/usr/lib/llvm-19/bin",
    "/usr/local/opt/llvm/bin",
)


@dataclass(slots=True)
class IncludeCleanerEdit:
    """一条建议：在某个源文件的第几行删掉（或补上）哪个头。"""

    source: str
    action: str
    #: 原始写法，含引号或尖括号（``"foo.h"`` / ``<vector>``），照抄输出。
    spelling: str
    #: 去掉引号/尖括号的裸名，用于按文件名匹配。
    name: str
    #: 删除建议带的行号；插入建议为 None。
    line: int | None
    #: 对位时用的区间。单行就是 (line, line)，插入为 None。
    lines: tuple[int, int] | None = None

    @property
    def is_removal(self) -> bool:
        return self.action == REMOVE


@dataclass(slots=True)
class IncludeCleanerRecord:
    """一次 clang-include-cleaner 运行的产出（对应**一个**源文件）。

    ``skipped`` 是本模块最要紧的一个字段：它为 True 时，``removes`` 为空
    不代表工具认可了这些 include，而是工具**根本没分析**这个文件。
    """

    source: str
    removes: list[IncludeCleanerEdit] = field(default_factory=list)
    inserts: list[IncludeCleanerEdit] = field(default_factory=list)
    #: 因编译错误被跳过。
    skipped: bool = False
    skip_reason: str | None = None
    #: 输出里的编译器诊断行（保留前若干条，够定位问题即可）。
    diagnostics: list[str] = field(default_factory=list)
    #: 没认出来的输出行。一份输出若全是这类行，说明拿错了文件或换版本了。
    unrecognised: list[str] = field(default_factory=list)
    #: 进程退出码。**不能用来判断成败**：编译失败时它照样是 0。
    exit_code: int | None = None
    #: 调用失败（找不到二进制、超时、被杀）。
    error: str | None = None

    @property
    def analysed(self) -> bool:
        """是否真的分析过——既没被跳过，也没在调用层失败。"""
        return not self.skipped and self.error is None

    @property
    def is_empty(self) -> bool:
        return not self.removes and not self.inserts


@dataclass(slots=True)
class IncludeCleanerPair:
    source: str
    include: str
    matched_by: str
    #: 本工具这一侧的结论；``None`` 表示这一轮没跑 ``--probe``（未实测）。
    our_verdict: str | None
    our_usage_label: str | None
    estimated_lines_saved: int | None
    has_candidate: bool
    we_agree: bool


@dataclass(slots=True)
class IncludeCleanerComparison:
    records: list[IncludeCleanerRecord] = field(default_factory=list)
    pairs: list[IncludeCleanerPair] = field(default_factory=list)
    #: 我们的候选里没有被任何外部建议覆盖到的条数。
    our_unmatched: int = 0

    @property
    def analysed_files(self) -> int:
        return sum(1 for r in self.records if r.analysed)

    @property
    def skipped_files(self) -> int:
        """被跳过/调用失败的文件数。

        这些文件**没有参加对位**。把它和「对位后一致 0 条」放在一起看，
        才知道 0 是真的没有一致项，还是压根没分析几个文件。
        """
        return sum(1 for r in self.records if not r.analysed)

    @property
    def removals(self) -> int:
        return len(self.pairs)

    @property
    def agreement(self) -> int:
        return sum(1 for p in self.pairs if p.we_agree)

    @property
    def conflicts(self) -> int:
        """它说删、我们判 KEEP 的——值得人看一眼的分歧。"""
        return sum(
            1
            for p in self.pairs
            if p.our_verdict == AdviceVerdict.KEEP.value and not p.we_agree
        )

    def to_dict(self) -> dict:
        return {
            "files": len(self.records),
            "analysed_files": self.analysed_files,
            "skipped_files": self.skipped_files,
            "removal_suggestions": self.removals,
            "insert_suggestions": sum(len(r.inserts) for r in self.records),
            "agreed": self.agreement,
            "conflicts": self.conflicts,
            "our_unmatched_candidates": self.our_unmatched,
            "skipped_detail": [
                {
                    "source": r.source,
                    "reason": r.skip_reason or r.error,
                    "diagnostics": r.diagnostics[:3],
                }
                for r in self.records
                if not r.analysed
            ],
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


def find_binary(explicit: str | os.PathLike | None = None) -> Path | None:
    """定位 clang-include-cleaner。

    优先显式指定，其次 PATH，最后几个常见安装位置。找不到返回 ``None``——
    由调用方决定是报错还是降级，**不要在这里抛异常**：这个工具不是本项目的
    运行时依赖，缺它不该让 ``scan`` 之类的主路径挂掉。
    """
    if explicit:
        given = Path(explicit)
        if given.is_file():
            return given
        return None

    which = shutil.which("clang-include-cleaner") or shutil.which(
        "clang-include-cleaner.exe"
    )
    if which:
        return Path(which)

    name = "clang-include-cleaner.exe" if os.name == "nt" else "clang-include-cleaner"
    for directory in _CANDIDATE_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file():
            return candidate
    return None


def parse_include_cleaner_output(
    text: str, source: str = ""
) -> IncludeCleanerRecord:
    """解析一次 ``--print=changes`` 运行的输出。

    ``source`` 由调用方给：工具输出里没有文件名（见模块文档第 3 条），
    归属只能从外面带进来。

    返回**一条**记录——一次运行对应一个文件，这是该工具的硬约束。
    """
    record = IncludeCleanerRecord(source=source)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        change = _CHANGE_RE.match(line)
        if change:
            sign, quoted, angled, line_no = change.groups()
            name = quoted if quoted is not None else angled
            spelling = f'"{quoted}"' if quoted is not None else f"<{angled}>"
            number = int(line_no) if line_no else None
            target = record.removes if sign == "-" else record.inserts
            target.append(
                IncludeCleanerEdit(
                    source=source,
                    action=REMOVE if sign == "-" else ADD,
                    spelling=spelling,
                    name=name or "",
                    line=number,
                    lines=(number, number) if number is not None else None,
                )
            )
            continue

        if _SKIP_RE.match(line) or _TOO_MANY_RE.match(line) or _PROCESSING_RE.match(line):
            record.skipped = True
            record.skip_reason = (
                record.skip_reason or line
            )
            continue

        if _DIAG_RE.search(line):
            if len(record.diagnostics) < 5:
                record.diagnostics.append(line)
            # 诊断行出现往往伴随跳过；但跳过与否以 Skipping 那行为准，
            # 这里不直接置 skipped，避免把 warning 也当成失败。
            continue

        record.unrecognised.append(line)

    return record


def run_on_unit(
    binary: str | os.PathLike,
    source: str | os.PathLike,
    *,
    database: str | os.PathLike | None = None,
    directory: str | os.PathLike | None = None,
    extra_args: tuple[str, ...] = (),
    timeout: float | None = 300.0,
) -> IncludeCleanerRecord:
    """对**一个**源文件跑一次 clang-include-cleaner。

    有编译数据库时走 ``-p``（工具自己从库里取该单元的编译参数，最省心）；
    否则靠 ``directory`` + ``extra_args`` 手工给。

    无论走哪条路，一次只传一个源文件——传多了工具直接拒绝（见模块文档第 1 条）。

    **路径基准的坑（踩过）**：``source`` 是相对路径时，它相对的是 ``directory``
    （即子进程 cwd），**不是**当前进程的工作目录。两者不一致时会得到
    ``error: no such file or directory``，而退出码仍然是 1、没有任何变更输出——
    一眼看去很像「这个 TU 没有可删的 include」。传绝对路径最省事，
    ``analyse_database`` 走的就是这条路。
    """
    source_path = Path(source)
    command = [str(binary), "--print=changes"]
    if database is not None:
        command += ["-p", str(database)]
    command.append(str(source_path))
    if extra_args:
        command += ["--", *extra_args]

    record = IncludeCleanerRecord(source=str(source_path))
    try:
        done = subprocess.run(
            command,
            cwd=str(directory) if directory is not None else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except OSError as exc:
        record.error = f"调用失败：{exc}"
        return record
    except subprocess.TimeoutExpired:
        record.error = f"超时（>{timeout:.0f}s）"
        return record

    parsed = parse_include_cleaner_output(
        (done.stdout or "") + "\n" + (done.stderr or ""), source=str(source_path)
    )
    parsed.exit_code = done.returncode
    return parsed


def analyse_database(
    database: str | os.PathLike,
    *,
    binary: str | os.PathLike | None = None,
    jobs: int = 1,
    timeout: float | None = 300.0,
    limit: int | None = None,
) -> list[IncludeCleanerRecord]:
    """对 ``compile_commands.json`` 里的每个单元各跑一次，收齐记录。

    ``jobs`` 默认 1：这个工具每个进程都要做一遍完整 AST 分析，内存不便宜，
    默认串行是对的；调用方明确知道自己在做什么时再调大。

    单元顺序按数据库原序，不是按完成顺序——结果要能和扫描报告的单元顺序
    对齐才便于核对。
    """
    resolved = find_binary(binary)
    if resolved is None:
        raise FileNotFoundError("找不到 clang-include-cleaner 二进制")

    database_path = Path(database)
    entries = json.loads(database_path.read_text(encoding="utf-8"))
    if limit is not None:
        entries = entries[:limit]

    def one(entry: dict) -> IncludeCleanerRecord:
        return run_on_unit(
            resolved,
            entry.get("file", ""),
            database=str(database_path),
            directory=entry.get("directory"),
            timeout=timeout,
        )

    if jobs <= 1:
        return [one(entry) for entry in entries]

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(one, entries))


def compare_with_scan(
    records: list[IncludeCleanerRecord],
    scan: ProjectScan,
) -> IncludeCleanerComparison:
    """把 clang-include-cleaner 的删除建议与本工具的候选对上。

    对位口径与 IWYU 共用 ``attrib/crosscheck.py``：两个后端的「我们对上了」
    必须是同一个意思，否则两份报告的 ``一致`` 数字不可比。

    **被跳过的记录不参加对位**，只进 ``skipped_files``。它们要是静悄悄混进
    来，「0 条一致」会被读成「两个工具看法一致，没有可删的」，而真相可能
    是「大部分文件压根没分析成」。
    """
    by_tu = group_by_tu(scan.candidates)
    comparison = IncludeCleanerComparison(records=list(records))

    matched: set[int] = set()
    for record in records:
        if not record.analysed:
            continue
        ours = by_tu.get(basename_of(record.source), [])
        for edit in record.removes:
            hit, how = match_candidate(ours, edit.name, edit.lines)
            if hit is not None:
                matched.add(id(hit))
            comparison.pairs.append(
                IncludeCleanerPair(
                    source=record.source,
                    include=edit.spelling,
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

    candidates = [c for c in scan.candidates if not c.is_header_site]
    comparison.our_unmatched = sum(1 for c in candidates if id(c) not in matched)
    return comparison
