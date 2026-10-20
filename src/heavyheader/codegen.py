"""代码等价校验：删掉一条 include 之后，编出来的还是不是同一个程序。

为什么需要它（2026-10-19 实测）：伞头拆分实验里，删掉 ``<iomanip>`` 后编译**通过**，
但目标文件从 21,289 字节缩到 10,183 字节——重载解析变了，编的根本不是同一个程序。
只判「能编过」的工具会把这条当成可删建议发出去，使用者不会察觉。

判据用**汇编文本的哈希**而不是目标文件：

* 同一份输入连编两次，``.o`` 的哈希就不一样（大小相同、内容有别），
  用 ``-frandom-seed`` 也压不住——拿 ``.o`` 比哈希会把所有改动都判成「变了」；
* 汇编是确定性的；但 clang 会把**输出文件名**写进 ``# Object name``，
  所以输出路径必须固定（本模块由调用方给一个稳定的路径）。

两个前提条件都是踩出来的，不是风格偏好：``-c`` 必须剥掉（它与 ``-S`` 冲突，
留着会静默产出 ``.o``），输出路径必须固定。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .drivers.base import CompilerDriver, DriverOptions
from .models import CompilerFamily

#: 与 ``-S`` 冲突或会让它失效的参数：``-c`` 让编译器停在汇编之前，
#: ``-fsyntax-only`` 连代码生成都不做。两者都必须去掉才能比代码。
_CONFLICTING = ("-c", "-S", "-fsyntax-only")

#: 预处理行标记（``# 1 "a.h" 1 3``）与调试位置指令。它们记录的是**读过的文件**，
#: 不是生成出来的代码：删掉一条未使用的 include，代码一字不变，但这些行会少一批。
#: 不归一化掉，闸门就会把「安全的删除」全判成「代码变了」——实测踩过。
_NOISE_LINE = re.compile(r'^\s*(?:#\s*\d+\s+"|\.file\b|\.loc\b)')

#: 编译器生成的局部标号（``.LFB5414`` / ``.L12``）。编号是**内部计数器**，删掉一条
#: 未使用的 include 就会整体漂移，而指令一条没变。按出现顺序重编号即可对齐——
#: 等价代码得到等价编号，真改了代码则指令本身不同，仍然会被抓到。
#: 注意没有前置 \\b：``.`` 是非单词字符，\b 在行首/空白后不会匹配。
_LOCAL_LABEL = re.compile(r"(\.L[A-Za-z]*)(\d+)")


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """一次「编到汇编」的结果。``digest`` 为 None 表示这次没编出来。"""

    ok: bool
    digest: str | None = None
    note: str = ""

    def same_as(self, other: Fingerprint) -> bool:
        return self.ok and other.ok and self.digest == other.digest


def supported(driver: CompilerDriver) -> bool:
    """这台编译器能不能做汇编指纹比对。

    ``-S`` / ``-g0`` 是 GCC 与 Clang 的参数；MSVC 的对应写法不同（``/FA``），
    没有实现。不判能力就直接开闸门，MSVC 上会「编译失败 → 判为代码变了」，
    把每个候选都误报成危险改动——这比不开闸门更糟。
    """
    return driver.family in (CompilerFamily.GCC, CompilerFamily.CLANG)


def assembly_argv(
    driver: CompilerDriver, source: Path, options: DriverOptions, asm_path: Path
) -> list[str]:
    """编到汇编的命令行：剥掉与 ``-S`` 冲突的参数，再补 ``-S``。

    单独抽出来是为了可测——``-c`` 混进来时 ``-S`` 会被静默忽略，产出的仍是
    ``.o``，哈希从此不稳定（实验里整条判据因此失效过一次）。
    """
    argv = [
        token
        for token in driver.build_command(
            source, dataclasses.replace(options, output_path=asm_path)
        )
        if token not in _CONFLICTING
    ]
    # ``-g0`` 压掉调试信息：它记录的是**读过哪些文件、行号映射到哪**，
    # 删掉一条 include 就会变，但代码一字未动。实测（doctest，带 -g 的 Debug 构建）
    # 不压掉会让闸门把全部候选判成「代码变了」——差异全在 ``.cv_file`` /
    # ``.cv_loc`` / ``.debug$S`` 的路径字符串里。放在最后，覆盖命令行里已有的 -g。
    argv.extend(["-g0", "-S"])
    return argv


def canonicalise(assembly: str) -> str:
    """把局部标号按首次出现顺序重编号，抹掉编译器内部计数器的漂移。"""
    mapping: dict[str, str] = {}

    def _renumber(match: re.Match[str]) -> str:
        token = match.group(0)
        if token not in mapping:
            mapping[token] = f"{match.group(1)}{len(mapping)}"
        return mapping[token]

    return _LOCAL_LABEL.sub(_renumber, assembly)


def fingerprint(
    driver: CompilerDriver,
    source: Path,
    options: DriverOptions,
    cwd: Path | None,
    asm_path: Path,
    timeout: float = 300.0,
    mask: tuple[str, ...] = (),
) -> Fingerprint:
    """编译到 ``asm_path`` 并返回它的（掩码后）哈希。

    两个约束，都是踩出来的：

    * ``asm_path`` 必须**稳定**——汇编里嵌了 ``# Object name``，换路径就变字节；
    * 源码路径也要**掩掉**——verify 把补丁写到临时目录，两份源码目录不同，
      汇编里的 ``# 1 "…/main.cpp"`` 行标记就会不同。``mask`` 给出要替换成
      ``<path>`` 的目录前缀，掩完再哈希。
    """
    asm_path.parent.mkdir(parents=True, exist_ok=True)
    if asm_path.exists():
        asm_path.unlink()
    argv = assembly_argv(driver, source, options, asm_path)
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Fingerprint(ok=False, note=f"启动编译器失败：{exc}")

    if completed.returncode != 0 or not asm_path.exists():
        tail = (completed.stderr or "").strip().splitlines()[-2:]
        return Fingerprint(ok=False, note="编译到汇编失败：" + " / ".join(tail))

    text = asm_path.read_text(encoding="utf-8", errors="replace")
    kept = [line for line in text.splitlines() if not _NOISE_LINE.match(line)]
    text = canonicalise("\n".join(kept))
    for prefix in mask:
        if not prefix:
            continue
        # 路径在汇编里可能是**双反斜杠**（Windows 目标会把 `\` 转义成 `\\`），
        # 只替换一种形式等于没掩——实测因此漏掉，误报了一整轮。
        forms = {prefix, prefix.replace(os.sep, os.sep * 2), prefix.replace(os.sep, "/")}
        for form in forms:
            text = text.replace(form, "<path>")
    return Fingerprint(ok=True, digest=hashlib.sha256(text.encode("utf-8")).hexdigest())


def compare(
    driver: CompilerDriver,
    original: Path,
    patched: Path,
    options: DriverOptions,
    cwd: Path | None,
    workdir: Path,
    timeout: float = 300.0,
) -> tuple[Fingerprint, Fingerprint]:
    """分别给原始与打补丁后的源码取指纹。

    三条约束，全部是踩出来的：

    * **同一个输出路径**（``unit.s``）——汇编里嵌着输出文件名，换名字就变字节；
    * **同一个源码路径**——两份源码都先落到 ``workdir/<原文件名>`` 再编。源码里出现
      ``__FILE__`` 时（doctest 的错误信息就带着它），两份的路径不同会让字符串常量、
      进而连它的符号名都不同，闸门于是把安全删除判成「代码变了」。实测因此误报过一次。
    * ``-g0``（见 :func:`assembly_argv`）——调试信息里的行表与文件名不是代码。
    """
    asm_path = workdir / "unit.s"
    staged = workdir / original.name
    workdir.mkdir(parents=True, exist_ok=True)
    # 源码必须落到 workdir（两份同名、同路径，否则 ``__FILE__`` 会污染汇编），
    # 但**同目录相对包含**（``#include "foo.h"``）在 workdir 里就找不到了——
    # 原目录必须补进搜索路径。不补的后果不是「闸门更严」，而是「闸门拿不到
    # 指纹」，而拿不到指纹会被下游当成「代码变了」（2026-10-20 实测：doctest
    # 的 foo.h 候选整批因此误报成危险改动）。
    options = dataclasses.replace(
        options, include_dirs=[*options.include_dirs, original.parent]
    )
    before_text = original.read_text(encoding="utf-8", errors="replace")
    after_text = patched.read_text(encoding="utf-8", errors="replace")

    staged.write_text(before_text, encoding="utf-8")
    before = fingerprint(driver, staged, options, cwd, asm_path, timeout)
    staged.write_text(after_text, encoding="utf-8")
    after = fingerprint(driver, staged, options, cwd, asm_path, timeout)
    return before, after
