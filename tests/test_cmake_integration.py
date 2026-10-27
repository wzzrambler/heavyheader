"""CMake 集成：模块能配置、扫描目标能产出、门禁能拦。

**缺 cmake 或缺可用的 C++ 编译器时 skip，不假装通过。** 这条测试的价值在于它在
有 cmake 的环境里真跑一遍；没有 cmake 的地方静默 pass 等于没测。

之所以值得测：CMake 模块是最容易「写完一次就再没人动」的东西——语法错了只有在
被 include 时才暴露，而那通常发生在别人的项目里。
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
MODULE_DIR = REPO_ROOT / "cmake"


def _cmake_command() -> list[str] | None:
    """找到能用的 cmake。

    pip 装的 cmake 不一定进 PATH，但提供 ``python -m cmake``，所以两种都试。
    """
    which = shutil.which("cmake")
    if which:
        return [which]
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "cmake", "--version"],
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if probe.returncode == 0:
        return [sys.executable, "-m", "cmake"]
    return None


def _build_setup() -> tuple[list[str], dict[str, str], list[str]] | None:
    """返回 ``(cmake 命令, 环境变量, 生成器参数)``；环境不具备则返回 None。

    生成器不能写死：Windows 上默认生成器是 Visual Studio，而本机没有 MSVC——
    那样 configure 会以「找不到编译器」失败，看起来像模块有问题。
    """
    cmake = _cmake_command()
    if cmake is None:
        return None
    env = dict(os.environ)

    if os.name == "nt":
        if shutil.which("cl"):
            # MSVC 在 PATH 里（例如 VS 开发者命令行），用默认生成器即可。
            return cmake, env, []
        gpp = shutil.which("g++")
        if gpp and shutil.which("mingw32-make"):
            env["CXX"] = gpp
            return cmake, env, ["-G", "MinGW Makefiles"]
        return None

    cxx = shutil.which("c++") or shutil.which("g++")
    if not cxx:
        return None
    env["CXX"] = cxx
    return cmake, env, []


_SETUP = _build_setup()

pytestmark = pytest.mark.skipif(
    _SETUP is None,
    reason="本机没有可用的 cmake 或 C++ 编译器（两者缺一这条测试就无从跑起）",
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
    (root / "CMakeLists.txt").write_text(
        "\n".join(
            [
                "cmake_minimum_required(VERSION 3.10)",
                "project(hh_demo CXX)",
                "set(CMAKE_EXPORT_COMPILE_COMMANDS ON)",
                "add_executable(app main.cpp)",
                "target_include_directories(app PRIVATE ${CMAKE_SOURCE_DIR}/include)",
                f'list(APPEND CMAKE_MODULE_PATH "{MODULE_DIR.as_posix()}")',
                "include(HeavyHeader)",
                "heavyheader_scan(app)",
                'heavyheader_gate(app BASELINE "${CMAKE_BINARY_DIR}/hh-baseline.json")',
            ]
        ),
        encoding="utf-8",
    )
    return root


#: 构建工具回显里可能有中文（模块的 COMMENT 就是），按平台默认编码去解码
#: 会在 Windows 的 cp1252 上炸掉——连 stdout 都变成 None，真正的失败原因
#: 反而看不见。所以显式给编码，并容忍替换。
_ENCODING = {"encoding": "utf-8", "errors": "replace"}


def _configure(project: Path, build: Path) -> None:
    cmake, env, generator = _SETUP  # type: ignore[misc]
    subprocess.run(
        [*cmake, "-S", str(project), "-B", str(build), *generator],
        check=True,
        capture_output=True,
        timeout=900,
        env=env,
        **_ENCODING,
    )


def _build(build: Path, target: str) -> subprocess.CompletedProcess:
    cmake, env, _ = _SETUP  # type: ignore[misc]
    return subprocess.run(
        [*cmake, "--build", str(build), "--target", target],
        capture_output=True,
        timeout=900,
        env=env,
        **_ENCODING,
    )


def test_module_configures_and_scan_target_produces_a_scan(tmp_path: Path):
    project = _project(tmp_path / "proj")
    build = tmp_path / "build"

    _configure(project, build)

    result = _build(build, "heavyheader-scan-app")
    assert result.returncode == 0, result.stdout + result.stderr

    scan_json = build / "heavyheader-app.json"
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
    assert _build(build, "heavyheader-scan-app").returncode == 0

    baseline = build / "hh-baseline.json"
    shutil.copyfile(build / "heavyheader-app.json", baseline)
    passed = _build(build, "heavyheader-gate-app")
    assert passed.returncode == 0, passed.stdout + passed.stderr

    # 把基线改小：门禁比的是「各头 attributed_lines 之和」，所以改的是
    # headers 而不是 totals（totals 只是展示用的汇总，改它不会改变判定）。
    # 改判定输入而不是再扫一遍——省一次全量扫描，且被测的仍是那条门禁命令。
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["headers"], "基线里没有头文件——这份基线本身就不成立"
    for header in payload["headers"]:
        header["attributed_lines"] = 1
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    blocked = _build(build, "heavyheader-gate-app")
    assert blocked.returncode != 0, "基线被改小后门禁仍通过——它拦不住任何东西"
