"""Tests for compiler driver detection.

``detect_driver`` is the first thing that runs against the user's machine, so
its failure modes are the user's first impression.  It once raised
``AttributeError`` from inside its own error handler (it caught
``subprocess.SubprocessExpired``, an attribute that does not exist), which
turned "compiler not found" into a traceback.  These tests pin down the
probing path — including the failure branch — so that cannot come back.
"""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from heavyheader.drivers.base import (
    CompilationError,
    GccLikeDriver,
    MsvcDriver,
    detect_driver,
)


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
