"""同一棵源码树、多套构建配置：判定矩阵。

**为什么这是一个命令**：`scan` 回答「这个工程能删什么」，而真实的工程不是只有
一个「这个工程」——同一份源码会用不同的 `-D` 编很多次。实测（zstd 1.5.6）：
`pool.c → allocations.h` 在单线程配置里可删（384 → 205 ms，−46.6%），在
`-DZSTD_MULTITHREAD` 里删了直接编不过。它属于哪一类，不取决于代码，取决于
**这次编译有没有打开那个宏**。

所以 `configs` 做的事很窄：拿同一份 `compile_commands.json`，给每套配置追加
不同的编译参数各扫一遍，按 `(单元, 头, 行)` 对齐，只报**判定变了的那几条**。
不需要新的分析逻辑——判定层本来就从 `-D` 求值分支，这里只是把它摆到台面上。

用法::

    heavyheader configs . --compiler clang \\
        --config "single:" --config "mt:-DZSTD_MULTITHREAD"
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ConfigSpec",
    "VerdictChange",
    "derive_database",
    "verdict_changes",
    "render_matrix",
]

#: 对齐用的键：(单元路径小写, 头文件名, 行号)。行号参与是因为同一个头可能在
#: 一个文件里出现两次（条件编译的两个分支）。
Key = tuple[str, str, "int | None"]


@dataclass(frozen=True)
class ConfigSpec:
    """一套构建配置：一个名字 + 追加到每条编译命令后面的参数。"""

    name: str
    flags: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: str) -> ConfigSpec:
        """``"mt:-DZSTD_MULTITHREAD -DNDEBUG"`` → ``ConfigSpec("mt", (...))``。

        冒号可以省略（``"plain"`` 等价于 ``"plain:"``）；名字里不允许空格，
        否则命令行上分不清哪一段是名字。
        """
        name, _, rest = raw.partition(":")
        name = name.strip()
        if not name or " " in name or "\t" in name:
            raise ValueError(f"配置名不能为空或含空格：{raw!r}")
        return cls(name=name, flags=tuple(rest.split()))


@dataclass(frozen=True)
class VerdictChange:
    """一条候选在 A、B 两套配置下的判定差异。"""

    tu: str
    header: str
    line: int | None
    before: str
    after: str | None  # None = 这套配置里它根本不是候选

    def describe(self) -> str:
        after = self.after if self.after is not None else "（不再是候选）"
        return f"{self.tu} → {self.header}：{self.before} → {after}"


def derive_database(entries: list[dict], flags: tuple[str, ...]) -> list[dict]:
    """把 ``flags`` 追加到每条编译命令尾部，返回新的条目列表。

    追加而不是插入：``command`` 是自由文本，插到中间要重新切词（引号、转义、
    Windows 反斜杠路径都会踩坑），而 gcc/clang 都接受「选项写在输入文件之后」。
    """
    if not flags:
        return [dict(entry) for entry in entries]
    suffix = " ".join(flags)
    derived = []
    for entry in entries:
        copy = dict(entry)
        copy["command"] = f"{entry['command']} {suffix}"
        derived.append(copy)
    return derived


def table_of(candidates, *, tu_name: str | None = None) -> dict[Key, dict]:
    """把一份扫描的候选压成 ``(tu, 头, 行) -> 行`` 的表。

    ``tu_name=True`` 时只保留文件名（跨机器比对时路径前缀会变）；默认保留完整
    路径的小写形式，与引擎里其它地方的口径一致。
    """
    table: dict[Key, dict] = {}
    for candidate in candidates:
        tu_path = candidate.tu if isinstance(candidate.tu, Path) else Path(candidate.tu)
        tu = tu_path.name if tu_name else str(tu_path).lower()
        key: Key = (tu, Path(candidate.header).name, candidate.line)
        table[key] = {
            "tu": tu_path.name,
            "header": Path(candidate.header).name,
            "line": candidate.line,
            "verdict": candidate.usage_verdict,
            "seconds": candidate.estimated_seconds_saved,
            "reason": candidate.usage_reason,
        }
    return table


def verdict_changes(
    before: dict[Key, dict], after: dict[Key, dict]
) -> list[VerdictChange]:
    """``before`` 为基准，列出 ``after`` 里判定不同的那几条（顺序稳定）。"""
    changes: list[VerdictChange] = []
    for key in sorted(before, key=lambda k: (k[0], k[1], k[2] or 0)):
        old = before[key]
        new = after.get(key)
        if new is None:
            changes.append(
                VerdictChange(old["tu"], old["header"], old["line"], old["verdict"], None)
            )
        elif new["verdict"] != old["verdict"]:
            changes.append(
                VerdictChange(
                    old["tu"], old["header"], old["line"], old["verdict"], new["verdict"]
                )
            )
    return changes


def render_matrix(
    specs: list[ConfigSpec],
    summaries: dict[str, dict],
    changes: dict[str, list[VerdictChange]],
) -> str:
    """Markdown：先一张配置总表，再逐个配置列出「判定变了什么」。"""
    lines = ["# 构建配置矩阵", ""]
    lines.append("同一棵源码树，按多套配置各扫一遍；只列判定发生变化的候选。")
    lines.append("")
    lines.append("| 配置 | 追加参数 | 单元 | 候选 | 疑似未使用 | 无法判定 | 在用 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for spec in specs:
        row = summaries.get(spec.name, {})
        verdicts = row.get("verdicts", {})
        flags = " ".join(spec.flags) or "—"
        lines.append(
            f"| `{spec.name}` | `{flags}` | {row.get('tu', 0)} | "
            f"{row.get('candidates', 0)} | {verdicts.get('plausibly_unused', 0)} | "
            f"{verdicts.get('unknown', 0)} | {verdicts.get('in_use', 0)} |"
        )
    lines.append("")

    base = specs[0].name if specs else ""
    if len(specs) > 1:
        lines.append(f"## 相对 `{base}` 的判定变化")
        lines.append("")
        for spec in specs[1:]:
            items = changes.get(spec.name, [])
            lines.append(f"### `{base}` → `{spec.name}`：{len(items)} 条不同")
            lines.append("")
            if not items:
                lines.append("判定完全一致。")
            else:
                lines.append("| 单元 | 头文件 | 行 | 基准 | 本配置 |")
                lines.append("|---|---|---:|---|---|")
                for change in items:
                    after = change.after if change.after is not None else "（不再是候选）"
                    lines.append(
                        f"| `{change.tu}` | `{change.header}` | "
                        f"{change.line if change.line is not None else '—'} | "
                        f"{change.before} | {after} |"
                    )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_database(entries: list[dict], path: Path) -> None:
    path.write_text(json.dumps(entries, indent=1), encoding="utf-8")


def temporary_database(
    entries: list[dict], flags: tuple[str, ...]
) -> tuple[Path, tempfile.TemporaryDirectory]:
    """派生一份临时 ``compile_commands.json``；调用方负责清理目录。"""
    holder = tempfile.TemporaryDirectory(prefix="heavyheader-config-")
    path = Path(holder.name) / "compile_commands.json"
    write_database(derive_database(entries, flags), path)
    return path, holder
