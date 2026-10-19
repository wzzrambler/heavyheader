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
    """同一份源码放在两个目录，指纹必须相同（否则判据永远为「不同」）。

    故意让源码里出现 ``__FILE__``：真实工程（doctest）用它拼错误信息，两份源码
    目录不同时字符串常量、乃至它的符号名都会变。``compare`` 把两份源码落到同一个
    路径再编，所以这种差异根本不会出现。
    """
    if CLANG is None:
        pytest.skip("需要 clang++")
    driver = detect_driver(CLANG)
    text = 'const char *where() { return __FILE__; }\nint main() { return where()[0]; }\n'
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


def test_fingerprint_ignores_debug_info(tmp_path):
    """带 ``-g`` 时也必须判等价。

    这是实测踩到的一整轮误报：编译数据库里带 ``-g`` 时，汇编里会写
    ``.cv_file`` / ``.cv_loc`` 以及 ``.debug$S`` 里的源文件路径字符串。
    删掉一条 include 后路径没变、代码没变，但调试信息里的**文件名与行表**
    变了，于是 24 个候选全被判成「代码变了」。闸门靠 ``-g0`` 压掉调试信息，
    再叠一层路径掩码（汇编里的路径是双反斜杠转义形式）。
    """
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

    options = DriverOptions(extra_flags=["-g"])
    before, after = compare(
        driver, first, second, options, None, tmp_path / "work"
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
    # -g0 必须在 -S 之前、且晚于命令行里可能已有的 -g，否则调试信息会带进哈希
    assert argv[-1] == "-S"
    assert "-g0" in argv
    assert argv.index("-g0") < argv.index("-S")


def test_gate_is_unavailable_on_msvc():
    """MSVC 没有 ``-S``/``-g0`` 这套写法。

    能力不够时不判、不跑：硬开会把每个候选都判成「编译失败 → 代码变了」，
    比不开闸门更糟。调用方据此决定是否声称做过校验。
    """
    from heavyheader.codegen import supported
    from heavyheader.models import CompilerFamily

    driver = _RecordingDriver()
    driver.family = CompilerFamily.MSVC
    assert supported(driver) is False
    driver.family = CompilerFamily.GCC
    assert supported(driver) is True
    driver.family = CompilerFamily.CLANG
    assert supported(driver) is True
