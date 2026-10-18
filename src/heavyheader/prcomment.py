"""Render and post a Pull Request comment from a scan comparison.

A build-cost tool is only useful if its verdict reaches the people who can act
on it.  ``pr-comment`` turns the output of :func:`compare_scans` and
:func:`evaluate_gate` into a GitHub-flavoured Markdown comment and -- when asked
-- posts it through the ``gh`` CLI.

Design choices, all of which are load-bearing:

* **No third-party libraries.**  Posting uses the ``gh`` CLI, which is present
  on GitHub Actions runners out of the box.  Adding ``requests`` or ``PyGithub``
  would break the "runs in a bare CI container" promise for one feature that
  already has a free, pre-installed client.
* **Idempotent by default.**  Every comment carries a hidden HTML marker.  When
  a PR is pushed to repeatedly, the bot edits its own earlier comment instead of
  appending a new one each time, so reviewers are not buried in duplicate noise.
* **Degrades gracefully.**  If ``gh`` is missing or the environment does not
  expose a PR number, the command still prints the Markdown (and can write it to
  a file), so the body is never lost; only the network step fails, with a clear
  message and a distinct exit code.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .baseline import (
    BaselineError,
    Comparison,
    GateResult,
    compare_scans,
    evaluate_gate,
    load_baseline,
    save_baseline,
)
from .models import ProjectScan

__all__ = [
    "MARKER",
    "PRCommentError",
    "render_pr_comment",
    "resolve_pr_number",
    "resolve_repo",
    "find_existing_comment",
    "post_comment",
    "run_pr_comment",
]

# A hidden marker so the bot can find and update its own previous comment
# instead of leaving a trail of duplicates on every push.
MARKER = "<!-- heavyheader-report -->"


class PRCommentError(RuntimeError):
    """Posting failed (missing tooling, no PR number, network error)."""


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _fmt_change(before: float, after: float, *, signed: bool = True) -> str:
    """Human readable delta for the summary table."""
    if before == after:
        return "—"
    percent = (after - before) / before * 100.0 if before else 0.0
    if not signed:
        percent = abs(percent)
    return f"{percent:+.1f}%"


def _posix(path: Path) -> str:
    """Render a path with forward slashes regardless of the host OS.

    PR comments are read on GitHub, where POSIX-style paths are the only
    convention that means anything; a Windows backslash here would just look
    like a typo to a reviewer.
    """
    return path.as_posix()


def render_pr_comment(
    comparison: Comparison,
    gate: GateResult,
    *,
    title: str = "HeavyHeader 构建成本报告",
    version: str = "",
    max_regression: float = 5.0,
    candidate_count: int = 0,
) -> str:
    """Build the Markdown body for a PR comment.

    The body is GitHub-flavoured: a verdict line, a compact metrics table,
    the worst regressed/most improved headers, candidate savings, and a
    collapsible block of the gate reasons.  A hidden ``MARKER`` is prepended so
    the bot can recognise and update the comment later.
    """
    verdict = "通过"
    if not gate.passed:
        verdict = (
            f"未通过（成本回归 {comparison.attributed_percent:+.1f}%，"
            f"预算 {max_regression:+.1f}%）"
        )

    lines: list[str] = [
        MARKER,
        "",
        f"## {title}",
        "",
        f"**结论：{verdict}**",
        "",
        "| 指标 | 基线 | 本次 PR | 变化 |",
        "|---|---:|---:|---:|",
        f"| 头文件解析量 | {comparison.attributed_lines_before:,} "
        f"| {comparison.attributed_lines_after:,} "
        f"| {_fmt_change(comparison.attributed_lines_before, comparison.attributed_lines_after)} |",
        f"| 编译耗时 | {comparison.total_seconds_before:.2f}s "
        f"| {comparison.total_seconds_after:.2f}s "
        f"| {_fmt_change(comparison.total_seconds_before, comparison.total_seconds_after)} |",
        f"| 编译单元 | {comparison.tu_count_before} "
        f"| {comparison.tu_count_after} "
        f"| {_fmt_change(comparison.tu_count_before, comparison.tu_count_after, signed=False)} |",
        f"| 缺 guard 头文件 | {comparison.unguarded_before} "
        f"| {comparison.unguarded_after} "
        f"| {_fmt_change(comparison.unguarded_before, comparison.unguarded_after, signed=False)} |",
        "",
    ]

    if comparison.regressed_headers:
        lines += [
            "### 成本上升的头文件",
            "",
            "| 头文件 | 之前 | 之后 | 变化 |",
            "|---|---:|---:|---:|",
        ]
        for delta in comparison.regressed_headers[:15]:
            lines.append(
                f"| `{_posix(delta.path)}` | {delta.before:,} | {delta.after:,} "
                f"| {delta.percent:+.1f}% |"
            )
        lines.append("")

    if comparison.improved_headers:
        lines += [
            "### 成本下降的头文件",
            "",
            "| 头文件 | 之前 | 之后 | 变化 |",
            "|---|---:|---:|---:|",
        ]
        for delta in comparison.improved_headers[:15]:
            lines.append(
                f"| `{_posix(delta.path)}` | {delta.before:,} | {delta.after:,} "
                f"| {delta.percent:+.1f}% |"
            )
        lines.append("")

    if candidate_count:
        lines += [
            "### 可精简的 include（候选）",
            "",
            f"本次扫描发现 **{candidate_count}** 处可精简的 include，"
            f"预计可省 **{comparison.candidate_savings:,}** 行解析量。",
            "",
        ]

    if comparison.new_headers:
        lines.append(f"新增头文件：**{len(comparison.new_headers)}** 个。")
        lines.append("")

    if not gate.passed:
        lines += [
            "<details><summary>门禁理由</summary>",
            "",
        ]
        for reason in gate.reasons:
            lines.append(f"- {reason}")
        if gate.warnings:
            lines.append("")
            for warning in gate.warnings:
                lines.append(f"- 警告：{warning}")
        lines += ["", "</details>", ""]

    footer_v = f" v{version}" if version else ""
    lines += [
        "---",
        f"<sub>由 HeavyHeader{footer_v} 生成 · 数据直接来自编译器自身输出</sub>",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# posting (via gh CLI)
# --------------------------------------------------------------------------


def resolve_pr_number(pr_arg: str | None = None) -> str | None:
    """Work out which PR to comment on.

    Priority: explicit ``--pr``, then common CI env vars, then parse the PR
    number out of ``GITHUB_REF`` (GitHub Actions sets it to
    ``refs/pull/<n>/merge``).
    """
    if pr_arg:
        return pr_arg
    for var in ("PR_NUMBER", "GITHUB_PR_NUMBER", "PR_ID"):
        value = os.environ.get(var)
        if value:
            return value.strip()
    ref = os.environ.get("GITHUB_REF", "")
    match = re.search(r"refs/pull/(\d+)/", ref)
    if match:
        return match.group(1)
    return None


def resolve_repo() -> str | None:
    """Return ``owner/repo`` for the current checkout, if discoverable."""
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    result = _run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def find_existing_comment(repo: str, pr: str, marker: str) -> str | None:
    """Return the id of this bot's previous comment on the PR, if any.

    Best-effort: any failure (rate limit, weird output) returns ``None`` and
    the caller simply creates a new comment.
    """
    result = _run_gh(["api", f"repos/{repo}/issues/{pr}/comments"])
    if result.returncode != 0:
        return None
    try:
        comments = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    for comment in comments:
        body = comment.get("body") or ""
        if marker in body:
            return str(comment.get("id"))
    return None


def post_comment(
    body: str,
    *,
    pr: str,
    repo: str,
    marker: str = MARKER,
) -> tuple[bool, str]:
    """Post ``body`` to PR ``pr`` in ``repo``, updating the prior comment.

    Returns ``(ok, action)`` where ``action`` is ``"发布"`` (created) or
    ``"更新"`` (edited).  The body is passed to ``gh`` via a temp file so that
    newlines and special characters need no shell quoting.
    """
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    )
    try:
        handle.write(body)
        handle.close()
        existing = find_existing_comment(repo, pr, marker)
        if existing:
            result = _run_gh(
                [
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/{repo}/issues/comments/{existing}",
                    "-F",
                    f"body=@{handle.name}",
                ]
            )
            action = "更新"
        else:
            result = _run_gh(
                [
                    "api",
                    "-X",
                    "POST",
                    f"repos/{repo}/issues/{pr}/comments",
                    "-F",
                    f"body=@{handle.name}",
                ]
            )
            action = "发布"
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PRCommentError(f"gh 调用失败（{action}）：{detail}")
    return True, action


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def run_pr_comment(
    *,
    before: Path,
    after: Path,
    pr: str | None = None,
    post: bool = False,
    fail_on_regression: bool = False,
    update_baseline: bool = False,
    max_regression: float = 5.0,
    heavy_header_lines: int = 2000,
    out: Path | None = None,
    title: str = "HeavyHeader 构建成本报告",
) -> int:
    """Load two baselines, render the comment, optionally post it.

    Exit codes:
      ``0``  comment produced (and, if requested, posted); no regression or
             ``--fail-on-regression`` not set;
      ``1``  gate failed and ``--fail-on-regression`` was set;
      ``2``  could not load a baseline, or posting was requested but impossible.
    """
    try:
        before_scan: ProjectScan = load_baseline(before)
        after_scan: ProjectScan = load_baseline(after)
    except BaselineError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    comparison = compare_scans(before_scan, after_scan)
    gate = evaluate_gate(
        before_scan,
        after_scan,
        max_regression_percent=max_regression,
        heavy_header_lines=heavy_header_lines,
    )

    body = render_pr_comment(
        comparison,
        gate,
        title=title,
        version=__version__,
        max_regression=max_regression,
        candidate_count=len(after_scan.candidates),
    )

    if out is not None:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(body, encoding="utf-8")
        print(f"已写入 {out_path}", file=sys.stderr)

    print(body)

    if update_baseline and gate.passed and after_scan.ok_tus:
        save_baseline(before, after_scan)
        print(f"基线已更新：{before}", file=sys.stderr)

    if post:
        pr_number = resolve_pr_number(pr)
        if not pr_number:
            print(
                "错误：无法确定 PR 编号（用 --pr 指定，或在 CI 中确保 GITHUB_REF "
                "含 pull 编号）。评论正文已打印到标准输出。",
                file=sys.stderr,
            )
            return 2
        if not shutil.which("gh"):
            print(
                "错误：未找到 gh CLI，无法发布评论。请安装 gh，或将上文评论正文自行粘贴。",
                file=sys.stderr,
            )
            return 2
        repo = resolve_repo()
        if not repo:
            print(
                "错误：无法确定仓库（设置 GITHUB_REPOSITORY，或在仓库内运行 gh）。",
                file=sys.stderr,
            )
            return 2
        try:
            _ok, action = post_comment(body, pr=pr_number, repo=repo)
        except PRCommentError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 2
        print(f"已{action} PR #{pr_number} 的评论（仓库 {repo}）。", file=sys.stderr)

    if fail_on_regression and not gate.passed:
        return 1
    return 0
