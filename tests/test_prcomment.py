"""Tests for the PR comment renderer and poster."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from heavyheader.baseline import Comparison, GateResult, HeaderDelta
from heavyheader.prcomment import (
    MARKER,
    PRCommentError,
    find_existing_comment,
    post_comment,
    render_pr_comment,
    resolve_pr_number,
    run_pr_comment,
)

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def comparison() -> Comparison:
    return Comparison(
        total_seconds_before=42.30,
        total_seconds_after=44.10,
        attributed_lines_before=120000,
        attributed_lines_after=135000,
        tu_count_before=8,
        tu_count_after=8,
        header_deltas=[
            HeaderDelta(Path("src/foo.h"), before=1000, after=1500),
            HeaderDelta(Path("src/bar.h"), before=2000, after=1800),
        ],
        candidate_savings=3456,
    )


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def test_marker_is_prepended(comparison: Comparison) -> None:
    gate = GateResult(passed=True, comparison=comparison)
    assert render_pr_comment(comparison, gate, candidate_count=2).startswith(MARKER)


def test_passing_verdict_has_no_reasons_block(comparison: Comparison) -> None:
    gate = GateResult(passed=True, comparison=comparison)
    body = render_pr_comment(comparison, gate, candidate_count=2)
    assert "结论：通过" in body
    assert "门禁理由" not in body


def test_failing_verdict_shows_reasons_and_percent(comparison: Comparison) -> None:
    gate = GateResult(passed=False, comparison=comparison, reasons=["boom"], warnings=[])
    body = render_pr_comment(comparison, gate, max_regression=5.0, candidate_count=2)
    assert "未通过（成本回归 +12.5%" in body
    assert "门禁理由" in body
    assert "boom" in body


def test_paths_use_forward_slashes(comparison: Comparison) -> None:
    gate = GateResult(passed=False, comparison=comparison, reasons=["x"])
    body = render_pr_comment(comparison, gate, candidate_count=0)
    assert "src/foo.h" in body
    # 在 Windows 上 Path 渲染会带反斜杠，PR 评论里必须统一成 POSIX 风格
    assert "src\\foo.h" not in body


def test_candidate_section_only_when_count_present(comparison: Comparison) -> None:
    gate = GateResult(passed=True, comparison=comparison)
    no_cand = render_pr_comment(comparison, gate, candidate_count=0)
    with_cand = render_pr_comment(comparison, gate, candidate_count=3)
    assert "可精简" not in no_cand
    assert "可精简" in with_cand and "3,456" in with_cand


# --------------------------------------------------------------------------
# PR number resolution
# --------------------------------------------------------------------------


def test_resolve_pr_explicit() -> None:
    assert resolve_pr_number("42") == "42"


def test_resolve_pr_from_pr_number_env(monkeypatch) -> None:
    monkeypatch.setenv("PR_NUMBER", "99")
    monkeypatch.delenv("GITHUB_REF", raising=False)
    assert resolve_pr_number() == "99"


def test_resolve_pr_from_github_ref(monkeypatch) -> None:
    monkeypatch.delenv("PR_NUMBER", raising=False)
    monkeypatch.setenv("GITHUB_REF", "refs/pull/123/merge")
    assert resolve_pr_number() == "123"


def test_resolve_pr_none_when_unknown(monkeypatch) -> None:
    monkeypatch.delenv("PR_NUMBER", raising=False)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    assert resolve_pr_number() is None


# --------------------------------------------------------------------------
# posting via gh
# --------------------------------------------------------------------------


def _fake_gh(stdout: str = "[]", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = ""
    return proc


def test_find_existing_comment_returns_id_when_marker_present() -> None:
    payload = (
        '[{"id": 7, "body": "random"}, '
        '{"id": 9, "body": "<!-- heavyheader-report --> hi"}]'
    )
    with patch("heavyheader.prcomment._run_gh", return_value=_fake_gh(payload)) as m:
        assert find_existing_comment("o/r", "1", MARKER) == "9"
        m.assert_called_once()


def test_find_existing_comment_returns_none_when_absent() -> None:
    payload = '[{"id": 7, "body": "nothing here"}]'
    with patch("heavyheader.prcomment._run_gh", return_value=_fake_gh(payload)):
        assert find_existing_comment("o/r", "1", MARKER) is None


def test_find_existing_comment_returns_none_on_bad_output() -> None:
    # 非 JSON（如速率限制文本）不应抛错，降级为「无既有评论」
    with patch("heavyheader.prcomment._run_gh", return_value=_fake_gh("not json")):
        assert find_existing_comment("o/r", "1", MARKER) is None


def test_post_creates_when_no_prior_comment() -> None:
    with patch(
        "heavyheader.prcomment.find_existing_comment", return_value=None
    ), patch("heavyheader.prcomment._run_gh", return_value=_fake_gh()) as m:
        ok, action = post_comment("body", pr="1", repo="o/r")
    assert ok and action == "发布"
    cmd = " ".join(m.call_args[0][0])
    assert "api" in cmd and "POST" in cmd and "issues/1/comments" in cmd


def test_post_updates_when_prior_comment_exists() -> None:
    # 幂等：同一 PR 反复推送时编辑旧评论，而不是堆积新评论
    with patch(
        "heavyheader.prcomment.find_existing_comment", return_value="55"
    ), patch("heavyheader.prcomment._run_gh", return_value=_fake_gh()) as m:
        ok, action = post_comment("body", pr="1", repo="o/r")
    assert ok and action == "更新"
    cmd = " ".join(m.call_args[0][0])
    assert "PATCH" in cmd and "comments/55" in cmd


def test_post_raises_on_gh_failure() -> None:
    with patch(
        "heavyheader.prcomment.find_existing_comment", return_value=None
    ), patch(
        "heavyheader.prcomment._run_gh",
        return_value=_fake_gh(returncode=1, stdout="err"),
    ):
        with pytest.raises(PRCommentError):
            post_comment("body", pr="1", repo="o/r")


# --------------------------------------------------------------------------
# orchestration (run_pr_comment)
# --------------------------------------------------------------------------


def _after_scan(candidates=2) -> MagicMock:
    after = MagicMock()
    after.ok_tus = [1]
    after.candidates = [MagicMock(estimated_lines_saved=10) for _ in range(candidates)]
    return after


@patch("heavyheader.prcomment.save_baseline")
@patch("heavyheader.prcomment.load_baseline")
@patch("heavyheader.prcomment.evaluate_gate")
@patch("heavyheader.prcomment.compare_scans")
def test_run_prints_body_and_returns_zero(
    m_compare, m_gate, m_load, m_save, comparison: Comparison, capsys
) -> None:
    m_load.side_effect = [MagicMock(), _after_scan()]
    m_compare.return_value = comparison
    m_gate.return_value = GateResult(passed=True, comparison=comparison)

    rc = run_pr_comment(before=Path("b.json"), after=Path("a.json"))

    assert rc == 0
    assert MARKER in capsys.readouterr().out


@patch("heavyheader.prcomment.save_baseline")
@patch("heavyheader.prcomment.load_baseline")
@patch("heavyheader.prcomment.evaluate_gate")
@patch("heavyheader.prcomment.compare_scans")
def test_run_fails_on_regression_when_requested(
    m_compare, m_gate, m_load, m_save, comparison: Comparison
) -> None:
    m_load.side_effect = [MagicMock(), _after_scan()]
    m_compare.return_value = comparison
    m_gate.return_value = GateResult(
        passed=False, comparison=comparison, reasons=["boom"]
    )

    rc = run_pr_comment(
        before=Path("b.json"), after=Path("a.json"), fail_on_regression=True
    )
    assert rc == 1


@patch("heavyheader.prcomment.save_baseline")
@patch("heavyheader.prcomment.load_baseline")
@patch("heavyheader.prcomment.evaluate_gate")
@patch("heavyheader.prcomment.compare_scans")
def test_run_posts_when_gh_available(
    m_compare, m_gate, m_load, m_save, comparison: Comparison
) -> None:
    m_load.side_effect = [MagicMock(), _after_scan()]
    m_compare.return_value = comparison
    m_gate.return_value = GateResult(passed=True, comparison=comparison)

    with patch("heavyheader.prcomment.shutil.which", return_value="gh"), patch(
        "heavyheader.prcomment.resolve_pr_number", return_value="1"
    ), patch("heavyheader.prcomment.resolve_repo", return_value="o/r"), patch(
        "heavyheader.prcomment.post_comment", return_value=(True, "发布")
    ) as m_post:
        rc = run_pr_comment(
            before=Path("b.json"), after=Path("a.json"), post=True
        )
    assert rc == 0
    m_post.assert_called_once()


@patch("heavyheader.prcomment.load_baseline")
@patch("heavyheader.prcomment.evaluate_gate")
@patch("heavyheader.prcomment.compare_scans")
def test_run_exits_two_when_gh_missing_but_post_requested(
    m_compare, m_gate, m_load, comparison: Comparison, capsys
) -> None:
    m_load.side_effect = [MagicMock(), _after_scan()]
    m_compare.return_value = comparison
    m_gate.return_value = GateResult(passed=True, comparison=comparison)

    with patch("heavyheader.prcomment.shutil.which", return_value=None), patch(
        "heavyheader.prcomment.resolve_pr_number", return_value="1"
    ):
        rc = run_pr_comment(
            before=Path("b.json"), after=Path("a.json"), post=True
        )
    # 没有 gh 时不应静默丢结果：正文仍打印到 stdout
    assert rc == 2
    assert MARKER in capsys.readouterr().out


@patch("heavyheader.prcomment.load_baseline")
@patch("heavyheader.prcomment.evaluate_gate")
@patch("heavyheader.prcomment.compare_scans")
def test_run_exits_two_when_no_pr_number(
    m_compare, m_gate, m_load, comparison: Comparison
) -> None:
    m_load.side_effect = [MagicMock(), _after_scan()]
    m_compare.return_value = comparison
    m_gate.return_value = GateResult(passed=True, comparison=comparison)

    with patch("heavyheader.prcomment.shutil.which", return_value="gh"), patch(
        "heavyheader.prcomment.resolve_pr_number", return_value=None
    ):
        rc = run_pr_comment(
            before=Path("b.json"), after=Path("a.json"), post=True
        )
    assert rc == 2


@patch("heavyheader.prcomment.save_baseline")
@patch("heavyheader.prcomment.load_baseline")
@patch("heavyheader.prcomment.evaluate_gate")
@patch("heavyheader.prcomment.compare_scans")
def test_run_updates_baseline_when_passed(
    m_compare, m_gate, m_load, m_save, comparison: Comparison
) -> None:
    after = _after_scan()
    m_load.side_effect = [MagicMock(), after]
    m_compare.return_value = comparison
    m_gate.return_value = GateResult(passed=True, comparison=comparison)

    run_pr_comment(
        before=Path("b.json"), after=Path("a.json"), update_baseline=True
    )
    m_save.assert_called_once_with(Path("b.json"), after)
