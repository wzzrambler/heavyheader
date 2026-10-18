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

from heavyheader.drivers.base import CompilationError, CompilerDriver
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
    # 源文件 token 必须被剥掉——driver 自己会追加它。
    # （这一条曾经断言的是「在 flags 里」，即把重复传参的 bug 当成了预期；
    # 见下方「the duplicated-source bug」一节的回归测试。）
    assert "src\\f1.cpp" not in flags


# --------------------------------------------------------------------------
# the duplicated-source bug (2026-10-18): one token, every measurement wrong
# --------------------------------------------------------------------------


def test_compile_commands_source_token_is_not_passed_twice(tmp_path):
    """``-c foo.cpp`` 留在 extra_flags 里，driver 又会追加一次源文件——
    GCC 静默接受、把文件编译两遍、打出两条 TOTAL，而读取方只拿第一条。
    扫描、探针、基线三层的计时与 include 树全部建立在这条错误命令上。

    修法：构造任务时就把数据库自带的源文件 token 剥掉（driver 拥有
    源文件参数）。这条测试锁住「extra_flags 里不出现源文件」。
    """
    source = tmp_path / "app.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    cc_path = tmp_path / "compile_commands.json"
    cc_path.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(source),
                    "command": f'g++ -std=c++17 -O0 -c "{source}" -o "{tmp_path / "app.o"}"',
                }
            ]
        ),
        encoding="utf-8",
    )

    tasks = _tasks_from_compile_commands(cc_path, max_tus=10, root=tmp_path)

    assert len(tasks) == 1
    extra = tasks[0].options.extra_flags
    assert str(source) not in extra
    assert "-c" in extra  # 编译开关保留，只有源文件 token 被剥掉
    assert "-o" not in extra  # 输出路径仍然要被剥掉（不污染源码树）


def test_windows_style_compile_commands_also_lose_the_duplicate(tmp_path):
    """反斜杠路径的数据库同样受影响——Windows 上这才是默认形态。"""
    source = tmp_path / "app.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    cc_path = tmp_path / "compile_commands.json"
    cc_path.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(source),
                    "command": f'g++ -std=c++17 -O0 -c {source} -o {tmp_path / "app.o"}',
                }
            ]
        ),
        encoding="utf-8",
    )

    tasks = _tasks_from_compile_commands(cc_path, max_tus=10, root=tmp_path)

    assert str(source) not in tasks[0].options.extra_flags


def test_cli_flags_are_appended_to_compile_commands_tasks(tmp_path):
    """``--flag`` 在 compile_commands 模式下必须生效，不能被静默丢弃。

    真实故障：用 clang 扫 Windows 工程时需要 ``--target=… --gcc-toolchain=…``
    才能绕开会默认链上的 MSVC 头。CLI 接受了参数、``--reproduce`` 也把它打印
    出来了，但 ``scan_project`` 只在「任务没有自带 flags」时才注入 CLI 参数，
    于是这两个 flag 在装配阶段被丢掉，25 个编译单元全部编译失败，而错误信息
    只是「找不到类型」之类，指向不了真正原因。

    断言语义：数据库自带 flags **保留**，CLI flags **追加**，两者都不丢。
    """
    source = tmp_path / "app.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    cc_path = tmp_path / "compile_commands.json"
    cc_path.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(source),
                    "command": f'g++ -std=c++17 -DFROM_DB=1 -c "{source}"',
                }
            ]
        ),
        encoding="utf-8",
    )

    seen: list[list[str]] = []

    class _Recording(CompilerDriver):
        family = CompilerFamily.GCC

        def __init__(self):
            self.executable = "g++"

        def run(self, source, options, cwd=None, timeout=300.0):
            seen.append(list(options.extra_flags))
            return TranslationUnit(source=Path(source), dependencies=[])

        def build_command(self, source, options):  # pragma: no cover
            return ["g++", str(source)]

        def interpret(self, source, returncode, stdout, stderr, wall_seconds):
            return TranslationUnit(source=Path(source), dependencies=[])

    scan_project(
        ScanOptions(
            root=tmp_path,
            compiler="g++",
            compile_commands=cc_path,
            max_tus=10,
            jobs=1,
            extra_flags=["--target=x86_64-w64-windows-gnu"],
        ),
        driver=_Recording(),
    )

    assert len(seen) == 1
    flags = seen[0]
    assert "-DFROM_DB=1" in flags  # 数据库自带参数保留
    assert "--target=x86_64-w64-windows-gnu" in flags  # CLI 参数被追加
