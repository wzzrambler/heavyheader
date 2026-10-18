"""Tests for ``blame`` -- commit-level build-cost attribution.

The end-to-end test builds a real throwaway git repository (no network, no
compilers): two commits, a new include edge in the second, and two synthetic
baselines whose deterministic metric moves exactly the way the graph says it
should.  The point of that fixture is that the *expected* numbers are derived
by hand from the closure arithmetic, so a regression in the attribution logic
fails the test instead of silently re-baselining.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from heavyheader.blame import (
    BlameError,
    blame_scans,
    parse_include_diff,
    resolve_target,
)
from heavyheader.models import CompilerFamily, HeaderStats, ProjectScan, TranslationUnit


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout


def _commit_all(repo: Path, message: str) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").strip()


# ---------------------------------------------------------------------------
# pure units
# ---------------------------------------------------------------------------


def test_parse_include_diff_extracts_both_directions():
    diff = (
        "diff --git a/core.h b/core.h\n"
        "--- a/core.h\n"
        "+++ b/core.h\n"
        "@@ -1,2 +1,3 @@\n"
        ' #include <vector>\n'
        '-#include "old.h"\n'
        '+#include "big.h"\n'
        " int x;\n"
    )
    change = parse_include_diff(diff, Path("core.h"))
    assert change.added == ['"big.h"']
    assert change.removed == ['"old.h"']


def test_parse_include_diff_ignores_non_include_lines():
    diff = (
        "--- a/a.cpp\n+++ b/a.cpp\n"
        "@@ -1,2 +1,2 @@\n"
        "+int new_symbol = 1;\n"
        "-int old_symbol = 2;\n"
        '+// #include "commented.h"\n'
    )
    change = parse_include_diff(diff, Path("a.cpp"))
    assert change.added == [] and change.removed == []


def test_resolve_target_matches_by_suffix_and_name(tmp_path):
    scan = _scan(root=tmp_path, tus=[], headers={"proj/include/big.h": (100, 1)})
    assert resolve_target('"big.h"', scan) == tmp_path / "proj/include/big.h"
    assert resolve_target('"include/big.h"', scan) == tmp_path / "proj/include/big.h"
    assert resolve_target("<vector>", scan) is None


# ---------------------------------------------------------------------------
# helpers for synthetic scans
# ---------------------------------------------------------------------------


def _scan(root: Path, tus: list[tuple[Path, list[str]]], headers: dict[str, tuple[int, int]]):
    """Build a ProjectScan: ``headers`` maps name -> (line_count, tu_reach)."""
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard=None,
    )
    for source, deps in tus:
        scan.tus.append(
            TranslationUnit(source=source, dependencies=[root / d for d in deps])
        )
    for name, (line_count, tu_reach) in headers.items():
        path = root / name
        scan.header_stats[path] = HeaderStats(
            path=path,
            line_count=line_count,
            tu_reach=tu_reach,
            tu_direct=tu_reach,
            inclusion_events=tu_reach,
            attributed_lines=line_count * tu_reach,
        )
    return scan


# ---------------------------------------------------------------------------
# end-to-end with a real git repository
# ---------------------------------------------------------------------------


def test_blame_names_the_commit_that_added_the_edge(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "include").mkdir()
    (repo / "src" / "main.cpp").write_text('int main(){return 0;}\n', encoding="utf-8")
    (repo / "src" / "util.cpp").write_text("int util(){return 0;}\n", encoding="utf-8")
    (repo / "include" / "core.h").write_text("#pragma once\nint core();\n", encoding="utf-8")
    (repo / "include" / "big.h").write_text(
        "#pragma once\n" + "int big();\n" * 100, encoding="utf-8"
    )
    c1 = _commit_all(repo, "c1: baseline")

    # 回归：core.h 拉进 big.h。util.cpp 从此也开始为 big.h 付费。
    (repo / "include" / "core.h").write_text(
        '#pragma once\n#include "big.h"\nint core();\n', encoding="utf-8"
    )
    c2 = _commit_all(repo, "c2: core.h pulls big.h")

    big = repo / "include" / "big.h"
    main_cpp = repo / "src" / "main.cpp"
    util_cpp = repo / "src" / "util.cpp"

    before = _scan(
        root=repo,
        tus=[
            (main_cpp, ["include/core.h", "include/big.h"]),
            (util_cpp, ["include/core.h"]),
        ],
        headers={"include/core.h": (2, 2), "include/big.h": (100, 1)},
    )
    after = _scan(
        root=repo,
        tus=[
            (main_cpp, ["include/core.h", "include/big.h"]),
            (util_cpp, ["include/core.h", "include/big.h"]),
        ],
        headers={"include/core.h": (3, 2), "include/big.h": (100, 2)},
    )
    before.git_commit = c1
    after.git_commit = c2

    report = blame_scans(before, after, repo)

    assert report.net_delta == 102  # big.h +100（util.cpp 新增可达），core.h 自身 +2
    assert len(report.entries) == 1
    entry = report.entries[0]
    assert entry.commit.sha == c2
    assert entry.file == Path("include/core.h")
    assert entry.change == '+#include "big.h"'
    assert entry.target_path == big
    # 预测：big.h 100 行 ×（新到达它的 TU 数 1）= +100；实测同为 +100。
    assert entry.predicted_delta == 100
    assert entry.observed_delta == 100
    assert entry.confidence == "high"
    # core.h 自己从 2 行变 3 行带来的 +2 不是任何 include 改动的结果——必须
    # 留在「未解释」里，而不是被硬分派给某个 commit。
    assert report.unexplained == 2
    assert report.explained_delta == 100


def test_blame_reports_nothing_when_cost_did_not_move(tmp_path):
    repo = tmp_path / "repo"
    (repo / "include").mkdir(parents=True)
    (repo / "include" / "core.h").write_text("#pragma once\n", encoding="utf-8")
    c1 = _commit_all(repo, "c1")
    (repo / "include" / "core.h").write_text("#pragma once\n// 只加注释\n", encoding="utf-8")
    c2 = _commit_all(repo, "c2: comment only")

    before = _scan(root=repo, tus=[], headers={"include/core.h": (2, 1)})
    after = _scan(root=repo, tus=[], headers={"include/core.h": (2, 1)})
    before.git_commit = c1
    after.git_commit = c2

    report = blame_scans(before, after, repo)
    assert report.net_delta == 0
    assert report.entries == []
    assert report.unexplained == 0


def test_blame_without_revisions_is_a_clear_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "keep.h").write_text("#pragma once\n", encoding="utf-8")
    _commit_all(repo, "c1")
    before = _scan(root=repo, tus=[], headers={})
    after = _scan(root=repo, tus=[], headers={})
    with pytest.raises(BlameError, match="revision range"):
        blame_scans(before, after, repo)


def test_blame_outside_a_repository_is_a_clear_error(tmp_path):
    before = _scan(root=tmp_path, tus=[], headers={})
    after = _scan(root=tmp_path, tus=[], headers={})
    with pytest.raises(BlameError, match="not a git repository"):
        blame_scans(before, after, tmp_path / "nowhere")


def test_blame_unresolvable_target_is_low_confidence(tmp_path):
    """加了一条扫描没跟到的 include：不猜，标 low 并说明原因。

    其他头文件的成本确实变了（b.h 新增），所以归因流程照常运行；ghost.h
    既不在任何基线的头文件表里，它的条目就必须以 low 置信度出现，而不是
    被静默丢掉或者被硬塞一个数字。
    """
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    c1 = _commit_all(repo, "c1")
    (repo / "src" / "main.cpp").write_text(
        '#include "ghost.h"\nint main(){return 0;}\n', encoding="utf-8"
    )
    c2 = _commit_all(repo, "c2: add untracked include")

    before = _scan(
        root=repo,
        tus=[(repo / "src" / "main.cpp", ["include/a.h"])],
        headers={"include/a.h": (10, 1)},
    )
    after = _scan(
        root=repo,
        tus=[(repo / "src" / "main.cpp", ["include/a.h", "include/b.h"])],
        headers={"include/a.h": (10, 1), "include/b.h": (5, 1)},
    )
    before.git_commit = c1
    after.git_commit = c2

    report = blame_scans(before, after, repo)
    assert report.net_delta == 5
    entries = {e.target: e for e in report.entries}
    assert '"ghost.h"' in entries
    entry = entries['"ghost.h"']
    assert entry.target_path is None
    assert entry.confidence == "low"
    assert "不在扫描" in entry.note
    # 没有 include 改动能解释 b.h 的 +5——留在未解释里。
    assert report.unexplained == 5
