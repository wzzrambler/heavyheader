"""Precompiled-header *measurement*: build the PCH, recompile, time it.

:mod:`heavyheader.attrib.pch` answers "what belongs in a PCH" with a model:
lines of header text that stop being re-parsed.  Models can be wrong.  This
module is the other half -- it does what the model only predicts:

1. take the recommended header set,
2. write a real umbrella header and precompile it into a real ``.gch``,
3. recompile every candidate unit twice -- with and without the PCH -- and
   take the minimum of each side (the same robust estimator
   :mod:`heavyheader.verify` uses),
4. report what *actually* happened.

What makes this more than a slower way to get the same number
-------------------------------------------------------------
Two things the estimate structurally cannot see, and one it gets backwards:

* **A PCH is only shared between units compiled with identical flags.** One
  ``-D`` difference and the compiler silently refuses the ``.gch`` -- exit
  code 0, no error, and the "saving" is exactly zero.  The estimate counts
  every unit that reaches the headers, which is an upper bound that assumes
  a homogeneity real projects rarely have.  This module groups units by their
  actual flag signature and measures the largest group, so the number reported
  is the number somebody could really get.
* **The compiler may decline the PCH without saying anything** unless asked.
  ``-Winvalid-pch`` makes it speak; the raw stderr is then searched for the
  refusal.  Without that check a trial where nothing was ever precompiled
  would look identical to a successful one.
* **Some units stop compiling.** ``-include`` forces the PCH ahead of
  everything, so a unit that defines a macro before including a header now
  sees that header already pre-expanded.  That is a real cost of adopting a
  PCH and the estimate has no way to express it.  Here it is a counted,
  per-unit finding.

Honesty boundaries
------------------
* Timing is wall clock, one compile at a time.  Wall clock includes process
  startup, which a PCH does not remove, so the measured saving is
  **systematically smaller** than the parsing work eliminated.
* This measures one flag group.  It does not claim the whole project would
  save the same fraction; the report says how many units were measured out of
  how many were eligible, and refuses to extrapolate.
* Lines saved and seconds saved are different units.  This module does **not**
  convert one into the other, and does not report a "prediction error"
  against the line estimate -- there is no honest conversion factor
  (see ``experiments/``: preprocessing share overpredicts measured saving by
  ~1.6x).  What it does compare is *coverage*: units the estimate thought
  would benefit versus units that measurably did.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..drivers.base import CompilationError, CompilerDriver, DriverOptions
from ..models import ProjectScan, TranslationUnit, tu_compile_seconds
from .pch import PCHRecommendation, estimate_pch

__all__ = ["PCHUnitTrial", "PCHTrial", "run_pch_trial", "option_signature"]


# 编译器「没有采用 .gch」的说法。GCC：``pch.h.gch: not used because ...``；
# Clang：``precompiled header '...' was ignored / is not used``。两者都只在
# 打开 -Winvalid-pch 之后才出现，而且都不影响退出码——只能从输出里认出来。
#
# 第一版写成 ``(?:gch|pch)\b[^\n]{0,200}?(?:not used|ignored|invalid|…)``：
# 在 json 的 unit-allocator.cpp 上把 12 个单元里的 1 个错判成「未采用」。
# 那条 stderr 里是 ``In file included from …heavyheader_pch.h:2:`` 这样的
# 诊断上下文行——``pch`` 被当成 ``.gch``，后面 200 字符内任何一处出现
# ``invalid``（例如一条无关的 ``invalid use of …``）就命中。拒绝信号必须是
# **紧跟在 .gch 之后的原因**，不能是「同一行里出现了某个词」。
_PCH_REFUSED = re.compile(
    # GCC：``/path/pch.h.gch: not used because 'X' is defined``
    r"\.(?:gch|pch)['\"]?[^\n]{0,40}?"
    r"(?:not used|ignored|invalid|not valid|incompatible|mismatch|differs)"
    r"|"
    # Clang：``precompiled header '...gch' is not used``
    r"precompiled header[^\n]{0,120}?"
    r"(?:not used|ignored|invalid|incompatible|not valid)",
    re.IGNORECASE,
)

# 编译器不认识 -Winvalid-pch 时自己说的话。这时候那个开关没有生效，
# 「没看到拒绝」不等于「采用了」——必须换成别的判据，不能默默当成功。
#: 让编译器在**没有**采用 .gch 时开口。不给它，一次从未生效的实测与
#: 一次成功的实测在退出码和耗时上都有可能长得一样。
_WARN_FLAG = "-Winvalid-pch"

_UNKNOWN_WARNING = re.compile(
    r"(?:unknown warning option|unrecogni[sz]ed command[ -]line option)"
    r"[^\n]{0,60}invalid[-_]pch",
    re.IGNORECASE,
)


@dataclass(slots=True)
class PCHUnitTrial:
    """One translation unit, compiled both ways."""

    source: Path
    baseline_seconds: float
    pch_seconds: float
    baseline_ok: bool
    pch_ok: bool
    #: 编译器是不是真的用了 .gch。False 意味着这次编译**根本没有预编译**，
    #: 它的耗时不能算进 PCH 的收益里——否则等于给一个没发生的事记功。
    pch_adopted: bool = False
    note: str = ""

    @property
    def delta_seconds(self) -> float:
        """正数 = 用 PCH 更快。"""
        return self.baseline_seconds - self.pch_seconds

    @property
    def faster(self) -> bool:
        return self.pch_ok and self.pch_adopted and self.delta_seconds > 0

    @property
    def usable(self) -> bool:
        """这个单元能用上 PCH：编得过、且编译器确实采用了 .gch。"""
        return self.pch_ok and self.pch_adopted

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "baseline_seconds": round(self.baseline_seconds, 4),
            "pch_seconds": round(self.pch_seconds, 4),
            "delta_seconds": round(self.delta_seconds, 4),
            "baseline_ok": self.baseline_ok,
            "pch_ok": self.pch_ok,
            "pch_adopted": self.pch_adopted,
            "faster": self.faster,
            "note": self.note,
        }


@dataclass(slots=True)
class PCHTrial:
    """The outcome of actually building a PCH and recompiling with it."""

    compiler: str
    family: str
    #: 放进 PCH 的头（估算给出的集合）。
    headers: list[Path]
    pch_header: Path | None = None
    pch_built: bool = False
    pch_build_seconds: float = 0.0
    pch_error: str = ""
    #: 参与实测的那一组编译选项的指纹。同一指纹 = 可以共享一份 .gch。
    group_signature: str = ""
    group_size: int = 0
    #: 估算认为会用上 PCH 的单元数（全项目，未按选项分组）。
    candidate_units: int = 0
    total_units: int = 0
    #: 估算给出的覆盖率（eligible / total）。
    estimated_coverage: float = 0.0
    runs: int = 2
    full_compile: bool = False
    units: list[PCHUnitTrial] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # -- 聚合口径 ---------------------------------------------------------

    @property
    def measured_units(self) -> int:
        return len(self.units)

    @property
    def adopted_units(self) -> int:
        return sum(1 for unit in self.units if unit.usable)

    @property
    def faster_units(self) -> int:
        return sum(1 for unit in self.units if unit.faster)

    @property
    def baseline_seconds(self) -> float:
        return sum(unit.baseline_seconds for unit in self.units)

    @property
    def pch_seconds(self) -> float:
        return sum(unit.pch_seconds for unit in self.units)

    @property
    def saved_seconds(self) -> float:
        return self.baseline_seconds - self.pch_seconds

    @property
    def saved_percent(self) -> float:
        """实测省下的时间占**被测单元**基线的百分比。

        分母不是全项目：只测了这一组单元，拿全项目当分母等于把没测的部分
        也算成收益。
        """
        base = self.baseline_seconds
        if base <= 0:
            return 0.0
        return self.saved_seconds / base * 100.0

    # -- 「PCH 的功劳」口径：只算编译器真的采用了 .gch 的单元 ------------
    #
    # 全组口径回答的是「给这批单元加上 -include 之后，总耗时变了多少」；
    # 它包含那些 PCH 从未生效的单元，那部分的差值纯属噪声，不是 PCH 的功劳。
    # 两个口径都必须报：只报前者会把噪声算成收益，只报后者会让读者误以为
    # 整组单元都能享受到。

    @property
    def adopted_baseline_seconds(self) -> float:
        return sum(unit.baseline_seconds for unit in self.units if unit.usable)

    @property
    def adopted_pch_seconds(self) -> float:
        return sum(unit.pch_seconds for unit in self.units if unit.usable)

    @property
    def adopted_saved_seconds(self) -> float:
        return self.adopted_baseline_seconds - self.adopted_pch_seconds

    @property
    def adopted_saved_percent(self) -> float:
        """预编译头本身带来的节省。没有单元采用 .gch 时是 0，不是负数。"""
        base = self.adopted_baseline_seconds
        if base <= 0:
            return 0.0
        return self.adopted_saved_seconds / base * 100.0

    @property
    def measured_coverage(self) -> float:
        """实测里真用上 PCH 的单元 / 参与实测的单元。"""
        if not self.units:
            return 0.0
        return self.adopted_units / len(self.units) * 100.0

    @property
    def project_coverage(self) -> float:
        """实测里真用上 PCH 的单元 / 全项目编译成功的单元。

        这才是「这个项目有多少单元能真的用上 PCH」——估算的 ``coverage``
        是上界，这个是下界（只测了一个选项组）。
        """
        if self.total_units <= 0:
            return 0.0
        return self.adopted_units / self.total_units * 100.0

    @property
    def agreement(self) -> float:
        """估算认为会受益的单元里，实测真的变快的比例。

        估算与实测口径不同（行数 vs 秒），不能做偏差换算；但「它点名的单元
        是不是真的变快了」是一个可以直接量的对照。
        """
        if not self.units:
            return 0.0
        return self.faster_units / len(self.units) * 100.0

    @property
    def break_even_units(self) -> int | None:
        """要多少个单元才能把生成 .gch 的一次性成本赚回来。

        用实测的单单元平均收益去摊 ``pch_build_seconds``。None = 算不出来
        （收益为 0 或没有测到单元）。这不是预测，是拿实测数字做除法。
        """
        if not self.units or self.saved_seconds <= 0:
            return None
        per_unit = self.saved_seconds / self.adopted_units if self.adopted_units else 0.0
        if per_unit <= 0 or self.pch_build_seconds <= 0:
            return None
        return int(self.pch_build_seconds / per_unit) + 1

    @property
    def is_conclusive(self) -> bool:
        return self.pch_built and len(self.units) >= 2 and self.adopted_units > 0

    def to_dict(self) -> dict:
        return {
            "compiler": self.compiler,
            "family": self.family,
            "headers": [str(path) for path in self.headers],
            "pch_header": str(self.pch_header) if self.pch_header else None,
            "pch_built": self.pch_built,
            "pch_build_seconds": round(self.pch_build_seconds, 4),
            "pch_error": self.pch_error,
            "group_signature": self.group_signature,
            "group_size": self.group_size,
            "measured_units": self.measured_units,
            "candidate_units": self.candidate_units,
            "total_units": self.total_units,
            "estimated_coverage": round(self.estimated_coverage, 2),
            "measured_coverage": round(self.measured_coverage, 2),
            "project_coverage": round(self.project_coverage, 2),
            "adopted_units": self.adopted_units,
            "faster_units": self.faster_units,
            "agreement_percent": round(self.agreement, 2),
            "runs": self.runs,
            "full_compile": self.full_compile,
            "baseline_seconds": round(self.baseline_seconds, 4),
            "pch_seconds": round(self.pch_seconds, 4),
            "saved_seconds": round(self.saved_seconds, 4),
            "saved_percent": round(self.saved_percent, 2),
            "adopted_saved_seconds": round(self.adopted_saved_seconds, 4),
            "adopted_saved_percent": round(self.adopted_saved_percent, 2),
            "break_even_units": self.break_even_units,
            "conclusive": self.is_conclusive,
            "units": [unit.to_dict() for unit in self.units],
            "notes": list(self.notes),
        }


def option_signature(options: DriverOptions | None) -> str:
    """编译选项的指纹：只有指纹相同的单元才能共享一份 .gch。

    为什么必须做：一份 .gch 只在「编译选项与生成它时完全一致」时被采用，
    差一个 ``-D`` 编译器就静默退回普通编译。把指纹不同的单元放进同一次实测，
    测出来的是「一部分单元用了 PCH、另一部分没有」的混合数——看起来是个
    谦虚的收益，实际是一半的测量无效。
    """
    if options is None:
        return "unknown"
    parts = [
        str(options.standard or ""),
        *sorted(str(p) for p in options.include_dirs),
        *sorted(str(d) for d in options.defines),
        *sorted(str(f) for f in options.extra_flags),
    ]
    digest = hashlib.sha1("\n".join(parts).encode("utf-8", "replace")).hexdigest()
    return digest[:12]


def _write_umbrella(path: Path, headers: list[Path]) -> None:
    """Write the umbrella header whose precompiled form is the PCH.

    用**绝对路径**而不是源文件里的写法：同一个头在不同单元里可能通过不同的
    ``-I`` 被找到，写相对形式会在某些单元里解析成别的文件，于是那份 .gch
    对它们失效。绝对路径在 Windows 上必须走正斜杠——``"C:\\x\\y.h"`` 里的
    ``\\x`` 是预处理器转义序列，会被吃掉。
    """
    lines = ["/* Generated by HeavyHeader -- a PCH trial, not part of the project. */"]
    # 故意不加 include guard：这份文件只会被预编译一次，加了反而可能与被
    # 包含头的 guard 宏同名，让整份 PCH 内容变成空的。
    for header in headers:
        lines.append(f'#include "{header.as_posix()}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pch_users(scan: ProjectScan, headers: list[Path]) -> list[TranslationUnit]:
    """包含至少一个 PCH 头的单元——与 :func:`estimate_pch` 的 pch_users 同口径。"""
    if not headers:
        return []
    users: list[TranslationUnit] = []
    for tu in scan.ok_tus:
        reached = {event.included for event in tu.include_events}
        if any(header in reached for header in headers):
            users.append(tu)
    return users


def _compile_once(
    driver: CompilerDriver,
    tu: TranslationUnit,
    options: DriverOptions,
    cwd: Path | None,
    timeout: float,
) -> tuple[TranslationUnit, str]:
    return driver.run_verbose(tu.source, options, cwd, timeout)


def _best_of(
    driver: CompilerDriver,
    tu: TranslationUnit,
    options: DriverOptions,
    cwd: Path | None,
    runs: int,
    timeout: float,
) -> tuple[TranslationUnit, str]:
    """Compile ``runs`` times and keep the fastest run.

    取最小值而不是平均值，理由与 ``verify`` 完全相同：噪声只会让一次编译
    **变慢**，不会让它变快，所以最小值是最接近真值的估计量。平均值会把
    一次杀毒扫描、一次缺页都算进「PCH 的收益」里。
    """
    best: TranslationUnit | None = None
    best_stderr = ""
    for _ in range(max(1, runs)):
        candidate, stderr = _compile_once(driver, tu, options, cwd, timeout)
        if best is None or tu_compile_seconds(candidate) < tu_compile_seconds(best):
            best, best_stderr = candidate, stderr
        # 失败没有快慢之分，第一次失败就够了——重试只会浪费时间。
        if not candidate.ok:
            return candidate, stderr
    return best or tu, best_stderr


def run_pch_trial(
    scan: ProjectScan,
    *,
    driver: CompilerDriver | None = None,
    recommendation: PCHRecommendation | None = None,
    workdir: Path | None = None,
    runs: int = 2,
    full_compile: bool = False,
    max_units: int | None = None,
    min_group: int = 2,
    timeout: float = 300.0,
) -> PCHTrial:
    """Build the recommended PCH for real and measure what it buys.

    ``workdir`` is where the umbrella header and the ``.gch`` go; it is the
    caller's job to keep or clean it (an experiment archive wants to keep it,
    a CLI run wants a temp directory).

    The trial measures **one** flag-signature group -- the largest one.  That
    is a deliberate narrowing: a PCH is only shared between identically
    compiled units, so measuring a mixed group would report a diluted number
    that no real build configuration could reproduce.
    """
    if driver is None:
        from ..drivers.base import detect_driver

        driver = detect_driver(scan.compiler)

    rec = recommendation or getattr(scan, "pch", None) or estimate_pch(scan)
    headers = list(getattr(rec, "headers", []) or [])
    users = _pch_users(scan, headers)

    trial = PCHTrial(
        compiler=getattr(driver, "executable", scan.compiler),
        family=str(getattr(driver, "family", scan.compiler_family)),
        headers=headers,
        candidate_units=len(users),
        total_units=len(scan.ok_tus),
        estimated_coverage=float(getattr(rec, "coverage", 0.0) or 0.0),
        runs=runs,
        full_compile=full_compile,
    )

    if not headers:
        trial.pch_error = "估算没有给出任何候选头，无 PCH 可测。"
        return trial
    if not users:
        trial.pch_error = "没有任何编译单元包含这些头，PCH 无适用场景。"
        return trial

    # -- 按编译选项分组：只有指纹相同的单元才能共享一份 .gch --------------
    groups: dict[str, list[TranslationUnit]] = {}
    signatures: dict[str, DriverOptions] = {}
    cwds: dict[str, Path | None] = {}
    for tu in users:
        options = getattr(tu, "compile_options", None)
        signature = option_signature(options)
        groups.setdefault(signature, []).append(tu)
        if options is not None and signature not in signatures:
            signatures[signature] = options
        cwds.setdefault(signature, getattr(tu, "compile_cwd", None))

    if not groups:
        trial.pch_error = "候选单元没有记录编译选项，无法复现编译。"
        return trial

    signature, group = max(groups.items(), key=lambda item: (len(item[1]), item[0]))
    trial.group_signature = signature
    trial.group_size = len(group)
    if len(group) < max(2, min_group):
        trial.pch_error = (
            f"编译选项一致的单元只有 {len(group)} 个（共 {len(users)} 个候选）。"
            "编译选项不同的单元无法共享一份 .gch，样本不足时不做测量——"
            "拿一个单元跟自己比，测出来的是噪声。"
        )
        return trial

    if max_units is not None and max_units > 0:
        group = group[:max_units]

    options = signatures.get(signature) or DriverOptions()
    cwd = cwds.get(signature)
    if cwd is None:
        cwd = scan.root if scan.root.is_dir() else None

    work = workdir or Path(".")
    work.mkdir(parents=True, exist_ok=True)
    umbrella = work / "heavyheader_pch.h"
    _write_umbrella(umbrella, headers)
    trial.pch_header = umbrella

    gch = umbrella.with_suffix(umbrella.suffix + ".gch")

    # -- 生成 .gch --------------------------------------------------------
    build_argv = driver.build_pch_command(umbrella, options, gch)
    if build_argv is None:
        trial.pch_error = (
            f"{trial.family} 工具链没有可用的预编译头机制（"
            "本模块靠 -include 注入，MSVC 的 /Yc//Yu 要求被预编译的头在"
            "每个单元里都是第一个 include），不做实测。"
        )
        return trial

    try:
        completed, build_seconds, _ = driver.spawn(build_argv, cwd, timeout)
    except CompilationError as exc:
        trial.pch_error = f"生成预编译头失败：{exc}"
        return trial
    trial.pch_build_seconds = build_seconds
    if completed.returncode != 0:
        trial.pch_error = (
            "生成预编译头失败（退出码 "
            f"{completed.returncode}）："
            f"{_first_line(completed.stderr) or '无输出'}"
        )
        return trial
    trial.pch_built = True
    if not gch.is_file():
        # 编译器说成功了却没落盘——多半是 -o 被项目自己的 extra_flags 覆盖。
        trial.pch_built = False
        trial.pch_error = (
            f"编译器返回成功但 {gch.name} 不存在；"
            "编译选项里可能有冲突的 -o，预编译头没有生成。"
        )
        return trial

    # -- 逐单元：有 PCH / 无 PCH ------------------------------------------
    inject_flags = driver.pch_use_flags(umbrella) or []
    objects = work / "objects"
    unit_options = replace(options, full_compile=full_compile, output_path=None)
    if full_compile:
        objects.mkdir(parents=True, exist_ok=True)

    for index, tu in enumerate(group):
        note = ""
        base_options = unit_options
        pch_options = replace(
            unit_options,
            extra_flags=[*unit_options.extra_flags, *inject_flags, _WARN_FLAG],
        )
        if full_compile:
            # 完整编译时两侧各写自己的目标文件：写同一个会让第二次编译
            # 覆盖第一次的产物，而计时上看起来毫无破绽。
            base_options = replace(base_options, output_path=objects / f"base_{index}.o")
            pch_options = replace(pch_options, output_path=objects / f"pch_{index}.o")

        baseline_tu, _ = _best_of(driver, tu, base_options, cwd, runs, timeout)
        try:
            pch_tu, stderr = _best_of(driver, tu, pch_options, cwd, runs, timeout)
        except CompilationError as exc:
            note = f"用 PCH 编译时编译器无法启动：{exc}"
            pch_tu, stderr = TranslationUnit(source=tu.source, error=str(exc)), ""

        verdict = _adopt_verdict(pch_tu, stderr)
        if verdict is None:
            # -Winvalid-pch 不被这个编译器认识，它的沉默什么都不能证明。
            # 去掉这个开关重编一次，改用「是不是真的变快了」当判据。
            fallback = replace(
                unit_options,
                extra_flags=[*unit_options.extra_flags, *inject_flags],
                output_path=base_options.output_path,
            )
            try:
                pch_tu, stderr = _best_of(driver, tu, fallback, cwd, runs, timeout)
            except CompilationError as exc:
                pch_tu = TranslationUnit(source=tu.source, error=str(exc))
            baseline_seconds = tu_compile_seconds(baseline_tu)
            adopted = (
                pch_tu.ok
                and baseline_seconds > 0
                and tu_compile_seconds(pch_tu) < baseline_seconds
            )
            note = f"{note} {_WARN_FLAG} 不被识别，改以实测快慢判定采用与否".strip()
        else:
            adopted = verdict
            if not adopted and pch_tu.ok:
                note = f"{note} 编译器未采用 .gch（{_WARN_FLAG} 有输出）".strip()
        if not pch_tu.ok and pch_tu.error:
            note = f"{note} 用 PCH 后编译失败：{pch_tu.error}".strip()

        trial.units.append(
            PCHUnitTrial(
                source=tu.source,
                baseline_seconds=tu_compile_seconds(baseline_tu),
                pch_seconds=tu_compile_seconds(pch_tu),
                baseline_ok=baseline_tu.ok,
                pch_ok=pch_tu.ok,
                pch_adopted=adopted,
                note=note.strip(),
            )
        )

    # -- 口径说明：写进结果里，别让读者自己猜 ------------------------------
    trial.notes.append(
        f"只测了编译选项指纹 {trial.group_signature} 的 {trial.measured_units} 个单元"
        f"（全项目 {trial.total_units} 个编译成功的单元里有 "
        f"{trial.candidate_units} 个包含至少一个 PCH 头）。"
        "不同指纹的单元无法共享同一份 .gch，所以这个分数不能外推到全项目。"
    )
    trial.notes.append(
        f"每侧编译 {runs} 次取最快的一次；计时是进程墙钟，"
        "含进程启动开销（那部分 PCH 省不掉），因此实测收益低于解析工作量的减少。"
    )
    if not full_compile:
        trial.notes.append("本次为 -fsyntax-only 口径，不含代码生成与优化阶段。")
    else:
        trial.notes.append("本次为完整编译口径（-c 产出目标文件）。")
    not_adopted = sum(1 for unit in trial.units if unit.pch_ok and not unit.pch_adopted)
    if not_adopted:
        trial.notes.append(
            f"{not_adopted} 个单元编译通过但编译器未采用 .gch，其耗时已计入基线侧、"
            "不计入收益。"
        )
    broken = sum(1 for unit in trial.units if unit.baseline_ok and not unit.pch_ok)
    if broken:
        trial.notes.append(
            f"{broken} 个单元原本能编过、加上 PCH 后编不过——这是采用 PCH 的真实代价，"
            "估算里看不到。"
        )
    return trial


def _adopt_verdict(pch_tu: TranslationUnit, stderr: str) -> bool | None:
    """编译器到底用没用上 .gch？

    返回 ``True`` 用了、``False`` 没用、``None`` **无从判断**——第三种情况
    必须存在：``-Winvalid-pch`` 不被识别时，输出里既没有拒绝也没有确认，
    把它当成任何一种都是编造。调用方对 ``None`` 要换判据（见
    :func:`run_pch_trial`），不能直接当 ``True``。
    """
    if not pch_tu.ok:
        return False
    if _UNKNOWN_WARNING.search(stderr or ""):
        return None
    # 注意「没有输出」是**好消息**：-Winvalid-pch 只在拒绝 .gch 时开口，
    # 采用成功时它一句话都不说。把它判成「未采用」会把每一次成功的实测
    # 都记成失败——这个错误方向恰好是「数字变难看」，所以不会有人来报。
    return not _PCH_REFUSED.search(stderr or "")


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""
