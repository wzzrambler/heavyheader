"""Tests for the scan orchestration layer.

``scan_project`` 并行编译成百上千个编译单元，它自己的健壮性决定了「一次
跑不通」时用户看到的是可用结果还是回溯。这里盯住两类真实故障：

1. **失效的工作目录**：``compile_commands.json`` 里的 ``directory`` 指向已被
   删除的构建目录（先生成再清理 build 目录是常态）。原样透传会让 subprocess
   抛 ``NotADirectoryError``——那不是 ``CompilationError``，线程池接不住；
2. **单个编译单元的意外异常**：以前线程池只接 ``CompilationError``，其余异常
   穿透出去，500 个 TU 里 1 条坏记录就能带走整次扫描及其余已完成的结果。

两类都用替身驱动验证，不依赖真实编译器。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heavyheader.drivers.base import CompilerDriver, CompilationError, DriverOptions
from heavyheader.models import CompilerFamily, TranslationUnit
from heavyheader.scanner import (
    ScanOptions,
    _tasks_from_compile_commands,
    scan_project,
)


class _StubDriver(CompilerDriver):
    """Returns an empty TU without touching a compiler."""

    family = CompilerFamily.GCC

    def __init__(self, error: Exception | None = None, spawn_retries: int = 0):
        self.error = error
        self.spawn_retries = spawn_retries
        self.executable = "g++"
        self.calls: list[tuple[Path, Path | None]] = []

    def run(self, source, options, cwd=None, timeout=300.0) -> TranslationUnit:
        self.calls.append((Path(source), Path(cwd) if cwd else None))
        if self.error is not None:
            raise self.error
        tu = TranslationUnit(source=Path(source), dependencies=[])
        tu.spawn_retries = self.spawn_retries
        return tu

    def build_command(self, source, options) -> list[str]:  # pragma: no cover
        return ["g++", str(source)]

    def interpret(self, source, returncode, stdout, stderr, wall_seconds):
        return TranslationUnit(source=Path(source), dependencies=[])


def _write_project(root: Path) -> Path:
    """A two-file project plus a compilation database."""
    (root / "src").mkdir()
    for i in (1, 2):
        (root / "src" / f"f{i}.cpp").write_text(
            f"int f{i}() {{ return {i}; }}\n", encoding="utf-8"
        )
    db = [
        {
            "directory": str(root),
            "command": "g++ -std=c++17 -c src/f1.cpp",
            "file": "src/f1.cpp",
        },
        {
            # 构建目录已被删除
            "directory": str(root / "build-gone"),
            "command": "g++ -std=c++17 -c src/f2.cpp",
            "file": "src/f2.cpp",
        },
    ]
    cc_path = root / "compile_commands.json"
    cc_path.write_text(json.dumps(db, indent=1), encoding="utf-8")
    return cc_path


def test_stale_directory_falls_back_to_the_scan_root(tmp_path):
    """Failure mode 1: a deleted build directory must not reach subprocess."""
    cc_path = _write_project(tmp_path)

    tasks = _tasks_from_compile_commands(cc_path, max_tus=10, root=tmp_path)

    assert len(tasks) == 2
    assert tasks[0].cwd == tmp_path
    assert tasks[1].cwd == tmp_path, "失效的 directory 应退回到扫描根，而不是原样透传"


def test_scan_survives_a_stale_compilation_database(tmp_path):
    """The end-to-end version: the scan must complete, not crash."""
    cc_path = _write_project(tmp_path)
    driver = _StubDriver()

    scan = scan_project(
        ScanOptions(root=tmp_path, compile_commands=cc_path, jobs=1), driver=driver
    )

    assert len(scan.ok_tus) == 2
    assert scan.failed_tus == []
    assert all(cwd == tmp_path for _, cwd in driver.calls)


def test_one_bad_translation_unit_does_not_destroy_the_scan(tmp_path):
    """Failure mode 2: an unexpected exception is a unit failure, not a crash."""
    cc_path = _write_project(tmp_path)
    # 一个既非 CompilationError 也非 OSError 的意外异常。
    driver = _StubDriver(error=RuntimeError("something nobody anticipated"))

    scan = scan_project(
        ScanOptions(root=tmp_path, compile_commands=cc_path, jobs=1), driver=driver
    )

    # 扫描必须完成，且把每个单元都记为失败而不是抛出。
    assert len(scan.tus) == 2
    assert len(scan.failed_tus) == 2
    assert all("RuntimeError" in str(tu.error) for tu in scan.failed_tus)


def test_compilation_errors_are_still_recorded_as_failures(tmp_path):
    """Guarding against over-broadening: real compile errors keep their path."""
    cc_path = _write_project(tmp_path)
    driver = _StubDriver(error=CompilationError("compiler not found"))

    scan = scan_project(
        ScanOptions(root=tmp_path, compile_commands=cc_path, jobs=1), driver=driver
    )

    assert len(scan.failed_tus) == 2
    assert "compiler not found" in str(scan.failed_tus[0].error)


def test_missing_compilation_database_falls_back_to_discovery(tmp_path):
    """No compile_commands.json: the recursive walk must still find sources."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.cpp").write_text("int a() { return 1; }\n", encoding="utf-8")

    scan = scan_project(
        ScanOptions(root=tmp_path, jobs=1), driver=_StubDriver()
    )

    assert len(scan.ok_tus) == 1


