"""Forward-declaration *verification*: make the edit, recompile, measure.

:mod:`heavyheader.attrib.forward` answers "which includes look like they could
be replaced by a forward declaration" using regular expressions.  Its own
docstring says it plainly: "only used as a pointer" is a semantic property and
regexes can be fooled.  This module is the other half -- it performs the edit
for real:

1. take a candidate from the text analysis,
2. comment out the ``#include``, write the forward declarations the header's
   own spelling implies (class-key, template parameter list, namespace),
3. compile the patched translation unit and the original one, ``runs`` times
   each, keeping the minimum,
4. when the toolchain can produce assembly, compare the two fingerprints --
   because "it still compiles" is not "the program is the same".

Why the trial earns its keep
----------------------------
The text rule is conservative on purpose, and conservative has a cost: it
reports only the cases it can argue for.  The cases it *cannot* argue for are
exactly the ones where a compile is cheap and decisive -- a type used only
through a typedef alias, or through a template argument that happens to need
the complete type in one unit but not another.  And in the other direction,
the rule's false positives ("every mention was a pointer") are the expensive
ones: an unverified suggestion that breaks the build teaches a team to ignore
the tool.

Honesty boundaries
------------------
* **Compiling is not equivalence.** A unit can compile with a forward
  declaration and still generate different code (an inline function that is no
  longer available, a different overload chosen).  The reported tier therefore
  distinguishes "編譯通过" from "汇编指纹一致"; only the latter is called
  *equivalent*.
* If the assembly gate is unavailable (MSVC, or a compiler that refuses
  ``-S``), the result stays at "compiles only" and the report says so -- it
  never falls back to claiming equivalence.
* A saving smaller than :data:`TIME_NOISE_FLOOR` is reported as noise.  One
  compile of one unit is a noisy measurement; calling a 3 ms difference a win
  would be the kind of number this project exists to not produce.
* The edit is applied to a **copy** of the translation unit in a temporary
  directory, never to the real file: unlike a shared header (which cannot be
  copied, or include-path resolution would stop finding it), a translation
  unit is only ever read by the compiler that names it.  Nothing to restore
  means nothing to leave broken.
"""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import codegen
from ..drivers.base import CompilationError, CompilerDriver, DriverOptions
from ..includes import find_include_lines
from ..models import ProjectScan, TranslationUnit, tu_compile_seconds
from .advice import TIME_NOISE_FLOOR
from .forward import (
    ForwardCandidate,
    TypeDeclaration,
    find_forward_declaration_candidates,
    type_declarations,
)

__all__ = [
    "STATUS_AMBIGUOUS",
    "STATUS_CODEGEN_DIFFERS",
    "STATUS_COMPILES_ONLY",
    "STATUS_COMPILE_ERROR",
    "STATUS_EQUIVALENT",
    "STATUS_NEEDS_SPLIT",
    "STATUS_SKIPPED",
    "STATUS_VERIFIED",
    "ForwardTrial",
    "ForwardUnitTrial",
    "plan_patch",
    "render_declaration",
    "run_forward_trial",
]

#: 编译通过 **且** 汇编指纹一致 **且** 收益过噪声地板。
STATUS_VERIFIED = "verified"
#: 编译通过、指纹一致，但收益在噪声地板之内——「程序没变」已证，值不值得改未证。
STATUS_EQUIVALENT = "equivalent"
#: 编译通过，但这套工具链给不出汇编指纹，等价性没有证据。
STATUS_COMPILES_ONLY = "compiles-only"
#: 编译通过，但汇编指纹不同——这次改动改变了代码生成，**不可采纳**。
STATUS_CODEGEN_DIFFERS = "codegen-differs"
#: 换成前向声明后编不过。
STATUS_COMPILE_ERROR = "compile-error"
#: 源码里有不止一行 include 指向这个头，注释错行会让结论指向另一个文件。
STATUS_AMBIGUOUS = "ambiguous"
#: 生成不出（或没必要生成）可编译的前向声明，没有开测。
STATUS_SKIPPED = "skipped"
#: 同一个头里还有需要完整定义的类型：整条 include 换掉必然编不过，先拆头再测。
STATUS_NEEDS_SPLIT = "needs-split"

