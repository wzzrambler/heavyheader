"""SemanticBackend: optional AST cross-check via clang-include-cleaner.

The ctypes-in-process route (ROADMAP v0.3 route 2) was proven non-viable on
this machine (see experiments/libclang_spike.py -- every TU-creation entry
point returns CXError_ASTReadError with no diagnostics).  This route drives the
*same* LLVM tool as an external process, which competitor.py already does
successfully on 63/64 units.  These tests pin the subprocess contract so a
regression is loud, not silent.
"""

from pathlib import Path

from heavyheader.attrib.semantic import (
    SemanticBackend,
    SemanticVerdict,
    _semantic_agrees,
    default_mingw_args,
)


def test_detect_returns_none_when_tool_absent(monkeypatch):
    # 本机可能装了 clang-include-cleaner；清空默认探测路径 + 清空环境变量，
    # 才能真实模拟「任何地方都找不到工具」的情况。
    monkeypatch.setenv("CLANG_INCLUDE_CLEANER", "")
    monkeypatch.setattr(
        "heavyheader.attrib.semantic._DEFAULT_PATHS", ["C:/no/default/clang-include-cleaner.exe"]
    )
    backend = SemanticBackend(tool_path="C:/no/such/clang-include-cleaner.exe")
    assert backend.available is False
    assert backend.tool is None


def test_detect_finds_explicit_existing_path(tmp_path):
    fake = tmp_path / "clang-include-cleaner.exe"
    fake.write_bytes(b"MZ")  # 任意内容；探测只看 exists()
    backend = SemanticBackend(tool_path=fake)
    assert backend.available is True
    assert backend.tool == fake


def _fake_tool(tmp_path) -> Path:
    """A real on-disk dummy executable.

    Must be an *existing* path: passing a Windows-style literal made these
    tests depend on whatever LLVM install the runner happens to have
    (Windows CI has C:/Program Files/LLVM, ubuntu has one of the default
    paths) -- on macOS none exists, ``_detect`` returns None and the
    ``analyse`` guard fires.  The file never executes: subprocess.run is
    monkeypatched below.
    """
    tool = tmp_path / "clang-include-cleaner"
    tool.write_bytes(b"#!/bin/sh\nexit 0\n")
    return tool


def test_analyse_parses_removals(monkeypatch, tmp_path):
    backend = SemanticBackend(tool_path=_fake_tool(tmp_path))

    class _R:
        returncode = 0
        stdout = '- "windows.h"\n+ "missing.h"\n'
        stderr = ""

    monkeypatch.setattr(
        "heavyheader.attrib.semantic.subprocess.run", lambda *a, **k: _R()
    )
    verdict = backend.analyse(Path("unit.cpp"), Path("/build"))
    assert verdict.ok is True
    assert "windows.h" in verdict.removals
    # 插入建议（- "..."）不是删除建议，绝不混入 removals。
    assert "missing.h" not in verdict.removals


def test_analyse_subprocess_error_is_reported_not_raised(monkeypatch, tmp_path):
    backend = SemanticBackend(tool_path=_fake_tool(tmp_path))
    monkeypatch.setattr(
        "heavyheader.attrib.semantic.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )
    verdict = backend.analyse(Path("unit.cpp"), Path("/build"))
    assert verdict.ok is False
    assert "boom" in verdict.note


def test_analyse_nonzero_without_removals_is_failure(monkeypatch, tmp_path):
    backend = SemanticBackend(tool_path=_fake_tool(tmp_path))

    class _R:
        returncode = 1
        stdout = ""
        stderr = "cannot analyse this unit"

    monkeypatch.setattr(
        "heavyheader.attrib.semantic.subprocess.run", lambda *a, **k: _R()
    )
    verdict = backend.analyse(Path("unit.cpp"), Path("/build"))
    assert verdict.ok is False
    assert "cannot analyse" in verdict.note


def test_agrees_contract():
    ok = SemanticVerdict("u", frozenset({"a.h"}), True)
    assert _semantic_agrees(ok, Path("/x/a.h")) is True
    assert _semantic_agrees(ok, Path("/x/b.h")) is False  # 沉默 ≠ 在用
    assert _semantic_agrees(None, Path("/x/a.h")) is None  # 工具不可用
    assert (
        _semantic_agrees(SemanticVerdict("u", frozenset(), False), Path("/x/a.h"))
        is None
    )  # 该单元分析失败


def test_default_mingw_args_reference_mingw():
    args = default_mingw_args()
    assert any("--target=x86_64-w64-windows-gnu" in a for a in args)
    assert any("mingw64" in a.lower() for a in args)
