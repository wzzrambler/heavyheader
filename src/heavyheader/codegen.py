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
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .drivers.base import CompilerDriver, DriverOptions

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
    argv.append("-S")
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
        if prefix:
            text = text.replace(prefix, "<path>")
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

    两次必须用**同一个输出路径**（``unit.s``）：汇编里嵌的输出文件名是字节的一部分，
    换成 ``original.s`` / ``patched.s`` 会让两份永远不同。覆盖没关系——每次编完
    立刻读出来算哈希。源码目录用 ``mask`` 抹平。
    """
    asm_path = workdir / "unit.s"
    mask = (str(original.parent), str(patched.parent))
    before = fingerprint(driver, original, options, cwd, asm_path, timeout, mask)
    after = fingerprint(driver, patched, options, cwd, asm_path, timeout, mask)
    return before, after
