"""Commit-level build-cost attribution: the archaeology half of the loop.

``gate`` tells you *that* the build got more expensive.  ``blame`` tells you
*which commit did it, in which file, on which include line, and how much of
the observed delta that change explains* -- the build-cost equivalent of
``git blame``.

Why this is a separate capability rather than a mode of ``compare``:
``compare`` diffs two scans without knowing anything about the repository.
Attribution needs three more ingredients, all of them cheap:

1. the git commit each baseline was taken at (``scan`` records it when the
   tree is a git repository; ``--from``/``--to`` override it);
2. the include-line changes between those two commits (``git diff``);
3. per-translation-unit reachability, which the baselines already carry
   (``translation_units[].dependencies``).

The attribution itself is one graph operation: for a changed edge
``file -> header``, the headers whose attributed-line count should move are
exactly the *exclusive closure* of that edge in the new scan, and the
predicted delta of each is ``line_count x (TUs newly reaching it)``.  The
metric is deterministic, so a correct attribution predicts the observed
per-header deltas exactly -- that is what the confidence grades check.

Honesty rules baked into the output: predictions are labelled as such,
never merged into the observed numbers; deltas no changed edge explains are
reported as ``unexplained`` instead of being quietly distributed.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .baseline import HeaderDelta, compare_scans, load_baseline
from .models import ProjectScan

__all__ = [
    "BlameError",
    "IncludeChange",
    "CommitInfo",
    "BlameEntry",
    "BlameReport",
    "blame_scans",
    "parse_include_diff",
]


class BlameError(RuntimeError):
    """Cost attribution could not be performed."""


_INCLUDE_LINE = re.compile(r"^[+-]\s*#\s*include\s*(<[^>]+>|\"[^\"]+\")")
_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".c++", ".cp", ".h", ".hh", ".hpp", ".hxx", ".inl"}


@dataclass(slots=True)
class IncludeChange:
    """Include-line changes in one file between two revisions."""

    file: Path
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CommitInfo:
    sha: str
    author: str
    date: str
    subject: str


@dataclass(slots=True)
class BlameEntry:
    """One changed include edge, matched against the observed cost delta."""

    commit: CommitInfo
    file: Path
    change: str
    target: str
    target_path: Path | None
    predicted_delta: int
    observed_delta: int
    confidence: str
    note: str = ""
    #: TUs whose reachability of the target moved (the blast population).
    affected_tus: set[Path] = field(default_factory=set)
    #: Headers whose reach moved in exactly that population (the radius).
    blast: set[Path] = field(default_factory=set)


@dataclass(slots=True)
class BlameReport:
    total_before: int
    total_after: int
    from_rev: str
    to_rev: str
    entries: list[BlameEntry] = field(default_factory=list)
    unexplained: int = 0

    @property
    def explained_delta(self) -> int:
        return sum(e.observed_delta for e in self.entries)

    @property
    def net_delta(self) -> int:
        return self.total_after - self.total_before


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo``; return stdout or raise BlameError."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BlameError(f"cannot run git in {repo}: {exc}") from exc
    if proc.returncode != 0:
        raise BlameError(
            f"git {' '.join(args)} failed in {repo}: {(proc.stderr or '').strip()[:200]}"
        )
    return proc.stdout


def _is_git_repo(repo: Path) -> bool:
    try:
        _git(repo, "rev-parse", "--git-dir")
    except BlameError:
        return False
    return True


def _parse_log_line(line: str) -> CommitInfo | None:
    parts = line.split("\x1f")
    if len(parts) != 4:
        return None
    return CommitInfo(sha=parts[0].strip(), author=parts[1].strip(),
                      date=parts[2].strip(), subject=parts[3].strip())


def _introducing_commit(repo: Path, from_rev: str, to_rev: str, file: Path) -> CommitInfo | None:
    """Oldest commit in the range that touched ``file`` -- the attribution
    point for anything that file's change caused."""
    log = _git(
        repo, "log", "--reverse", "--format=%H\x1f%an\x1f%ad\x1f%s",
        f"{from_rev}..{to_rev}", "--", str(file),
    )
    for line in log.splitlines():
        info = _parse_log_line(line)
        if info is not None:
            return info
    return None


