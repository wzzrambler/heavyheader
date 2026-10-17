"""Tests for compiler driver detection and the spawn-retry robustness.

``detect_driver`` is the first thing that runs against the user's machine, so
its failure modes are the user's first impression.  It once raised
``AttributeError`` from inside its own error handler (it caught
``subprocess.SubprocessExpired``, an attribute that does not exist), which
turned "compiler not found" into a traceback.  These tests pin down the
probing path — including the failure branch — so that cannot come back.

The retry tests pin down the fix for the *intermittent TU failure*: when many
compiles run in parallel, the OS occasionally refuses to spawn a compiler
process (``[WinError 1455]``, ``EMFILE``…).  That is a launch failure, not a
compile failure, and must be retried rather than recorded as a failed TU.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from heavyheader.drivers.base import (
    CompilationError,
    DriverOptions,
    GccLikeDriver,
    MsvcDriver,
    _MAX_SPAWN_ATTEMPTS,
    detect_driver,
)

_OK_RUN = subprocess.CompletedProcess(args=["g++"], returncode=0, stdout="", stderr="")


def _gcc_driver() -> GccLikeDriver:
    return GccLikeDriver("g++")


def test_detects_gnu_by_name():
    driver = detect_driver("g++")
    assert isinstance(driver, GccLikeDriver)
    assert driver.is_clang is False


def test_detects_clang_by_name():
    driver = detect_driver("clang++")
    assert isinstance(driver, GccLikeDriver)
    assert driver.is_clang is True


def test_detects_msvc_by_name():
    assert isinstance(detect_driver("cl.exe"), MsvcDriver)


def test_msvc_driver_forces_english_diagnostics_via_vslang():
    """On a localized Windows, cl emits e.g. Chinese 'Note: including file:'

    and GBK-encoded output, which breaks ``parse_show_includes`` and the
    utf-8 decode in ``run``.  MsvcDriver must set ``VSLANG=1033`` so cl
    prints English ASCII diagnostics regardless of the machine locale.
    """
    env = MsvcDriver()._subprocess_env()
    assert env is not None
    assert env.get("VSLANG") == "1033"
    # It must still inherit the surrounding environment, not replace it.
    assert env.get("PATH") == os.environ.get("PATH")


def test_gcc_like_driver_inherits_environment_unchanged():
    """GCC/Clang need no locale tweaks; their env hook stays a plain inherit."""
    assert GccLikeDriver("g++")._subprocess_env() is None


def test_missing_compiler_gives_compilation_error_not_attribute_error():
    """The regression: probing used to blow up inside its own except clause.

    ``subprocess.SubprocessExpired`` does not exist, so a missing executable
    surfaced as AttributeError rather than a message the user can act on.
    """
    with pytest.raises(CompilationError) as excinfo:
        detect_driver("definitely-not-a-real-compiler-xyz")

    message = str(excinfo.value)
    assert "definitely-not-a-real-compiler-xyz" in message
    assert "cannot probe" in message


def test_probe_error_when_compiler_cannot_be_classified():
    """A compiler that runs but is unrecognisable must fail loudly, not guess."""
    with mock.patch.object(
        subprocess,
        "run",
        return_value=subprocess.CompletedProcess(
            args=["weirdcc", "--version"],
            returncode=0,
            stdout="weirdcc 1.0 (some proprietary toolchain)\n",
            stderr="",
        ),
    ):
        with pytest.raises(CompilationError) as excinfo:
            detect_driver("weirdcc")

    assert "cannot tell which compiler" in str(excinfo.value)


# 横幅取自本机 g++ 16.2.0 的逐字输出——注意 MinGW 的第一行既没有 "gcc"
# 也没有 FSF，只有读到第二行才能判定。
_MINGW_GPP_BANNER = (
    "g++.exe (x86_64-win32-seh-rev1, Built by MinGW-Builds project) 16.2.0\n"
    "Copyright (C) 2026 Free Software Foundation, Inc.\n"
    "This is free software; see the source for copying conditions.  There is NO\n"
    "warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.\n"
)


@pytest.mark.parametrize(
    "banner, expect_clang",
    [
        ("clang version 17.0.6 (https://github.com/llvm/llvm-project)\n", True),
        (_MINGW_GPP_BANNER, False),
        (
            "arm-linux-gnueabihf-g++ (GCC) 13.2.0\n"
            "Copyright (C) 2023 Free Software Foundation, Inc.\n",
            False,
        ),
        # 厂商构建：整段横幅里既没有 "gcc" 也没有 FSF 字样，只能靠
        # g++/MinGW 特征串识别。这是交叉编译工具链的实际形态。
        ("vendor-g++ (Vendor Toolchain) 12.1\nBuilt by MinGW-Builds project\n", False),
    ],
)
def test_probes_ambiguous_executables_by_banner(banner, expect_clang):
    """Wrappers and cross-compilers are named unpredictably; ask them."""
    with mock.patch.object(
        subprocess,
        "run",
        return_value=subprocess.CompletedProcess(
            args=["cross-cc", "--version"], returncode=0, stdout=banner, stderr=""
        ),
    ) as run_mock:
        driver = detect_driver("cross-cc")

    assert isinstance(driver, GccLikeDriver)
    assert driver.is_clang is expect_clang
    run_mock.assert_called_once()
    assert run_mock.call_args[0][0] == ["cross-cc", "--version"]


def test_probe_timeout_is_reported_as_compilation_error():
    """A hanging probe must not hang the scan; it is a SubprocessError subclass."""
    with mock.patch.object(
        subprocess, "run", side_effect=subprocess.TimeoutExpired(cmd="cc", timeout=30)
    ):
        with pytest.raises(CompilationError):
            detect_driver("cross-cc")


# --------------------------------------------------------------------------
# spawn-retry robustness (the intermittent-TU-failure fix)
# --------------------------------------------------------------------------
#
# 这些测试不碰真实编译器：直接替掉 subprocess.run，让它前若干次抛 OSError，
# 模拟「系统资源紧张启动不起来」的瞬时故障，验证 driver.run 会重试并最终把
# TU 算作成功，而不是误记成失败。


def test_run_retries_transient_spawn_failure_then_succeeds():
    """第一次启动失败（OSError），重试后成功——TU 必须算作 ok。"""
    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("[WinError 1455] The paging file is too small")
        return _OK_RUN

    with mock.patch.object(subprocess, "run", side_effect=flaky):
        tu = _gcc_driver().run(Path("a.cpp"), DriverOptions())

    assert tu.ok
    assert tu.spawn_retries == 1
    assert calls["n"] == 2


def test_run_retries_several_times_then_succeeds():
    """连续两次瞬时失败，第三次成功——重试次数被记下来。"""
    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("Resource temporarily unavailable")
        return _OK_RUN

    with mock.patch.object(subprocess, "run", side_effect=flaky):
        tu = _gcc_driver().run(Path("a.cpp"), DriverOptions())

    assert tu.ok
    assert tu.spawn_retries == 2
    assert calls["n"] == 3


def test_run_gives_up_after_exhausting_retries():
    """资源持续紧张、每次都启动失败：在耗尽重试次数后如实报失败。"""
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise OSError("Too many open files")

    with mock.patch.object(subprocess, "run", side_effect=boom):
        with pytest.raises(CompilationError) as exc:
            _gcc_driver().run(Path("a.cpp"), DriverOptions())

    assert "failed to start" in str(exc.value)
    assert calls["n"] == _MAX_SPAWN_ATTEMPTS


def test_run_does_not_retry_missing_compiler():
    """编译器根本不在 PATH 上——这是确定性的，不应浪费重试。"""
    calls = {"n": 0}

    def missing(*_a, **_k):
        calls["n"] += 1
        raise FileNotFoundError("g++ not found")

    with mock.patch.object(subprocess, "run", side_effect=missing):
        with pytest.raises(CompilationError) as exc:
            _gcc_driver().run(Path("a.cpp"), DriverOptions())

    assert "not found on PATH" in str(exc.value)
    assert calls["n"] == 1


def test_run_does_not_retry_timeout():
    """编译超时：再等一个超时没有意义，不应重试。"""
    calls = {"n": 0}

    def slow(*_a, **_k):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd=["g++"], timeout=1)

    with mock.patch.object(subprocess, "run", side_effect=slow):
        with pytest.raises(CompilationError) as exc:
            _gcc_driver().run(Path("a.cpp"), DriverOptions(), timeout=1.0)

    assert "timed out" in str(exc.value)
    assert calls["n"] == 1


def test_run_reports_zero_retries_on_clean_compile():
    """一帆风顺时 spawn_retries 为 0，不影响正常路径。"""
    with mock.patch.object(subprocess, "run", return_value=_OK_RUN):
        tu = _gcc_driver().run(Path("a.cpp"), DriverOptions())
    assert tu.ok
    assert tu.spawn_retries == 0


# --------------------------------------------------------------------------
# object-file measurement (the size dimension of the removal advice)
# --------------------------------------------------------------------------


def test_output_path_replaces_syntax_only_for_gcc():
    """``-o`` 与 ``-fsyntax-only`` 同时出现时 GCC 拒绝编译——
    体积测量必须显式剥掉语法检查开关，否则每次测量都是编译失败。"""
    out = Path("/tmp/probe.o")
    driver = _gcc_driver()
    argv = driver.build_command(
        Path("a.cpp"),
        DriverOptions(
            standard="c++17",
            include_dirs=[Path("/inc")],
            extra_flags=["-c", "-O2", "-MD", "-MF", "a.d"],
            output_path=out,
        ),
    )

    assert "-fsyntax-only" not in argv
    assert str(out) in argv
    assert argv[-1] == "a.cpp"
    # -o 与目标必须相邻成对，不能被 extra_flags 拆开。
    assert argv[argv.index("-o") + 1] == str(out)
    # 与 codegen 无关的参数必须原样保留，否则两次编译不可比。
    assert "-O2" in argv and "-c" in argv
    # 依赖文件参数必须剥掉：-MF 是相对路径时会写到调用方 cwd，污染工程。
    assert "-MD" not in argv and "-MF" not in argv and "a.d" not in argv


def test_output_path_keeps_every_other_flag_identical_for_gcc():
    """除了「产出 .o」必需的改动，两次编译必须逐字节同参数。"""
    driver = _gcc_driver()
    out = Path("/tmp/p.o")
    plain = driver.build_command(
        Path("a.cpp"), DriverOptions(standard="c++20", extra_flags=["-Wall", "-DFOO=1"])
    )
    sized = driver.build_command(
        Path("a.cpp"),
        DriverOptions(
            standard="c++20", extra_flags=["-Wall", "-DFOO=1"], output_path=out
        ),
    )

    assert set(plain) - {"-fsyntax-only"} == set(sized) - {"-o", str(out)}


def test_output_path_replaces_zs_for_msvc():
    """MSVC 的 ``/Zs`` 同样拒绝产出对象文件；``/Fo`` 是它的 ``-o``。"""
    from heavyheader.drivers.base import MsvcDriver

    out = Path("C:/tmp/probe.obj")
    argv = MsvcDriver().build_command(
        Path("a.cpp"),
        DriverOptions(
            standard="c++17",
            include_dirs=[Path("C:/inc")],
            extra_flags=["/c", "/O2", "/showIncludes"],
            output_path=out,
        ),
    )

    assert "/Zs" not in argv
    assert "/showIncludes" not in argv
    assert f"/Fo{out}" in argv
    assert "/c" in argv and "/O2" in argv


def test_source_is_never_passed_twice_even_if_extra_flags_carry_it():
    """driver 拥有源文件参数：extra_flags 里再带一份，GCC 会静默编译两遍
    并打出两条 TOTAL——读取方只取第一条，于是每个计时都可能是错的那半。
    这里是第二道防线（第一道在 scanner 构造任务时）。"""
    driver = _gcc_driver()
    argv = driver.build_command(
        Path("a.cpp"), DriverOptions(extra_flags=["-O2", "-c", "a.cpp"])
    )

    assert argv.count("a.cpp") == 1
    assert argv[-1] == "a.cpp"
    assert "-c" in argv  # 编译开关本身无罪，重复的源文件才有罪