#: 写在被注释掉的 include 行首，任何 diff 一眼能看见、`git checkout` 一条命令能回退。
REVERT_MARK = "// [heavyheader] "

#: 算作「已证等价」的两档。``STATUS_VERIFIED`` 与 ``STATUS_EQUIVALENT`` 的差别
#: 只在收益是否过噪声地板，程序没变这一点两者相同。
EQUIVALENT_STATUSES = (STATUS_VERIFIED, STATUS_EQUIVALENT)


# --------------------------------------------------------------------------
# 生成补丁
# --------------------------------------------------------------------------


def render_declaration(declaration: TypeDeclaration) -> str:
    """把一条声明拼成能在 TU 里编译的前向声明。

    命名空间必须原样还原，否则 ``namespace ns { class Foo; }`` 里的 ``Foo``
    与 TU 里用的 ``ns::Foo`` 是两个不同的类型，编译会以「不完整类型」失败——
    而且失败信息指向前向声明那一行，看起来像是我们写错了语法。
    """
    body = f"{declaration.template_prefix}{declaration.kind} {declaration.name};"
    for namespace in reversed(declaration.namespaces):
        body = f"namespace {namespace} {{ {body} }}"
    return body


def plan_patch(
    source_text: str,
    line_number: int,
    declarations: list[TypeDeclaration],
) -> str:
    """注释掉一行 ``#include``，并在**原位置**补上前向声明。

    补在原位置而不是插到文件头，是为了保留条件编译上下文：``#include`` 写在
    ``#ifdef`` 里时，换出来的声明也应该受同一个 ``#ifdef`` 管辖。挪到文件头会
    悄悄改变这些代码在哪些配置下可见。
    """
    lines = source_text.splitlines()
    index = line_number - 1
    if not 0 <= index < len(lines):
        return source_text

    target = lines[index]
    # BOM 留在文件最前面，注释标记插到它后面——实验要可逆，连编码头都不该动。
    bom = ""
    if target.startswith("\ufeff"):
        bom, target = "\ufeff", target[len("\ufeff") :]
    indent = " " * (len(target) - len(target.lstrip()))
    lines[index] = f"{bom}{indent}{REVERT_MARK}{target.lstrip()}"

    added = [f"{indent}{render_declaration(d)}" for d in declarations]
    if added:
        lines[index + 1 : index + 1] = added
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ForwardUnitTrial:
    """一个（编译单元, 头文件）组合的实测结果。"""

    tu: Path
    header: Path
    types: list[str] = field(default_factory=list)
    status: str = STATUS_SKIPPED
    baseline_seconds: float | None = None
    patched_seconds: float | None = None
    error: str = ""
    note: str = ""
    #: 实际写进补丁的那几行声明，原样保留供复核。
    declarations: str = ""

    @property
    def saved_seconds(self) -> float | None:
        if self.baseline_seconds is None or self.patched_seconds is None:
            return None
        return self.baseline_seconds - self.patched_seconds

    @property
    def faster(self) -> bool:
        saved = self.saved_seconds
        return saved is not None and saved > 0

    @property
    def measured(self) -> bool:
        return self.baseline_seconds is not None and self.patched_seconds is not None

    def to_dict(self) -> dict:
        return {
            "tu": str(self.tu),
            "header": str(self.header),
            "types": list(self.types),
            "status": self.status,
            "baseline_seconds": self.baseline_seconds,
            "patched_seconds": self.patched_seconds,
            "saved_seconds": self.saved_seconds,
            "faster": self.faster,
            "declarations": self.declarations,
            "error": self.error,
            "note": self.note,
        }