# ---------------------------------------------------------------------------
# diff parsing (pure)
# ---------------------------------------------------------------------------


def parse_include_diff(diff_text: str, file: Path) -> IncludeChange:
    """Extract added/removed ``#include`` lines from a unified diff.

    Only lines that start with ``+``/``-`` and are include directives count;
    diff headers and context lines never do.  Kept pure so the parsing can be
    tested without a repository.
    """
    change = IncludeChange(file=file)
    for line in diff_text.splitlines():
        if line.startswith(("+++", "---")):
            continue
        match = _INCLUDE_LINE.match(line)
        if match is None:
            continue
        if line.startswith("+"):
            change.added.append(match.group(1))
        elif line.startswith("-"):
            change.removed.append(match.group(1))
    return change


# ---------------------------------------------------------------------------
# reachability (pure)
# ---------------------------------------------------------------------------


def _reachability(scan: ProjectScan) -> dict[Path, set[Path]]:
    """``tu source -> every path that TU pulled in`` (from the dep lists)."""
    return {tu.source: set(tu.dependencies) for tu in scan.tus if tu.error is None}


def _newly_reaching(
    before: ProjectScan, after: ProjectScan, target: Path
) -> set[Path]:
    """TUs that reach ``target`` in ``after`` but did not in ``before``.

    For a removed include edge the caller swaps the two scans, which turns
    this into "TUs that lost the target" -- the same population, viewed
    from the other side.
    """
    prior = _reachability(before)
    result: set[Path] = set()
    for source, deps in _reachability(after).items():
        if target in deps and target not in prior.get(source, set()):
            result.add(source)
    return result


def _affected_headers(
    before: ProjectScan, after: ProjectScan, target: Path, affected: set[Path]
) -> set[Path]:
    """Headers whose reachability moved in exactly the same TU population.

    This is the flat-dependency stand-in for "the target's closure": a
    header belongs to the change's blast radius when precisely the TUs that
    newly (or no longer) reach the target also newly (or no longer) reach
    it.  Ancestors and co-included siblings never qualify -- their own
    reach did not move -- while a real subtree does (every one of its
    headers newly reaches the same TU set).
    """
    if not affected:
        return {target} if target in after.header_stats or target in before.header_stats else set()
    result = {target}
    for path in set(after.header_stats) | set(before.header_stats):
        if path == target:
            continue
        if _newly_reaching(before, after, path) == affected:
            result.add(path)
    return result


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------


def resolve_target(raw: str, *scans: ProjectScan) -> Path | None:
    """Resolve an include target (``"foo.h"``, ``<vector>``) to a header
    path recorded in one of the scans.

    Angle includes are matched by path suffix (``dir/foo.h`` against
    ``.../dir/foo.h``).  System headers that never appear in the scan's
    header list resolve to ``None`` -- removing a system include is rarely
    the actionable change and the report says so instead of guessing.
    """
    needle = raw.strip("\"<>")
    for scan in scans:
        for path in scan.header_stats:
            if path.name == needle or str(path).replace("\\", "/").endswith(
                needle.replace("\\", "/")
            ):
                return path
    return None


