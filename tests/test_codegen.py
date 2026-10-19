"""代码等价校验的测试。

两个前提是踩出来的，必须有测试锁住：
1. 源码目录要掩掉——补丁写在临时目录，路径不同会让汇编的行标记不同；
2. 输出路径要固定——汇编里嵌着 ``# Object name``，换路径就变字节。
否则这个判据会把任何改动都判成「代码变了」，闸门等于永远关闭。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from heavyheader.codegen import assembly_argv, compare, fingerprint
from heavyheader.drivers.base import CompilerDriver, DriverOptions, detect_driver

CLANG = shutil.which("clang++")


def test_fingerprint_ignores_source_directory(tmp_path):
    """同一份源码放在两个目录，指纹必须相同（否则判据永远为「不同」）。"""
    if CLANG is None:
        pytest.skip("需要 clang++")
    driver = detect_driver(CLANG)
    text = "int f() { return 1; }\nint main() { return f(); }\n"
    first = tmp_path / "a" / "main.cpp"
    second = tmp_path / "b" / "main.cpp"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text(text, encoding="utf-8")
    second.write_text(text, encoding="utf-8")

    before, after = compare(
        driver, first, second, DriverOptions(), None, tmp_path / "work"
    )
    assert before.ok and after.ok, (before.note, after.note)
    assert before.same_as(after)


def test_fingerprint_detects_real_change(tmp_path):
    """真的改了代码就必须判为不同——闸门不能是摆设。"""
    if CLANG is None:
        pytest.skip("需要 clang++")
    driver = detect_driver(CLANG)
    original = tmp_path / "a" / "main.cpp"
    patched = tmp_path / "b" / "main.cpp"
    original.parent.mkdir()
    patched.parent.mkdir()
    original.write_text("int f() { return 1; }\n", encoding="utf-8")
    patched.write_text("int f() { return 2; }\n", encoding="utf-8")

    before, after = compare(
        driver, original, patched, DriverOptions(), None, tmp_path / "work"
    )
    assert before.ok and after.ok
    assert not before.same_as(after)


def test_fingerprint_reports_compile_failure(tmp_path):
    if CLANG is None:
        pytest.skip("需要 clang++")
    driver = detect_driver(CLANG)
    bad = tmp_path / "bad.cpp"
    bad.write_text("int main() { this is not c++; }\n", encoding="utf-8")
    result = fingerprint(
        driver, bad, DriverOptions(), None, tmp_path / "work" / "unit.s"
    )
    assert result.ok is False
    assert result.digest is None
    assert "失败" in result.note


class _RecordingDriver(CompilerDriver):
    """只记录 argv、不做真实编译的驱动。"""

    family = None  # type: ignore[assignment]

    def __init__(self) -> None:
        from heavyheader.models import CompilerFamily

        self.family = CompilerFamily.CLANG
        self.argv: list[str] = []

    def build_command(self, source: Path, options: DriverOptions) -> list[str]:
        self.argv = [
            "cc",
            "-c",
            "-fsyntax-only",
            "-H",
            "-o",
            str(options.output_path),
            str(source),
        ]
        return list(self.argv)

    def interpret(self, source, returncode, stdout, stderr, wall_seconds):  # pragma: no cover
        raise NotImplementedError


def test_conflicting_flags_are_stripped(tmp_path):
    """``-c`` / ``-fsyntax-only`` 会让 ``-S`` 失效，必须剥掉再补 ``-S``。"""
    driver = _RecordingDriver()
    argv = assembly_argv(
        driver, tmp_path / "x.cpp", DriverOptions(), tmp_path / "work" / "u.s"
    )
    assert "-c" not in argv
    assert "-fsyntax-only" not in argv
    assert argv[-1] == "-S"
