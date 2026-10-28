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
    cn_numerals = {
        "十": 10,
        "十一": 11,
        "十二": 12,
        "十三": 13,
        "十四": 14,
        "十五": 15,
        "十六": 16,
        "十七": 17,
        "十八": 18,
        "十九": 19,
        "二十": 20,
    }
    for word, value in cn_numerals.items():
        if f"{word}个命令" in text:
            claimed.add(value)
    assert claimed <= {count}, (
        f"README 声称有 {sorted(claimed)} 个命令，实际 {count} 个。"
        "命令数会随迭代变化，写死数字就一定会过时——要么别写，要么跟着改。"
    )


#: 三份交付 PDF 的生成脚本。它们不在 README 那张表里，却同样写死了命令总数，
#: 而且**更难发现**——PDF 是二进制，过时的数字要解出文本才看得见。
_PDF_SCRIPTS = (
    _ROOT / "docs" / "make_pdf.py",
    _ROOT / "docs" / "make_pdf_review.py",
    _ROOT / "docs" / "make_topic_report.py",
)

_CN_SMALL = {
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}


def _claimed_counts(text: str) -> set[int]:
    """抽出文中所有「N 个命令」/「十N个命令」形式的总数断言。"""
    claimed = {int(m.group(1)) for m in re.finditer(r"(\d+)\s*个命令", text)}
    for word, value in _CN_SMALL.items():
        if f"{word}个命令" in text:
            claimed.add(value)
    return claimed


def test_pdf_scripts_do_not_claim_a_stale_command_count():
    """三份 PDF 生成脚本若写了命令总数，必须与 CLI 一致。

    这条守卫是 2026-10-28 补的：新增 `iwyu` / `include-cleaner` 两个子命令后，
    三个脚本里都还写着旧的数字，而 PDF 已经按旧数字渲染出去了。人工核对靠的是
    「记得去看」——那不是机制。
    """
    count = len(_subcommands())
    stale = {}
    for script in _PDF_SCRIPTS:
        claimed = _claimed_counts(script.read_text(encoding="utf-8"))
        if claimed and claimed != {count}:
            stale[script.name] = sorted(claimed)
    assert not stale, (
        f"这些 PDF 脚本声称的命令数与实际（{count}）不符：{stale}。"
        "改完 CLI 记得重建三份 PDF，否则纸面数字会继续旧下去。"
    )


def test_pdf_scripts_agree_on_the_test_count():
    """三份 PDF 脚本若都写了单元测试数，它们必须彼此一致。

    **这条守卫能防的和不能防的要分清。** 它防的是「改了一处、漏了另两处」——
    今天正是这么错的：数字从 759 涨到 792，三个脚本各写一份，改哪个全凭记性。
    它**防不了**「三处一起旧」：真实测试数只能在执行时得到，静态读不出来，
    而在这条测试里再跑一遍全量测试等于让测试套件自我递归。

    所以这里比的是「三份脚本互相一致」，而不是「与真实值一致」。真实值的守卫
    只能靠重建 PDF 时人工核一次（`pytest --no-header` 的汇总行）。
    """
    claimed = {}
    for script in _PDF_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        found = {
            int(m.group(1))
            # 「63 个测试二进制」「63 个测试套件」讲的是 json 工程的验收方式，
            # 不是测试总数——负向前瞻把它们排除掉，否则这条守卫会误报。
            for m in re.finditer(r"(\d+)\s*个(?:单元)?测试(?![二套])", text)
        }
        if found:
            claimed[script.name] = sorted(found)
    values = {tuple(v) for v in claimed.values()}
    assert len(values) <= 1, (
        f"三份 PDF 脚本写的测试数不一致：{claimed}。改一处就要改三处，"
        "否则三份纸面材料会互相打脸。"
    )