def blame_scans(
    before: ProjectScan,
    after: ProjectScan,
    repo: Path,
    from_rev: str | None = None,
    to_rev: str | None = None,
) -> BlameReport:
    """Attribute the cost delta between two scans to commits and include lines.

    ``from_rev``/``to_rev`` default to the git commits recorded in the
    baselines (``scan`` fills them in when the tree is a repository).
    """
    if not _is_git_repo(repo):
        raise BlameError(f"{repo} is not a git repository; blame needs history")

    from_rev = from_rev or before.git_commit
    to_rev = to_rev or after.git_commit
    if not from_rev or not to_rev:
        raise BlameError(
            "no revision range: pass --from/--to, or regenerate the baselines "
            "inside the git repository so scan can record the commits"
        )

    comparison = compare_scans(before, after)
    deltas = {d.path: d for d in comparison.header_deltas if d.delta != 0}

    report = BlameReport(
        total_before=comparison.attributed_lines_before,
        total_after=comparison.attributed_lines_after,
        from_rev=from_rev,
        to_rev=to_rev,
    )
    if not deltas:
        return report

    reach_before = _reachability(before)
    reach_after = _reachability(after)

    raw_files = _git(repo, "diff", "--name-only", f"{from_rev}..{to_rev}")
    changed_files = [
        Path(line.strip())
        for line in raw_files.splitlines()
        if line.strip() and Path(line.strip()).suffix.lower() in _SOURCE_SUFFIXES
    ]

    entries: list[BlameEntry] = []
    # git works with repo-relative paths, the baselines with absolute ones.
    # Resolving every changed file against the scan roots keeps the
    # reachability comparison meaningful; a file that matches nothing is
    # left as-is and its prediction honestly stays 0.
    known_paths = set(reach_before) | set(reach_after)
    for deps in list(reach_before.values()) + list(reach_after.values()):
        known_paths |= deps

    def _abs(rel: Path) -> Path:
        for base in (repo, before.root, after.root):
            candidate = base / rel
            if candidate in known_paths:
                return candidate
        return rel

    for rel in changed_files:
        diff_text = _git(repo, "diff", "--unified=0", f"{from_rev}..{to_rev}", "--", str(rel))
        change = parse_include_diff(diff_text, rel)
        commit = _introducing_commit(repo, from_rev, to_rev, rel)
        if commit is None:  # pragma: no cover - git always lists a commit here
            continue
        abs_file = _abs(rel)
        for sign, targets in (("+", change.added), ("-", change.removed)):
            for raw in targets:
                target = resolve_target(raw, after, before)
                predicted = 0
                observed = deltas.get(target, HeaderDelta(path=target or Path(raw),
                                                          before=0, after=0)).delta if target else 0
                if target is not None:
                    # An added edge is measured as "TUs that newly reach the
                    # target"; a removed one as "TUs that lost it" -- the same
                    # population from the other side.  Only TUs that also
                    # reach the changed file itself count: a TU that got the
                    # target through some other path is not this edge's doing.
                    b, a = (before, after) if sign == "+" else (after, before)
                    reach_map = _reachability(after if sign == "+" else before)
                    affected = {
                        source
                        for source in _newly_reaching(b, a, target)
                        if abs_file in reach_map.get(source, set())
                    }
                    blast = _affected_headers(b, a, target, affected)
                    predicted = _predicted_signed(target, affected, sign, blast, before, after)
                    observed = sum(deltas[p].delta for p in blast if p in deltas)
                    note = ""
                else:
                    affected, blast = set(), set()
                    note = "目标不在扫描的头文件列表内（可能是系统头）——无法量化"
                entries.append(
                    BlameEntry(
                        commit=commit,
                        file=rel,
                        change=f"{sign}#include {raw}",
                        target=raw,
                        target_path=target,
                        predicted_delta=predicted,
                        observed_delta=observed,
                        confidence=_grade(target, predicted, observed, deltas, after),
                        note=note,
                        affected_tus=affected,
                        blast=blast,
                    )
                )

    # Greedy by predicted magnitude: when two changed edges share blast
    # headers, the bigger predicted change claims them first and the other is
    # graded against what is left.  Documented, not hidden.
    entries.sort(key=lambda e: abs(e.predicted_delta), reverse=True)
    claimed: set[Path] = set()
    for entry in entries:
        if entry.target_path is None:
            continue
        fresh = {p for p in entry.blast if p in deltas and p not in claimed}
        entry.observed_delta = sum(deltas[p].delta for p in fresh)
        claimed |= fresh
        if entry.observed_delta or entry.predicted_delta:
            entry.confidence = _grade(
                entry.target_path, entry.predicted_delta, entry.observed_delta, deltas, after
            )

    report.entries = entries
    report.unexplained = report.net_delta - report.explained_delta
    return report