def test_warns_when_a_tu_needed_a_spawn_retry(tmp_path):
    """A TU that only compiled after a launch retry should be flagged, not hidden.

    这正是之前「偶发 TU 失败」的根因：系统资源紧张启动不起来，重试一次就成了。
    修复后它算作成功，但扫描应当提示用户——而不是悄悄把它当正常结果。
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.cpp").write_text("int a() { return 1; }\n", encoding="utf-8")

    scan = scan_project(
        ScanOptions(root=tmp_path, jobs=1), driver=_StubDriver(spawn_retries=1)
    )

    assert len(scan.ok_tus) == 1
    assert any("自动重试成功" in w for w in scan.warnings)


def test_no_retry_warning_when_all_tus_launched_cleanly(tmp_path):
    """没有 TU 遇到启动重试时，不应出现那条提示。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.cpp").write_text("int a() { return 1; }\n", encoding="utf-8")

    scan = scan_project(
        ScanOptions(root=tmp_path, jobs=1), driver=_StubDriver(spawn_retries=0)
    )

    assert not any("自动重试成功" in w for w in scan.warnings)


def test_windows_backslash_paths_in_compile_commands_survive_tokenising(tmp_path):
    """Windows 路径里的反斜杠不能被当作 shell 转义符吃掉。

    真实故障：MSBuild 生成的 compile_commands.json 写的是
    ``-I C:\\sdk\\include``；按 posix 规则切分会得到 ``C:sdkinclude``，
    于是每个头文件搜索路径都指向不存在的目录，**全部编译单元编译失败**，
    而扫描只报告「0 个单元」。用户会以为工具不工作，实际是路径被解析吃掉了。
    """
    db = [
        {
            "directory": str(tmp_path),
            "command": (
                'g++ -IC:\\sdk\\include -I"C:\\Program Files\\sdk\\inc" '
                "-DFOO=1 -c src\\f1.cpp"
            ),
            "file": "src/f1.cpp",
        }
    ]
    cc_path = tmp_path / "compile_commands.json"
    cc_path.write_text(json.dumps(db), encoding="utf-8")

    tasks = _tasks_from_compile_commands(cc_path, max_tus=10, root=tmp_path)

    assert len(tasks) == 1
    flags = tasks[0].options.extra_flags
    assert "-IC:\\sdk\\include" in flags
    # 引号内的空格不切分：整条 `-I...` 保持是一个参数。
    assert "-IC:\\Program Files\\sdk\\inc" in flags
    assert "-DFOO=1" in flags
    assert "src\\f1.cpp" in flags
