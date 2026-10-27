"""Tests for the PCH *measurement* path (``attrib/pchbuild.py``).

These tests deliberately use a fake driver: the property being tested is the
measurement protocol (grouping, adoption detection, aggregation), not whether
g++ works.  A real-compiler run is at the bottom and skips when no compiler is
installed.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from heavyheader.attrib.pch import PCHRecommendation
from heavyheader.attrib.pchbuild import (
    PCHTrial,
    PCHUnitTrial,
    _adopt_verdict,
    _write_umbrella,
    option_signature,
    run_pch_trial,
)
from heavyheader.drivers.base import CompilerDriver, DriverOptions, MsvcDriver
from heavyheader.models import (
    CompilerFamily,
    IncludeEvent,
    ProjectScan,
    TranslationUnit,
)

BASELINE_SECONDS = 1.0
PCH_SECONDS = 0.6


class FakeDriver(CompilerDriver):
    """A driver that reports fixed times and lets the test script its stderr.

    ``pch_stderr`` is what makes the adoption logic testable: the compiler's
    only way of saying "I did not use your .gch" is a line in stderr, so that
    line has to be controllable.
    """

    family = CompilerFamily.GCC

    def __init__(self, pch_stderr: str = "", fail_pch: bool = False) -> None:
        self.executable = "fake-g++"
        self.pch_stderr = pch_stderr
        self.fail_pch = fail_pch
        self.build_argv: list[str] | None = None

    def build_command(self, source, options):
        argv = ["fake-g++", str(source)]
        argv += list(options.extra_flags)
        return argv

    def build_pch_command(self, header, options, output):
        self.build_argv = ["fake-g++", "-x", "c++-header", str(header), "-o", str(output)]
        return self.build_argv

    def pch_use_flags(self, header):
        return ["-include", str(header)]

    def spawn(self, argv, cwd=None, timeout=300.0):
        # 生成 .gch 的调用：把「产物」真的写出来，让 run_pch_trial 的落盘检查
        # 走的是真实分支（返回码 0 但文件不存在必须被抓出来）。
        output = Path(argv[-1])
        if not self.fail_pch:
            output.write_bytes(b"fake-gch")
        return (
            subprocess.CompletedProcess(argv, 0 if not self.fail_pch else 1, "", ""),
            0.05,
            1,
        )

    def interpret(self, source, returncode, stdout, stderr, wall_seconds):
        if returncode != 0:
            return TranslationUnit(source=source, error="fake failure", wall_seconds=0.1)
        used_pch = any("include" in token for token in (stdout + stderr).split())
        return TranslationUnit(
            source=source,
            wall_seconds=PCH_SECONDS if used_pch else BASELINE_SECONDS,
        )

    def run_verbose(self, source, options, cwd=None, timeout=300.0):
        argv = self.build_command(source, options)
        used_pch = any("-include" in token for token in argv)
        return (
            TranslationUnit(
                source=source,
                wall_seconds=PCH_SECONDS if used_pch else BASELINE_SECONDS,
            ),
            self.pch_stderr if used_pch else "",
        )


def _tu(name: str, header: Path, options: DriverOptions | None = None) -> TranslationUnit:
    tu = TranslationUnit(
        source=Path(name),
        include_events=[IncludeEvent(includer=Path(name), included=header, depth=0, line=1)],
    )
    tu.compile_options = options
    tu.compile_cwd = None
    return tu


def _scan(tus: list[TranslationUnit], root: Path) -> ProjectScan:
    return ProjectScan(
        root=root,
        compiler="fake-g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=tus,
    )


def _recommendation(header: Path) -> PCHRecommendation:
    return PCHRecommendation(
        headers=[header],
        closure_headers=[header],
        closure_lines=100,
        eligible_tus=2,
        total_tus=2,
        net_lines_saved=100,
        percent_of_project=20.0,
    )


# -- 编译选项指纹 ---------------------------------------------------------


def test_option_signature_splits_units_that_cannot_share_a_gch():
    """差一个 -D 就不能共享 .gch——指纹必须把这两种单元分开。

    这是整套实测的地基：把不能共享 PCH 的单元放进同一次测量，得到的
    「收益」是一部分单元用了、另一部分没用的混合数，谁的真实构建都对不上。
    """
    base = DriverOptions(standard="c++17")
    assert option_signature(base) == option_signature(replace(base))
    assert option_signature(base) != option_signature(
        replace(base, defines=["NDEBUG"])
    )
    assert option_signature(None) == "unknown"


def test_trial_measures_only_one_flag_group(tmp_path):
    """只测指纹相同的一组，并在结果里说清测了多少、全项目有多少。"""
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    shared = DriverOptions(standard="c++17")
    odd = DriverOptions(standard="c++17", defines=["SPECIAL"])
    tus = [
        _tu("a.cpp", header, shared),
        _tu("b.cpp", header, shared),
        _tu("c.cpp", header, shared),
        _tu("d.cpp", header, odd),
    ]
    scan = _scan(tus, tmp_path)
    trial = run_pch_trial(
        scan,
        driver=FakeDriver(),
        recommendation=_recommendation(header),
        workdir=tmp_path / "work",
        runs=1,
    )
    assert trial.pch_built is True
    # 4 个单元都包含这个头，但只有 3 个编译选项一致。
    assert trial.candidate_units == 4
    assert trial.group_size == 3
    assert trial.measured_units == 3
    assert any("无法共享同一份 .gch" in note for note in trial.notes)


def test_trial_refuses_to_measure_a_group_of_one(tmp_path):
    """选项全都不同时不做测量——一个单元跟自己比，测出来的是噪声。"""
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    tus = [
        _tu("a.cpp", header, DriverOptions(defines=["A"])),
        _tu("b.cpp", header, DriverOptions(defines=["B"])),
    ]
    scan = _scan(tus, tmp_path)
    trial = run_pch_trial(
        scan,
        driver=FakeDriver(),
        recommendation=_recommendation(header),
        workdir=tmp_path / "work",
        runs=1,
    )
    assert trial.pch_built is False
    assert trial.units == []
    assert "编译选项一致的单元只有 1 个" in trial.pch_error


# -- 采用与否的判定 -------------------------------------------------------


def test_adoption_is_false_when_the_compiler_says_it_ignored_the_gch(tmp_path):
    """编译器说「没用」就必须算没用。

    这条是整套实测的命门：GCC 在不采用 .gch 时照常编译、退出码 0、耗时几乎
    不变，只看结果对象的话它和一次成功的实测长得一模一样。
    """
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    options = DriverOptions(standard="c++17")
    tus = [_tu("a.cpp", header, options), _tu("b.cpp", header, options)]
    scan = _scan(tus, tmp_path)
    trial = run_pch_trial(
        scan,
        driver=FakeDriver(pch_stderr="/tmp/pch.h.gch: not used because 'X' is defined"),
        recommendation=_recommendation(header),
        workdir=tmp_path / "work",
        runs=1,
    )
    assert trial.pch_built is True
    assert trial.adopted_units == 0
    # 没采用的单元不能给 PCH 记功：它们的耗时跟基线是一回事，差值是噪声。
    # 全组口径仍然如实报告（-include 加上去之后总耗时的变化），
    # 但「PCH 的功劳」必须是 0。
    assert trial.saved_seconds == 2 * (BASELINE_SECONDS - PCH_SECONDS)
    assert trial.adopted_saved_seconds == 0.0
    assert trial.adopted_saved_percent == 0.0
    assert any("未采用 .gch" in note for note in trial.notes)


def test_include_context_lines_are_not_read_as_a_refusal():
    """诊断上下文行里的 ``pch`` 不是「拒绝 .gch」。

    真实事故（json unit-allocator.cpp，2026-10-25）：stderr 里是
    ``In file included from …heavyheader_pch.h:2:`` 这类行，第一版正则把
    ``pch`` 当成 ``.gch``，只要同一行附近出现 ``invalid``（哪怕是一条无关的
    ``invalid use of …``）就判「未采用」——12 个单元错 1 个，而那个单元明明
    省了 484 ms。拒绝信号必须是紧跟 .gch 的原因。
    """
    ok = TranslationUnit(source=Path("unit-allocator.cpp"))
    stderr = (
        "In file included from C:/tmp/heavyheader_pch.h:2:\n"
        "In file included from C:/tmp/heavyheader_pch.h:3:\n"
        "unit-allocator.cpp:42:5: error: invalid use of incomplete type "
        "'struct Foo'  /* not used here */\n"
    )
    assert _adopt_verdict(ok, stderr) is True
    # 真正的拒绝仍然要抓得住。
    assert (
        _adopt_verdict(
            ok, "C:/tmp/pch.h.gch: not used because 'JSON_USE_GLOBAL' is defined"
        )
        is False
    )
    assert (
        _adopt_verdict(ok, "warning: precompiled header 'pch.h.gch' was ignored")
        is False
    )


def test_unknown_warning_flag_falls_back_to_measured_speedup(tmp_path):
    """-Winvalid-pch 不被识别时，沉默不能当成「采用了」。"""
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    options = DriverOptions(standard="c++17")
    tus = [_tu("a.cpp", header, options), _tu("b.cpp", header, options)]
    scan = _scan(tus, tmp_path)
    trial = run_pch_trial(
        scan,
        driver=FakeDriver(
            pch_stderr="warning: unknown warning option '-Winvalid-pch'"
        ),
        recommendation=_recommendation(header),
        workdir=tmp_path / "work",
        runs=1,
    )
    # 假驱动在有 PCH 时总是更快，所以回退判据应该判「采用了」。
    assert trial.adopted_units == 2
    assert trial.units[0].note and "不被识别" in trial.units[0].note


def test_adopt_verdict_distinguishes_unknown_from_adopted():
    ok = TranslationUnit(source=Path("a.cpp"))
    # 没有输出 = -Winvalid-pch 没开口 = 采用了。判成 False 会把每一次成功的
    # 实测都记成失败，而那个方向只会让数字变难看，没人会来报。
    assert _adopt_verdict(ok, "") is True
    assert _adopt_verdict(ok, "pch.h.gch: not used because X") is False
    assert _adopt_verdict(ok, "nothing to say here") is True
    assert _adopt_verdict(ok, "unknown warning option '-Winvalid-pch'") is None
    failed = TranslationUnit(source=Path("a.cpp"), error="boom")
    assert _adopt_verdict(failed, "whatever") is False


# -- 聚合 -----------------------------------------------------------------


def test_aggregates_report_saving_and_break_even(tmp_path):
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    options = DriverOptions(standard="c++17")
    tus = [_tu("a.cpp", header, options), _tu("b.cpp", header, options)]
    scan = _scan(tus, tmp_path)
    trial = run_pch_trial(
        scan,
        driver=FakeDriver(),
        recommendation=_recommendation(header),
        workdir=tmp_path / "work",
        runs=1,
    )
    assert trial.measured_units == 2
    assert trial.baseline_seconds == 2 * BASELINE_SECONDS
    assert trial.pch_seconds == 2 * PCH_SECONDS
    assert trial.saved_percent == 40.0
    assert trial.adopted_saved_percent == 40.0
    assert trial.measured_coverage == 100.0
    assert trial.agreement == 100.0
    # 每单元省 0.4 s，生成 .gch 花 0.05 s → 一个单元就回本。
    assert trial.break_even_units == 1
    assert trial.is_conclusive is True
    assert trial.to_dict()["saved_percent"] == 40.0


def test_umbrella_uses_posix_absolute_paths(tmp_path):
    """Windows 反斜杠在 #include 里是转义序列，.gch 会静默失效。"""
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    target = tmp_path / "pch.h"
    _write_umbrella(target, [header])
    text = target.read_text(encoding="utf-8")
    assert header.as_posix() in text
    assert "\\" not in text.split("\n")[-2]


