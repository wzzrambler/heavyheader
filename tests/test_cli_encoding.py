"""输出编码：CI 里 `gate` 曾经因为打印中文而崩溃。

真实现象（GitHub Actions，windows-latest）：`heavyheader gate` 退出码 1，
日志里是 `UnicodeEncodeError: 'charmap' codec can't encode characters`，
调用栈指向 `cli.py` 里打印「头文件解析量：…」的那一行。

根因不是门禁判定，而是**打印**：Windows 在管道下按 ANSI 代码页（runner 上是
cp1252）编码，中文无法映射就抛异常。任何人把输出接进 tee、日志或 CI 都会撞上。

修法：重定向时把 stdout/stderr 切成 UTF-8（交互式控制台保持原样，那边由
WriteConsoleW 处理 Unicode）。这两个用例锁住「重定向要切」和「终端不能动」。
"""

from __future__ import annotations

import io
import sys

from heavyheader import cli


def _redirected(encoding: str) -> io.TextIOWrapper:
    """一个「不是终端」的文本流，模拟管道/CI 重定向。"""
    return io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")


def test_redirected_stream_is_switched_to_utf8(monkeypatch):
    stream = _redirected("cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    # 修复前：这一行在 cp1252 下直接抛 UnicodeEncodeError。
    cli._make_redirected_output_utf8()
    print("头文件解析量：228,987 → 229,031", file=stream)
    stream.flush()

    written = stream.buffer.getvalue()
    assert "头文件解析量".encode("utf-8") in written


def test_interactive_stream_keeps_its_encoding(monkeypatch):
    """终端不能被改成 UTF-8：中文 Windows 控制台是 cp936，改了会显示乱码。"""
    stream = _redirected("cp1252")
    monkeypatch.setattr(stream, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    cli._make_redirected_output_utf8()

    assert stream.encoding.lower() == "cp1252"
