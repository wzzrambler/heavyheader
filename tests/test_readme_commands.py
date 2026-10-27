"""README 的命令表必须覆盖每一个 CLI 子命令（且不多写一个）。

为什么值得一条测试：`pch-verify` 上线后**数周**没进这张表，`history` 与
`pr-comment` 一直漏到 2026-10-27 才补上。人工核对抓不住这类漂移——表是在
`docs/` 改的，命令是在 `cli.py` 加的，两边从不互相报错。

这是给「文档落后于代码」这类问题留的一个早期警报，不是格式检查。
"""

import re
from pathlib import Path

from heavyheader.cli import build_parser

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"

#: 表格行形如 ``| `scan` | 说明 |``。只认行首第一个反引号里的名字，
#: 说明文字里提到的 `` `verify` `` 不该被当成一条命令。
#: ``MULTILINE`` 不能省：少了它 ``^`` 只匹配全文开头，`_documented()` 恒为空集，
#: 这条测试就变成「永远通过」，比没有更糟。
_ROW_RE = re.compile(r"^\|\s*`([a-z][a-z0-9-]*)`\s*\|", re.MULTILINE)


def _subcommands() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if action.dest == "command" and action.choices:
            return set(action.choices)
    raise AssertionError("CLI 解析器里找不到子命令分发入口")


def _documented() -> set[str]:
    return set(_ROW_RE.findall(_README.read_text(encoding="utf-8")))


def test_every_subcommand_is_documented():
    missing = sorted(_subcommands() - _documented())
    assert not missing, (
        f"README 命令表缺少 {missing}。新增子命令时同步更新表格；"
        "这张表是读者对工具能力的第一印象，漏一条等于那条能力不存在。"
    )


def test_documented_commands_all_exist():
    """反向也查：表里写着一个已被删除或改名的命令，同样是不可接受的。"""
    stale = sorted(_documented() - _subcommands())
    assert not stale, f"README 命令表里有不存在的子命令 {stale}"


def test_readme_mentions_the_command_count():
    """正文里若写了命令总数，它必须等于真实数量。

    只查「个命令」这种明确的总数表述；逐一列举命令名的段落不算。
    """
    text = _README.read_text(encoding="utf-8")
    count = len(_subcommands())
    claimed = {
        int(m.group(1))
        for m in re.finditer(r"(\d+)\s*个命令", text)
    }
    cn_numerals = {"十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14}
    for word, value in cn_numerals.items():
        if f"{word}个命令" in text:
            claimed.add(value)
    assert claimed <= {count}, (
        f"README 声称有 {sorted(claimed)} 个命令，实际 {count} 个。"
        "命令数会随迭代变化，写死数字就一定会过时——要么别写，要么跟着改。"
    )