def test_trial_reports_build_failure_without_claiming_zero_saving(tmp_path):
    """生成失败要写成「没测成」，不能写成 0 收益。"""
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    options = DriverOptions(standard="c++17")
    tus = [_tu("a.cpp", header, options), _tu("b.cpp", header, options)]
    scan = _scan(tus, tmp_path)
    trial = run_pch_trial(
        scan,
        driver=FakeDriver(fail_pch=True),
        recommendation=_recommendation(header),
        workdir=tmp_path / "work",
        runs=1,
    )
    assert trial.pch_built is False
    assert trial.units == []
    assert "生成预编译头失败" in trial.pch_error


def test_msvc_reports_unsupported_instead_of_a_fake_measurement(tmp_path):
    """MSVC 的 PCH 机制不是 -include，硬套会产出「编译过但 PCH 从未生效」的假实测。"""
    header = tmp_path / "common.hpp"
    header.write_text("#pragma once\n", encoding="utf-8")
    options = DriverOptions(standard="c++17")
    tus = [_tu("a.cpp", header, options), _tu("b.cpp", header, options)]
    scan = _scan(tus, tmp_path)
    trial = run_pch_trial(
        scan,
        driver=MsvcDriver(executable="cl"),
        recommendation=_recommendation(header),
        workdir=tmp_path / "work",
        runs=1,
    )
    assert trial.pch_built is False
    assert "没有可用的预编译头机制" in trial.pch_error