def _predicted_signed(
    target: Path,
    affected: set[Path],
    sign: str,
    blast: set[Path],
    before: ProjectScan,
    after: ProjectScan,
) -> int:
    """Attributed-line delta predicted for the whole blast radius.

    ``sum(line_count(x) for x in blast) x len(affected)``, signed by the
    change direction.  Line counts come from the scan that still has the
    files (``after`` for additions, ``before`` for removals).
    """
    source = after if sign == "+" else before
    lines = sum(
        stats.line_count
        for path, stats in source.header_stats.items()
        if path in blast
    )
    if not lines or not affected:
        return 0
    total = lines * len(affected)
    return total if sign == "+" else -total


def _grade(
    target: Path | None,
    predicted: int,
    observed: int,
    deltas: dict[Path, HeaderDelta],
    after: ProjectScan,
) -> str:
    """Confidence that this include change explains the observed delta.

    * ``high``   -- the prediction matches the observation exactly (the
      metric is deterministic, so this is the expected case);
    * ``medium`` -- the prediction is non-zero and in the right direction,
      or the observation is non-zero but the prediction is not;
    * ``low``    -- neither side lines up (e.g. the target is not in the
      scan, or the delta came from somewhere else entirely).
    """
    if target is None:
        return "low"
    if predicted == observed and predicted != 0:
        return "high"
    if predicted != 0 and observed != 0 and (predicted > 0) == (observed > 0):
        return "medium"
    if predicted != 0 or observed != 0:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_markdown(report: BlameReport) -> str:
    """Human-readable attribution report."""
    lines = [
        "# 成本考古（blame）",
        "",
        f"- 归因行数：{report.total_before:,} → {report.total_after:,}"
        f"（{report.net_delta:+,}，{_pct(report)}）",
        f"- 版本范围：`{report.from_rev}..{report.to_rev}`",
        f"- 已解释：{report.explained_delta:+,}；未解释：{report.unexplained:+,}",
        "",
    ]
    if not report.entries:
        lines += ["两个扫描之间没有成本变化，或没有可归因的 include 改动。", ""]
        return "\n".join(lines)
    lines += [
        "| commit | 文件 | 改动 | 预测 Δ | 实测 Δ | 置信度 |",
        "|---|---|---|---:|---:|---|",
    ]
    for e in report.entries:
        subject = e.commit.subject[:40] + ("…" if len(e.commit.subject) > 40 else "")
        lines.append(
            f"| `{e.commit.sha[:8]}` {subject} | `{e.file}` | "
            f"`{e.change}` | {e.predicted_delta:+,} | {e.observed_delta:+,} | {e.confidence} |"
        )
    lines.append("")
    if report.unexplained:
        lines += [
            f"> 未解释的 {report.unexplained:+,} 行：可能来自非 include 的改动"
            "（宏、模板、源码本身）或已知盲区，未硬分派给任何 commit。",
            "",
        ]
    lines += [
        "口径说明：预测值是「独占闭包 × 新增可达 TU 数」的图运算结果，实测值来自"
        "两份基线的归因行数差；两者分别是估计与实测，不相加。",
    ]
    return "\n".join(lines)


def _pct(report: BlameReport) -> str:
    if not report.total_before:
        return "n/a"
    return f"{report.net_delta / report.total_before * 100:+.1f}%"


def blame_baselines(
    before_path: str | Path,
    after_path: str | Path,
    repo: str | Path | None = None,
    from_rev: str | None = None,
    to_rev: str | None = None,
) -> BlameReport:
    """Load two baseline files and attribute the delta between them."""
    before = load_baseline(before_path)
    after = load_baseline(after_path)
    target_repo = Path(repo) if repo else Path(before.root)
    return blame_scans(before, after, target_repo, from_rev, to_rev)
