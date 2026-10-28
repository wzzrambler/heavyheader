"""Meson 集成：模块能配置、扫描目标能产出、门禁能拦。

**缺 meson / ninja / 可用的 C++ 编译器时 skip，不假装通过。** 这条测试的价值
在于它在有 meson 的环境里真跑一遍；没有的地方静默 pass 等于没测。

与 ``test_cmake_integration.py`` 的差别只有一处实质性的：**Meson 总是生成
``compile_commands.json``，不需要打开开关**。CMake 那边必须设
``CMAKE_EXPORT_COMPILE_COMMANDS``，所以 CMake 模块要为此发一条 warning，
这里不需要——少一个会静默失败的地方。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
#: 供 ``subdir()`` 引入的那份 meson.build。Meson 的 subdir 只接受源码树内的
#: 相对路径，所以测试把它复制到临时工程里——这与真实用法一致（用户也是把它
#: 放进自己的 vendor 目录）。
MODULE = REPO_ROOT / "meson" / "heavyheader" / "meson.build"

_ENCODING = {"encoding": "utf-8", "errors": "replace"}


def _meson_command() -> list[str] | None:
    """找到能用的 meson。

    pip 装的 meson 不一定进 PATH（Windows 上尤其如此），但提供
    ``python -m mesonbuild.mesonmain``，所以两种都试。
    """
    which = shutil.which("meson")
    if which:
        return [which]
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "mesonbuild.mesonmain", "--version"],
            capture_output=True,
            timeout=120,
            **_ENCODING,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode == 0:
        return [sys.executable, "-m", "mesonbuild.mesonmain"]
    return None


def _ninja_dir() -> str | None:
    """meson 靠 PATH 找 ninja，所以这里要拿到它的**目录**而不是可执行文件。"""
    which = shutil.which("ninja")
    if which:
        return str(Path(which).parent)
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import ninja; print(ninja.BIN_DIR)"],
            capture_output=True,
            timeout=120,
            **_ENCODING,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode == 0 and probe.stdout.strip():
        return probe.stdout.strip()
    return None


def _setup() -> tuple[list[str], dict[str, str]] | None:
    """返回 ``(meson 命令, 环境变量)``；环境不具备则返回 None。"""
    meson = _meson_command()
    if meson is None:
        return None
    ninja_dir = _ninja_dir()
    if ninja_dir is None:
        return None
    if not (shutil.which("c++") or shutil.which("g++") or shutil.which("cl")):
        return None
    env = dict(os.environ)
    env["PATH"] = ninja_dir + os.pathsep + env.get("PATH", "")
    return meson, env


_SETUP = _setup()

pytestmark = pytest.mark.skipif(
    _SETUP is None,
    reason="本机没有可用的 meson / ninja / C++ 编译器（缺一样这条测试就无从跑起）",
)


def _project(root: Path) -> Path:
    """一个最小 C++ 工程：一个源文件、一个头，足以跑通扫描。"""
    (root / "include").mkdir(parents=True, exist_ok=True)
    (root / "include" / "lib.h").write_text(
        "#pragma once\ninline int value() { return 1; }\n", encoding="utf-8"
    )
    (root / "main.cpp").write_text(
        '#include "lib.h"\nint main() { return value(); }\n', encoding="utf-8"
    )
    (root / "hh").mkdir(parents=True, exist_ok=True)
    (root / "hh" / "meson.build").write_text(
        MODULE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "meson.build").write_text(
        "\n".join(
            [
                "project('hh_demo', 'cpp')",
                "inc = include_directories('include')",
                "executable('app', 'main.cpp', include_directories: inc)",
                "heavyheader_baseline = 'hh-baseline.json'",
                "subdir('hh')",
            ]
        ),
        encoding="utf-8",
    )
    return root


def _run(*args: str) -> subprocess.CompletedProcess:
    meson, env = _SETUP  # type: ignore[misc]
    return subprocess.run(
        [*meson, *args],
        capture_output=True,
        timeout=900,
        env=env,
        **_ENCODING,
    )


def _configure(project: Path, build: Path) -> None:
    done = _run("setup", str(build), str(project))
    assert done.returncode == 0, done.stdout + done.stderr


def _compile(build: Path, target: str) -> subprocess.CompletedProcess:
    return _run("compile", "-C", str(build), target)


def test_module_configures_and_scan_target_produces_a_scan(tmp_path: Path):
    project = _project(tmp_path / "proj")
    build = tmp_path / "build"

    _configure(project, build)

    result = _compile(build, "heavyheader-scan")
    assert result.returncode == 0, result.stdout + result.stderr

    # 目标在 subdir 里，产物也就落在该子目录下。
    scan_json = build / "hh" / "heavyheader-scan.json"
    assert scan_json.exists(), "扫描目标没有产出 JSON——目标建了不等于命令跑对了"

    payload = json.loads(scan_json.read_text(encoding="utf-8"))
    units = payload.get("translation_units", [])
    assert units, "扫描到 0 个编译单元：多半是 compile_commands.json 没接上"
    assert any(u.get("ok", True) for u in units), "所有单元都编译失败，扫描结果不可用"


def test_gate_passes_against_its_own_baseline_and_blocks_on_regression(tmp_path: Path):
    """门禁必须两种状态都会：与自身基线比通过，与明显更低的基线比失败。

    只测「通过」是不够的——一个永远通过的门禁比没有门禁更糟，它会让人以为
    有人在看门。
    """
    project = _project(tmp_path / "proj")
    build = tmp_path / "build"
    _configure(project, build)
    assert _compile(build, "heavyheader-scan").returncode == 0

    baseline = build / "hh-baseline.json"
    shutil.copyfile(build / "hh" / "heavyheader-scan.json", baseline)
    passed = _compile(build, "heavyheader-gate")
    assert passed.returncode == 0, passed.stdout + passed.stderr

    # 把基线改小：门禁比的是「各头 attributed_lines 之和」，所以改的是
    # headers 而不是 totals（totals 只是展示用的汇总，改它不会改变判定）。
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    shrunk = 0
    for header in payload.get("headers") or []:
        lines = header.get("attributed_lines")
        if lines:
            header["attributed_lines"] = max(1, lines // 3)
            shrunk += 1
    assert shrunk, "基线里没有可改小的头，这条测试就测不到拦截了"
    baseline.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    blocked = _compile(build, "heavyheader-gate")
    assert blocked.returncode != 0, "回归 +200% 却没被拦住：门禁等于没装"


def test_module_keeps_scan_out_of_the_default_build(tmp_path: Path):
    """扫描不该进默认构建：它会把每个编译单元重新编译一遍。

    代价由每个人每次构建承担、收益只给关心构建成本的人——所以默认构建
    （``ninja`` 不带目标）必须不碰它。
    """
    project = _project(tmp_path / "proj")
    build = tmp_path / "build"
    _configure(project, build)

    default = _compile(build, "app")
    assert default.returncode == 0, default.stdout + default.stderr

    assert not (build / "hh" / "heavyheader-scan.json").exists(), (
        "默认构建把扫描也跑了：每个开发者的每次编译都会翻一倍"
    )