def test_real_compiler_produces_a_gch_the_compiler_accepts(tmp_path):
    """真机集成：造语料 → 生成 .gch → 重编，确认编译器真的采用了它。

    假驱动能测协议，测不出「-x c++-header + -include 这套参数到底管不管用」。
    这条测试守的是后者：任何一处参数拼错，编译器会静默不用 .gch，而其余
    一切看起来都正常。
    """
    if shutil.which("g++") is None:
        pytest.skip("需要 g++")

    root = tmp_path / "corpus"
    (root / "include").mkdir(parents=True)
    (root / "include" / "big.hpp").write_text(
        "#pragma once\n"
        + "".join(f"inline int heavy_{i}(int x) {{ return x + {i}; }}\n" for i in range(2000)),
        encoding="utf-8",
    )
    (root / "include" / "common.hpp").write_text(
        '#pragma once\n#include "big.hpp"\nstruct Cfg { int n; };\n', encoding="utf-8"
    )
    for index in range(3):
        (root / f"u{index}.cpp").write_text(
            f'#include "common.hpp"\nint u{index}() {{ return heavy_{index}(1); }}\n',
            encoding="utf-8",
        )

    from heavyheader.drivers.base import detect_driver
    from heavyheader.scanner import ScanOptions, scan_project

    driver = detect_driver("g++")
    scan = scan_project(
        ScanOptions(
            root=root,
            compiler="g++",
            standard="c++17",
            include_dirs=[root / "include"],
            jobs=1,
        ),
        driver=driver,
    )
    workdir = tmp_path / "work"
    trial = run_pch_trial(scan, driver=driver, workdir=workdir, runs=1)

    assert trial.pch_built is True, trial.pch_error
    assert trial.measured_units == 3
    # 三个单元编译选项完全一致，编译器没有理由拒绝这份 .gch。
    assert trial.adopted_units == 3, [unit.note for unit in trial.units]
    assert (workdir / "heavyheader_pch.h.gch").is_file()
    assert trial.pch_build_seconds > 0.0


