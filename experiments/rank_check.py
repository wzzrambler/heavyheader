"""Retrospective check: where does the new ranking put the 17 known outcomes?

The controlled experiment (``control.py``) burned 17 build trials on
nlohmann/json and recorded what happened to each one: 3 removals made the
unit compile faster, 12 failed to compile, 1 changed nothing, and 1 was not
even a candidate the tool could produce.

That is a ground truth nobody has to re-measure.  So when the candidate
ranking is changed, the honest way to evaluate the change is *not* to re-run
the whole experiment and hope -- it is to ask what the new ranking would have
said about edits whose outcome is already known:

* the 3 known wins must be near the top (otherwise the tool still cannot
  find the optimisations that exist);
* the 12 known failures must be *demoted* by the tool's own usage verdict
  (otherwise it is still recommending edits that do not compile).

Rank is measured on the deduplicated list, because that is the policy the
experiment's tool arm used: one trial per header, so one row per header.

This script costs one scan and zero build trials, and it prints both numbers
and the reason for each verdict.  Run it after touching
``attrib/graph.py`` or ``attrib/usage.py``.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(HERE))

from build import CXX, test_data_dir, write_generated_headers  # noqa: E402
from fetch import fetch  # noqa: E402
from run_experiment import compiler_banner  # noqa: E402

from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402


def dedupe_by_header(rows: list[dict]) -> list[dict]:
    """One row per header, first occurrence wins -- the experiment's policy."""
    seen: set[str] = set()
    out: list[dict] = []
    for row in rows:
        key = row["key"]
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _field(rows: list[dict], key: tuple[str, str], field: str):
    """Look up one candidate's field, tolerating same-basename collisions."""
    for row in rows:
        if (row["unit"], row["basename"]) == key:
            return row[field]
    return None