@dataclass(slots=True)
class ForwardTrial:
    """一整轮前向声明实测。"""

    compiler: str = ""
    family: str = ""
    runs: int = 2
    #: 本轮是否真的做了汇编指纹比对。False 时所有「编译通过」的结果只能到
    #: ``compiles-only`` 档，报告不许写「等价」。
    codegen_checked: bool = False
    #: 调用方**要求**做比对与否。它与 ``codegen_checked`` 分开，是因为「用户关掉了」
    #: 和「这台机器做不到」是两件事，报告里不能共用一句解释。
    codegen_requested: bool = True
    #: 有多少个文本候选进入了本轮（可能多于 ``trials``：``max_trials`` 会截断）。
    considered: int = 0
    trials: list[ForwardUnitTrial] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def _statuses(self, statuses: tuple[str, ...]) -> list[ForwardUnitTrial]:
        return [t for t in self.trials if t.status in statuses]

    @property
    def measured_units(self) -> int:
        return sum(1 for t in self.trials if t.measured)

    @property
    def equivalent_units(self) -> int:
        """已证程序没变的单元数（``verified`` + ``equivalent``）。"""
        return len(self._statuses(EQUIVALENT_STATUSES))

    @property
    def verified_units(self) -> int:
        return len(self._statuses((STATUS_VERIFIED,)))

    @property
    def equivalent_only_units(self) -> int:
        """已证等价但收益在噪声内——不是没用，是「说不清值不值」。"""
        return len(self._statuses((STATUS_EQUIVALENT,)))

    @property
    def compiles_only_units(self) -> int:
        return len(self._statuses((STATUS_COMPILES_ONLY,)))

    @property
    def codegen_differ_units(self) -> int:
        return len(self._statuses((STATUS_CODEGEN_DIFFERS,)))

    @property
    def compile_error_units(self) -> int:
        return len(self._statuses((STATUS_COMPILE_ERROR,)))

    @property
    def skipped_units(self) -> int:
        return len(self._statuses((STATUS_SKIPPED, STATUS_AMBIGUOUS)))

    @property
    def needs_split_units(self) -> int:
        """「先拆头才能测」的候选数——不是失败，是这件事本轮做不了。"""
        return len(self._statuses((STATUS_NEEDS_SPLIT,)))

    def _totals(self, statuses: tuple[str, ...]) -> tuple[float, float]:
        """``(基线之和, 补丁后之和)``，只统计这些档位里两侧都测到的单元。"""
        baseline = 0.0
        patched = 0.0
        for trial in self._statuses(statuses):
            if trial.baseline_seconds is None or trial.patched_seconds is None:
                continue
            baseline += trial.baseline_seconds
            patched += trial.patched_seconds
        return baseline, patched

    @property
    def equivalent_baseline_seconds(self) -> float:
        return self._totals(EQUIVALENT_STATUSES)[0]

    @property
    def equivalent_saved_seconds(self) -> float:
        """已证等价那一档省下的秒数（**只含负向变动**，变慢的不倒扣）。

        不倒扣的理由：这份报告回答的是「采纳这些建议能省多少」，一条让某个
        单元变慢的建议本来就不该被采纳。变慢的单元在表里逐条列出，不藏。
        """
        baseline, patched = self._totals(EQUIVALENT_STATUSES)
        return max(0.0, baseline - patched)

    @property
    def equivalent_saved_percent(self) -> float:
        baseline, patched = self._totals(EQUIVALENT_STATUSES)
        if baseline <= 0:
            return 0.0
        return max(0.0, (baseline - patched) / baseline * 100.0)

    @property
    def compiles_only_saved_seconds(self) -> float:
        baseline, patched = self._totals((STATUS_COMPILES_ONLY,))
        return max(0.0, baseline - patched)

    @property
    def slower_units(self) -> int:
        return sum(1 for t in self.trials if t.measured and not t.faster)

    def to_dict(self) -> dict:
        return {
            "compiler": self.compiler,
            "family": self.family,
            "runs": self.runs,
            "codegen_checked": self.codegen_checked,
            "codegen_requested": self.codegen_requested,
            "considered": self.considered,
            "measured_units": self.measured_units,
            "verified_units": self.verified_units,
            "equivalent_only_units": self.equivalent_only_units,
            "equivalent_units": self.equivalent_units,
            "compiles_only_units": self.compiles_only_units,
            "codegen_differ_units": self.codegen_differ_units,
            "compile_error_units": self.compile_error_units,
            "skipped_units": self.skipped_units,
            "needs_split_units": self.needs_split_units,
            "slower_units": self.slower_units,
            "equivalent_baseline_seconds": self.equivalent_baseline_seconds,
            "equivalent_saved_seconds": self.equivalent_saved_seconds,
            "equivalent_saved_percent": self.equivalent_saved_percent,
            "compiles_only_saved_seconds": self.compiles_only_saved_seconds,
            "trials": [t.to_dict() for t in self.trials],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------
# 编译
# --------------------------------------------------------------------------


def _best_of(
    driver: CompilerDriver,
    source: Path,
    options: DriverOptions,
    cwd: Path | None,
    runs: int,
    timeout: float,
) -> tuple[float | None, str]:
    """编译 ``runs`` 次取最小墙钟；失败立刻返回错误。

    取最小值而不是平均值：噪声只会让一次编译变慢，不会让它变快，所以最小值
    是最接近真值的估计量。失败没有快慢之分，第一次失败就够了。
    """
    best: float | None = None
    for _ in range(max(1, runs)):
        try:
            tu = driver.run(source, options, cwd=cwd, timeout=timeout)
        except CompilationError as exc:
            return None, str(exc)
        if not tu.ok:
            return None, tu.error or "compile failed"
        seconds = tu_compile_seconds(tu)
        if best is None or seconds < best:
            best = seconds
    return best, ""


def _patched_options(
    unit_options: DriverOptions, source: Path
) -> DriverOptions:
    """编译临时目录里那份副本用的选项。

    副本换了目录，``#include "foo.h"`` 就按副本所在目录解析——原目录必须补进
    搜索路径，否则整批候选会以「找不到头文件」失败，而这看起来和「前向声明
    不成立」一模一样。
    """
    return replace(
        unit_options,
        include_dirs=[*unit_options.include_dirs, source.parent],
    )


def _unit_for(scan: ProjectScan, source: Path) -> TranslationUnit | None:
    for tu in scan.ok_tus:
        if tu.source == source:
            return tu
    return None


def _fallback_options(scan: ProjectScan) -> DriverOptions:
    from ..scanner import default_include_dirs

    return DriverOptions(
        standard=scan.standard,
        include_dirs=default_include_dirs(scan.root),
    )


# --------------------------------------------------------------------------
# 一个候选的准备
# --------------------------------------------------------------------------


def _prepare(
    scan: ProjectScan, candidate: ForwardCandidate
) -> tuple[ForwardUnitTrial, str | None]:
    """算出该注释哪一行、该写哪些声明。返回 ``(trial, 补丁后的全文)``。

    返回 ``None`` 作为全文表示「不该开测」，此时 ``trial.status`` 里已经写明
    原因——绝不用一个空补丁去编译，那测出来的只是原地重编的噪声。
    """
    trial = ForwardUnitTrial(
        tu=candidate.tu,
        header=candidate.header,
        types=list(candidate.forwardable_types),
    )

    text = None
    try:
        text = candidate.tu.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        trial.status = STATUS_SKIPPED
        trial.note = f"读不到源码：{exc}"
        return trial, None

    lines = find_include_lines(text, candidate.header)
    if not lines:
        trial.status = STATUS_SKIPPED
        trial.note = "在源码里找不到对应的 #include 行"
        return trial, None
    if len(lines) > 1:
        trial.status = STATUS_AMBIGUOUS
        trial.note = (
            f"源码里 {len(lines)} 行 include 都指向 {candidate.header.name}，"
            "注释错行会让结论指向另一个文件，因此不测"
        )
        return trial, None
    line_number = lines[0]

    declarations = type_declarations(candidate.header)
    wanted = set(candidate.forwardable_types)
    picked = [d for d in declarations if d.name in wanted]

    missing = wanted - {d.name for d in picked}
    if missing:
        # 正则认得出名字、这里认不出声明上下文：宁可说不知道，也不猜它是不是
        # 在命名空间里——猜错的结果是编译失败，而失败原因看起来像用户的代码有问题。
        trial.note = f"无法确定这些类型的声明上下文：{', '.join(sorted(missing))}"

    internal = [d.name for d in picked if d.internal_linkage]
    if internal:
        trial.note = (
            (trial.note + "；" if trial.note else "")
            + f"匿名命名空间里的类型无法从外部声明：{', '.join(sorted(internal))}"
        )
    usable = [d for d in picked if not d.internal_linkage]

    if not usable:
        trial.status = STATUS_SKIPPED
        trial.note = trial.note or "没有可以生成前向声明的类型"
        return trial, None

    trial.types = [d.name for d in usable]
    trial.declarations = "\n".join(render_declaration(d) for d in usable)
    return trial, plan_patch(text, line_number, usable)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def run_forward_trial(
    scan: ProjectScan,
    *,
    driver: CompilerDriver | None = None,
    candidates: list[ForwardCandidate] | None = None,
    max_trials: int = 10,
    runs: int = 2,
    check_codegen: bool = True,
    timeout: float = 300.0,
    jobs: int = 1,
) -> ForwardTrial:
    """把文本候选逐条真的换成前向声明，重编、计时、比指纹。

    ``max_trials`` 截断的是**真正开测的条数**，不是候选总数——报告里同时给出
    ``considered`` 与本轮测了几条，避免把「只测了 10 条」读成「全项目只有 10 条」。
    """
    if driver is None:
        from ..drivers.base import detect_driver

        driver = detect_driver(scan.compiler)

    pool_of_candidates = (
        candidates
        if candidates is not None
        else (getattr(scan, "forward_candidates", None) or [])
    )
    if not pool_of_candidates:
        pool_of_candidates = find_forward_declaration_candidates(
            scan, max_candidates=max(max_trials, 20)
        )

    trial = ForwardTrial(
        compiler=getattr(driver, "executable", scan.compiler),
        family=str(getattr(driver, "family", scan.compiler_family)),
        runs=runs,
        considered=len(pool_of_candidates),
        codegen_requested=bool(check_codegen),
        #: MSVC 没有 ``-S``/``-g0`` 这套写法，硬开会把每个候选都误报成
        #: 「代码变了」——能力不够时直接不跑，报告也不许声称校验过。
        codegen_checked=bool(check_codegen and codegen.supported(driver)),
    )

    if not pool_of_candidates:
        trial.notes.append(
            "没有前向声明候选：要么没有「只用指针/引用」的类型，"
            "要么所有候选中都出现了需要完整定义的用法"
        )
        return trial

    if trial.codegen_requested and not trial.codegen_checked:
        trial.notes.append(
            "这套工具链给不出汇编指纹（MSVC 缺 -S/-g0 支持），"
            "本轮结论最高只能到「编译通过」，不判定代码是否等价"
        )
    elif not trial.codegen_requested:
        trial.notes.append(
            "本轮按 --no-codegen-check 关掉了汇编指纹比对，"
            "因此结论最高只能到「编译通过」——那是用户的选择，不是工具的能力上限"
        )

    # 有「需要完整定义」的类型在场时，把整条 include 换成前向声明**必然**编不过
    # ——那不是「前向声明不成立」，是「这个头得先拆开」。混进 compile-error 会让
    # 读者以为这条建议本身是错的，所以单独归一类，标明没测。
    blocked = [c for c in pool_of_candidates if c.blocking_types]
    testable = [c for c in pool_of_candidates if not c.blocking_types]

    for candidate in blocked[: max(0, max_trials)]:
        trial.trials.append(
            ForwardUnitTrial(
                tu=candidate.tu,
                header=candidate.header,
                types=list(candidate.forwardable_types),
                status=STATUS_NEEDS_SPLIT,
                note=(
                    f"同一头里 {', '.join(candidate.blocking_types)} 需要完整定义，"
                    "整条 include 换掉必然编不过：先把这些类型拆到单独的头，"
                    "再对这里的前向声明单独实测"
                ),
            )
        )
    if len(blocked) > max(0, max_trials):
        trial.notes.append(
            f"另有 {len(blocked) - max_trials} 个候选因同一头里存在需要完整定义的"
            "类型而未列入本表（受 --max-trials 限制）"
        )

    queue = testable[: max(0, max_trials)]
    prepared: list[tuple[ForwardUnitTrial, str]] = []
    for candidate in queue:
        unit_trial, patched = _prepare(scan, candidate)
        if patched is None:
            trial.trials.append(unit_trial)
        else:
            prepared.append((unit_trial, patched))
    if len(testable) > len(queue):
        trial.notes.append(
            f"本轮只测了 {len(queue)} 条候选，还有 {len(testable) - len(queue)} 条"
            "未测（受 --max-trials 限制）；报告里的收益不等于全项目收益"
        )

    # 基线按单元缓存：同一个 TU 上的多个候选共享同一次基线编译。
    baselines: dict[Path, tuple[float | None, str]] = {}

    def measure(item: tuple[ForwardUnitTrial, str]) -> None:
        unit_trial, patched = item
        tu = _unit_for(scan, unit_trial.tu)
        if tu is None:
            unit_trial.status = STATUS_SKIPPED
            unit_trial.note = "这个源码不在本轮成功编译的单元里，无法复现它的编译命令"
            return
        options = getattr(tu, "compile_options", None) or _fallback_options(scan)
        cwd = getattr(tu, "compile_cwd", None)

        if unit_trial.tu not in baselines:
            baselines[unit_trial.tu] = _best_of(
                driver, unit_trial.tu, options, cwd, runs, timeout
            )
        baseline_seconds, baseline_error = baselines[unit_trial.tu]
        if baseline_seconds is None:
            unit_trial.status = STATUS_COMPILE_ERROR
            unit_trial.error = baseline_error
            unit_trial.note = "原文件本身就编译不过，无从比较——先修好基线再谈收益"
            return
        unit_trial.baseline_seconds = baseline_seconds

        with tempfile.TemporaryDirectory(prefix="heavyheader-fwd-") as tmp:
            tmpdir = Path(tmp)
            patched_path = tmpdir / unit_trial.tu.name
            patched_path.write_text(patched, encoding="utf-8")
            patched_options = _patched_options(options, unit_trial.tu)

            seconds, error = _best_of(
                driver, patched_path, patched_options, cwd, runs, timeout
            )
            if seconds is None:
                unit_trial.status = STATUS_COMPILE_ERROR
                unit_trial.error = _first_line(error)
                return
            unit_trial.patched_seconds = seconds

            if not trial.codegen_checked:
                unit_trial.status = STATUS_COMPILES_ONLY
                return

            try:
                before, after = codegen.compare(
                    driver,
                    unit_trial.tu,
                    patched_path,
                    patched_options,
                    cwd,
                    tmpdir / "codegen",
                    timeout,
                )
            except (CompilationError, OSError) as exc:
                # 闸门没跑完 ≠ 代码变了。以前两者共用一个 False，于是「这台机器
                # 给不出汇编」被报成「发现危险改动」。
                unit_trial.status = STATUS_COMPILES_ONLY
                unit_trial.note = _join_note(
                    unit_trial.note, f"汇编指纹取不到：{_first_line(str(exc))}"
                )
                return

            if not (before.ok and after.ok):
                unit_trial.status = STATUS_COMPILES_ONLY
                unit_trial.note = _join_note(
                    unit_trial.note,
                    f"汇编指纹取不到：{before.note or after.note or '未知原因'}",
                )
                return

            if not before.same_as(after):
                unit_trial.status = STATUS_CODEGEN_DIFFERS
                unit_trial.note = _join_note(
                    unit_trial.note,
                    "补丁前后汇编指纹不同——这次改动改变了代码生成，不可采纳",
                )
                return

            saved = unit_trial.saved_seconds or 0.0
            unit_trial.status = (
                STATUS_VERIFIED if saved >= TIME_NOISE_FLOOR else STATUS_EQUIVALENT
            )
            if unit_trial.status == STATUS_EQUIVALENT:
                unit_trial.note = _join_note(
                    unit_trial.note,
                    f"收益 {saved * 1000:.0f} ms 在噪声地板"
                    f"（{TIME_NOISE_FLOOR * 1000:.0f} ms）之内，不构成可报收益",
                )

    if jobs > 1 and len(prepared) > 1:
        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [executor.submit(measure, item) for item in prepared]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - 一条候选不该带走整轮
                    trial.notes.append(f"一条候选测量时抛异常：{type(exc).__name__}: {exc}")
    else:
        for item in prepared:
            measure(item)

    trial.trials.extend(item[0] for item in prepared)
    return trial


def _first_line(text: str) -> str:
    """编译器错误的第一行——完整输出可能有几百行，报告里只要最要紧那句。"""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _join_note(existing: str, extra: str) -> str:
    return f"{existing}；{extra}" if existing else extra