def test_no_headers_is_reported_not_measured(tmp_path):
    scan = _scan([], tmp_path)
    trial = run_pch_trial(
        scan,
        driver=FakeDriver(),
        recommendation=PCHRecommendation(
            headers=[], closure_headers=[], closure_lines=0, eligible_tus=0,
            total_tus=0, net_lines_saved=0, percent_of_project=0.0,
        ),
        workdir=tmp_path / "work",
        runs=1,
    )
    assert trial.pch_built is False
    assert "没有给出任何候选头" in trial.pch_error
    assert trial.to_dict()["conclusive"] is False


# --------------------------------------------------------------------------
# 预警与实测的交叉表（2x2）
#
# 这组统计是整个「预置宏预警」能力的成绩单，公式错了不会崩、只会给出一个看起来
# 合理的百分比。所以它必须被单独盯住，而不是靠归档里的数字背书。
# --------------------------------------------------------------------------


def _trials(*rows: tuple[bool, bool]) -> PCHTrial:
    """按 ``(macro_prefixed, pch_ok)`` 造一批单元。"""
    trial = PCHTrial(compiler="fake-g++", family="gcc", headers=[Path("pch.h")])
    for index, (prefixed, ok) in enumerate(rows):
        trial.units.append(
            PCHUnitTrial(
                source=Path(f"u{index}.cpp"),
                baseline_seconds=1.0,
                pch_seconds=0.9 if ok else 0.0,
                baseline_ok=True,
                pch_ok=ok,
                pch_adopted=ok,
                macro_prefixed=prefixed,
            )
        )
    return trial


def test_cross_table_reproduces_the_json_measurement():
    """json 那次实测的形状：9 命中且编不过、1 命中但编过、0 漏报、14 干净。"""
    trial = _trials(*([(True, False)] * 9), (True, True), *([(False, True)] * 14))
    assert trial.macro_prefixed_count == 10
    assert (trial.prefixed_and_broken, trial.prefixed_and_ok) == (9, 1)
    assert (trial.clean_and_broken, trial.clean_and_ok) == (0, 14)
    assert trial.warning_recall == pytest.approx(100.0)
    assert trial.warning_precision == pytest.approx(90.0)


def test_cross_table_counts_a_miss_against_recall():
    """漏报必须拉低召回——召回是这张表存在的主要理由。"""
    trial = _trials((True, False), (False, False), (False, True))
    # 编不过的 2 个里有 1 个被预警覆盖。
    assert trial.warning_recall == pytest.approx(50.0)
    assert trial.clean_and_broken == 1


def test_cross_table_does_not_call_an_empty_prediction_perfect():
    """一条都没预警时精确率是 0，不是 100——「空集合上恒真」是这个项目栽过的坑。"""
    trial = _trials((False, False), (False, True))
    assert trial.macro_prefixed_count == 0
    assert trial.warning_precision == pytest.approx(0.0)


def test_cross_table_does_not_call_nothing_broken_full_recall():
    """没有任何单元编不过时召回率是 0，不是 100：分母为空等于「没测到」。"""
    trial = _trials((True, True), (False, True))
    assert trial.clean_and_broken + trial.prefixed_and_broken == 0
    assert trial.warning_recall == pytest.approx(0.0)
