"""Tests that the project's own links are well-formed.

这两类故障都真实发生过，且都无法靠单元级测试抓住：

1. 报告页脚写着 ``https://github.com/``（空仓库地址），用户点开只能看到
   GitHub 首页——因为它散落在渲染器字符串里，没有常量可断言；
2. ``docs/finalize.py`` 把裸占位串 ``heavyheader/heavyheader`` 整体替换成
   完整 URL，于是 ``https://github.com/heavyheader/heavyheader`` 变成了
   ``https://github.com/https://github.com/wzzrambler/heavyheader``，
   污染了 README 徽章、CHANGELOG 链接和 pyproject 的包元数据。

所以这里做两件事：断言常量本身，再全量扫描仓库里会被用户看到的文本。
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from heavyheader import PROJECT_URL

ROOT = Path(__file__).resolve().parent.parent

#: 会被用户/打包工具读到的文本。刻意不含生成物（benchmarks/results/*），
#: 那些是某次运行的历史快照，不属于源码保证范围。
SCANNED_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".cfg", ".txt"}
SCANNED_DIRS = ("src", ".")
#: ``_work`` 是竞赛实验拉取的第三方源码树（nlohmann/json 的 tarball 展开），
#: 里面的 markdown 不属于本项目，它们的链接好坏与本仓库无关——把它扫进来只会
#: 让测试因为别人的文档失败。
SKIP_DIR_NAMES = {".git", "__pycache__", "video_build_0", ".pytest_cache", "_work"}

#: 这两个文件**刻意**携带畸形链接：finalize.py 要用它当修复目标，
#: 本文件要用它当断言对象。把它们排除，否则扫描会自己告自己。
INTENTIONAL_PATTERN_FILES = {"finalize.py", "test_repo_links.py"}

#: 精确匹配「真的会出现在用户面前」的畸形链接，避免误伤解释性注释。
_BAD_DOUBLE = "github.com/https://"
_BAD_MD_LINK = re.compile(r"\]\(https://github\.com/\)")
_BAD_TOML_VALUE = re.compile(r'=\s*"https://github\.com/"')


def _iter_text_files() -> list[Path]:
    seen: set[Path] = set()
    found: list[Path] = []
    for rel in SCANNED_DIRS:
        base = (ROOT / rel).resolve()
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.name in INTENTIONAL_PATTERN_FILES:
                continue
            resolved = path.resolve()
            if resolved in seen:  # "src" 与 "." 会重复命中同一批文件
                continue
            seen.add(resolved)
            found.append(path)
    return sorted(found)


def test_project_url_is_a_real_repository_url():
    assert re.match(r"^https://github\.com/[^/]+/[^/]+$", PROJECT_URL), (
        f"PROJECT_URL 不是合法的仓库地址：{PROJECT_URL!r}"
    )


@pytest.mark.parametrize("path", _iter_text_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_malformed_github_links(path: Path):
    rel = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8", errors="replace")

    assert _BAD_DOUBLE not in text, (
        f"{rel} 含重复 scheme 的链接——朴素字符串替换的典型产物"
    )
    assert not _BAD_MD_LINK.search(text), (
        f"{rel} 含空仓库地址的 Markdown 链接——用户点开只会到 GitHub 首页"
    )
    assert not _BAD_TOML_VALUE.search(text), (
        f"{rel} 含空仓库地址的 TOML 值——会原样进到 PyPI 的包元数据里"
    )


# --------------------------------------------------------------------------
# finalize.py 的替换逻辑（脚本不在包内，按文件路径加载）
# --------------------------------------------------------------------------

_FINALIZE = ROOT / "docs" / "finalize.py"


@pytest.fixture(scope="module")
def finalize():
    spec = importlib.util.spec_from_file_location("hh_finalize", _FINALIZE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_substitution_replaces_full_and_bare_placeholders(finalize):
    repo = "https://github.com/wzzrambler/heavyheader"
    text = (
        "Homepage = \"https://github.com/heavyheader/heavyheader\"\n"
        "badge: heavyheader/heavyheader/actions/workflows/ci.yml\n"
    )
    out = finalize.substitute_repo_url(text, repo)

    assert out.count(repo) == 2
    assert "heavyheader/heavyheader" not in out


def test_substitution_does_not_double_the_scheme(finalize):
    """The historical bug: the prefix was already there, so it got doubled."""
    repo = "https://github.com/wzzrambler/heavyheader"
    text = "https://github.com/heavyheader/heavyheader/issues"

    assert finalize.substitute_repo_url(text, repo) == repo + "/issues"


def test_substitution_is_idempotent(finalize):
    repo = "https://github.com/wzzrambler/heavyheader"
    text = "https://github.com/heavyheader/heavyheader/tree/main/docs"

    once = finalize.substitute_repo_url(text, repo)
    twice = finalize.substitute_repo_url(once, repo)

    assert once == twice == repo + "/tree/main/docs"


def test_repair_double_scheme_reports_and_fixes(finalize):
    broken = "https://github.com/https://github.com/wzzrambler/heavyheader/issues"
    fixed, count = finalize.repair_double_scheme(broken)

    assert count == 1
    assert fixed == "https://github.com/wzzrambler/heavyheader/issues"
