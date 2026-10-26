"""相对 ``-I`` + 相对扫描根：头文件路径必须仍然落地。

这条守的是一个**静默失效**：``heavyheader scan ./proj -I ./proj/include`` 是
README 里主推的用法，但编译器在 ``-H`` 里给的是相对**进程 cwd** 的路径，而
``_normalise_header_key`` 按 **scan root** 把它接回去。当 root 不是 cwd 时，
每个项目头都会变成 ``./proj/proj/include/x.h`` 这种不存在的路径：

* 行数读成 0
* 项目头被判成外部头（不在 root 下）
* PCH 估算一个候选都选不出来，却报出「覆盖率 100%、净收益 0」

没有任何一处抛异常——报告看起来完全正常，只是空的。

修法是从源头消掉：``scan_project`` 把显式 ``-I`` 一律解析成绝对路径，编译器
就只会回绝对路径，拼接那一步根本不会发生。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from heavyheader.attrib.pch import estimate_pch
from heavyheader.drivers.base import detect_driver
from heavyheader.models import HeaderKind
from heavyheader.scanner import ScanOptions, scan_project


def _corpus(root: Path) -> None:
    (root / "include").mkdir(parents=True)
    (root / "include" / "heavy.hpp").write_text(
        "#pragma once\n"
        + "".join(f"inline int heavy_{i}(int x) {{ return x + {i}; }}\n" for i in range(300)),
        encoding="utf-8",
    )
    for index in range(3):
        (root / f"u{index}.cpp").write_text(
            '#include "heavy.hpp"\n'
            f"int u{index}() {{ return heavy_{index}(1); }}\n",
            encoding="utf-8",
        )


def test_relative_root_and_relative_include_still_find_project_headers(tmp_path, monkeypatch):
    """root 与 cwd 不同、``-I`` 又是相对路径时，项目头仍须被认出来。"""
    if shutil.which("g++") is None:
        pytest.skip("需要 g++")

    work = tmp_path / "work"
    work.mkdir()
    _corpus(work / "proj")
    # cwd 设在 proj 的**父目录**：这样 "proj" 是相对路径，且不等于 cwd。
    monkeypatch.chdir(work)
    relative_root = Path("proj")
    relative_include = relative_root / "include"

    scan = scan_project(
        ScanOptions(
            root=relative_root,
            compiler="g++",
            standard="c++17",
            include_dirs=[relative_include],
            jobs=1,
        ),
        driver=detect_driver("g++"),
    )

    heavy = next(
        stats for stats in scan.header_stats.values() if stats.path.name == "heavy.hpp"
    )
    # 路径必须是真实存在的那一个，不能被 root 二次拼接。
    assert heavy.path.is_file(), heavy.path
    assert heavy.path.parent.name == "include"
    assert heavy.path.parent.parent.name == "proj"
    assert heavy.kind is HeaderKind.PROJECT
    assert heavy.line_count > 0

    rec = estimate_pch(scan)
    assert rec.headers, "相对路径下 PCH 估算不能选不出候选"
    assert rec.net_lines_saved > 0


def test_absolute_and_relative_roots_agree(tmp_path, monkeypatch):
    """同一棵树，相对根与绝对根必须得出同一份 header_stats。"""
    if shutil.which("g++") is None:
        pytest.skip("需要 g++")

    work = tmp_path / "work"
    work.mkdir()
    _corpus(work / "proj")
    monkeypatch.chdir(work)

    def snapshot(root: Path) -> dict[str, tuple[int, str]]:
        scan = scan_project(
            ScanOptions(
                root=root,
                compiler="g++",
                standard="c++17",
                include_dirs=[root / "include"],
                jobs=1,
            ),
            driver=detect_driver("g++"),
        )
        return {
            stats.path.name: (stats.line_count, stats.kind.value)
            for stats in scan.header_stats.values()
            if stats.path.name == "heavy.hpp"
        }

    assert snapshot(Path("proj")) == snapshot((work / "proj").resolve())