def main() -> int:
    control_path = HERE / "results" / "control-20261017.json"
    control = json.loads(control_path.read_text(encoding="utf-8"))

    manifest = fetch()
    root = Path(manifest["source_dir"])
    work = HERE / "_work"
    write_generated_headers(root, work / "control", test_data_dir())

    print("[rank-check] scanning …", flush=True)
    options = ScanOptions(
        root=root,
        compiler=CXX,
        compile_commands=work / "compile_commands.json",
        jobs=8,
    )
    started = time.perf_counter()
    scan = scan_project(options, driver=detect_driver(CXX))
    scan_seconds = time.perf_counter() - started

    rows = []
    for candidate in scan.candidates:
        try:
            relative = Path(candidate.header).resolve().relative_to(scan.root.resolve())
            key = relative.as_posix()
        except (ValueError, OSError):
            key = Path(candidate.header).name
        rows.append(
            {
                "unit": candidate.tu.stem,
                "header": candidate.header,
                "key": key,
                "basename": Path(candidate.header).name,
                "verdict": candidate.usage_verdict,
                "confidence": candidate.confidence,
                "seconds": candidate.estimated_seconds_saved,
                "lines": candidate.estimated_lines_saved,
                "provider": candidate.notes[0] if candidate.notes else "",
            }
        )

    deduped = dedupe_by_header(rows)
    # 排名按**完整列表**算：工具真正排的就是 (单元, include) 这个二维空间。
    # 去重后的列表只用来报告「如果按实验口径每头只试一次，它会挑哪个单元」。
    rank_full: dict[tuple[str, str], int] = {}
    rank_deduped: dict[tuple[str, str], int] = {}
    for position, row in enumerate(rows, start=1):
        rank_full.setdefault((row["unit"], row["basename"]), position)
    for position, row in enumerate(deduped, start=1):
        rank_deduped.setdefault((row["unit"], row["basename"]), position)

    outcomes = []
    for arm_name, arm in control["arms"].items():
        for outcome in arm["outcomes"]:
            target = str(outcome["target"])
            unit = outcome["unit"]
            normalised = target.replace("\\", "/")
            basename = normalised.rsplit("/", 1)[-1]
            key = (unit, basename)
            outcomes.append(
                {
                    "arm": arm_name,
                    "unit": unit,
                    "target": target,
                    "status": outcome["status"],
                    "saved_percent": outcome.get("saved_percent"),
                    "rank": rank_full.get(key),
                    "rank_deduped": rank_deduped.get(key),
                    "verdict": _field(rows, key, "verdict"),
                    "confidence": _field(rows, key, "confidence"),
                    "seconds": _field(rows, key, "seconds"),
                    "provider": _field(rows, key, "provider") or "",
                }
            )

    total = len(rows)
    wins = [o for o in outcomes if o["status"] == "improved"]
    losses = [o for o in outcomes if o["status"] == "compile-error"]

    def top_share(items: list[dict], fraction: float) -> int:
        cutoff = max(1, int(total * fraction))
        return sum(1 for item in items if item["rank"] and item["rank"] <= cutoff)

    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project": f"{manifest['project']} {manifest['tag']}",
        "compiler": compiler_banner(),
        "scan_seconds": round(scan_seconds, 2),
        "candidates": len(rows),
        "candidates_deduped": len(deduped),
        "by_verdict": _counts(row["verdict"] for row in rows),
        "known_wins": len(wins),
        "known_wins_ranked": [o["rank"] for o in wins],
        "known_wins_missing": sum(1 for o in wins if o["rank"] is None),
        "known_failures": len(losses),
        "known_failures_demoted": sum(
            1 for o in losses if o["verdict"] in ("in_use", "unknown")
        ),
        "known_failures_in_space": sum(1 for o in losses if o["rank"] is not None),
        "known_failures_ranked": [o["rank"] for o in losses],
        "wins_in_top_10pct": top_share(wins, 0.10),
        "wins_in_top_25pct": top_share(wins, 0.25),
    }

    report = {"summary": summary, "outcomes": outcomes}
    out_dir = HERE / "results"
    stem = f"rank-check-{time.strftime('%Y%m%d')}"
    (out_dir / f"{stem}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / f"{stem}.md").write_text(render(report), encoding="utf-8")
    print(f"[rank-check] wrote results/{stem}.{{json,md}}")
    return 0


def _counts(values) -> dict:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def render(report: dict) -> str:
    s = report["summary"]
    outcomes = report["outcomes"]
    wins = [o for o in outcomes if o["status"] == "improved"]
    losses = [o for o in outcomes if o["status"] == "compile-error"]

    def fmt(value) -> str:
        return "—" if value is None else str(value)

    lines = [
        "# 回顾性检验：新排序把已实测的 17 个结局放在第几位",
        "",
        "> 由 `experiments/rank_check.py` 生成。**零次构建试验**——用的是 "
        "`control-20261017.json` 里已经测出来的结局当答案，只看新排序会怎么排。",
        "> 改过 `attrib/graph.py` 或 `attrib/usage.py` 之后就跑它。",
        "",
        f"工程：**{s['project']}**　编译器：`{s['compiler']}`",
        "",
        "## 新排序的候选空间",
        "",
        f"- 候选（(单元, include) 二维空间）：**{s['candidates']}** 个；"
        f"按实验口径每头只留一条：**{s['candidates_deduped']}** 个",
        f"- 用法判定分布：{_fmt_counts(s['by_verdict'])}",
        f"- 扫描耗时：**{s['scan_seconds']:.1f} s**",
        "",
        "名次的分母是**完整列表**（工具真正排序的空间，含系统头）。",
        "",
        "## 3 个已知有效改动排在哪",
        "",
        "| 单元 | include | 单测编译时间降幅 | 新排名 | 名次分位 | 用法判定 |",
        "|---|---|---:|---:|---:|---|",
    ]
    for o in sorted(wins, key=lambda x: (x["rank"] is None, x["rank"] or 0)):
        pct = (o["rank"] / s["candidates"] * 100.0) if o["rank"] else None
        lines.append(
            f"| `{o['unit']}` | `{o['target']}` | "
            f"{o['saved_percent']:+.1f}% | {fmt(o['rank'])} | "
            f"{'—' if pct is None else f'{pct:.0f}%'} | {fmt(o['verdict'])} |"
        )

    lines += [
        "",
        f"- 落在前 10%：**{s['wins_in_top_10pct']}/{s['known_wins']}**；"
        f"前 25%：**{s['wins_in_top_25pct']}/{s['known_wins']}**；"
        f"不在候选空间里：**{s['known_wins_missing']}** 个",
        "",
        "## 13 个已知失败的候选有没有被压下去",
        "",
        "| 单元 | include | 新排名 | 用法判定 | 为什么（工具给的） |",
        "|---|---|---:|---|---|",
    ]
    for o in sorted(
        losses, key=lambda x: (x["rank"] is None, x["rank"] or 0)
    ):
        reason = _shorten(o["provider"], 60)
        lines.append(
            f"| `{o['unit']}` | `{o['target']}` | {fmt(o['rank'])} | "
            f"{fmt(o['verdict'])} | {reason} |"
        )
    lines += [
        "",
        f"- 被判定为「正在使用 / 无法判定」而压后的："
        f"**{s['known_failures_demoted']}/{s['known_failures']}**"
        f"（另有 {s['known_failures'] - s['known_failures_in_space']} 个根本不在候选空间里，"
        "说明它们的闭包已被别的 include 覆盖，删了也省不下时间）",
        "",
        "## 怎么读这张表",
        "",
        "**排名靠前不等于已经验证。** 它仍然只是排序，最后的判定权在 `verify`："
        "那一层会真的注释掉这一行、重编、测时间。这份检验只回答一个问题——"
        "**在花任何一次构建试验之前，工具把已知的好改动和已知的坏改动分开了吗。**",
        "",
        "“—”表示这个 (单元, include) 根本不在新候选空间里，通常是该 include 被判定"
        "为「删掉也省不下任何东西」（它的闭包已经被别的 include 覆盖）。",
        "",
    ]
    return "\n".join(lines)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_counts(counts: dict) -> str:
    return "、".join(f"{k} {v} 个" for k, v in sorted(counts.items()))


if __name__ == "__main__":
    sys.exit(main())
