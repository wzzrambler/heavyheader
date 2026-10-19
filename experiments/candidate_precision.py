"""候选精度基准：拿真实编译结果与真实解析量当真值。

回答两个不同的问题，缺一不可：

1. **承诺是否成立**——候选说「删掉能省 N 行」，删掉之后真的少解析了文件吗？
   只看「能编译」会把「编得过但一个文件都没少」这种空头承诺算成成功。实测
   doctest 上 60 条候选里有 22 条正是这一类（承诺 27 万行，实测差 0）。
2. **排在前面的有没有用**——precision@k 统计 top-k 里「既有用又编得过」的比例。

真值全部来自编译器，不来自人工判断：

* ``-H`` 的事件数差 = 真实少解析的文件数（确定性，无噪声）；
* 编译返回码 = 这条改动能不能落地。

三种排序口径对照：``rank``（当前实现）、``usage``（只看 usage 分层 + 行数）、
``lines``（纯归因行数，等价于 Header Hero 的成本排序）。

用法::

    python experiments/candidate_precision.py \
        --project <dir> --db <compile_commands.json> --compiler clang++ \
        --tus 24 --out experiments/results/candidate-precision-<name>.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heavyheader.parse.includetree import parse_include_tree  # noqa: E402
from heavyheader.scanner import (  # noqa: E402
    ScanOptions,
    _split_command,
    _strip_output_flag,
    _strip_source_token,
    scan_project,
)

USAGE_TIER = {"plausibly_unused": 0, "unknown": 1, "in_use": 2}


def _key(path: str | Path) -> str:
    """归一化路径键。

    ``compile_commands`` 里写的是 ``C:/x/y.cpp``，扫描器给出的是 ``C:\\x\\y.cpp``，
    ``str(Path(...))`` 会**保留**原来的分隔符风格，两边直接比永远不相等——实测
    因此让全部候选都测不到解析量，基准等于没跑。
    """
    return os.path.normcase(os.path.normpath(str(path)))


def _entries(db_path: Path) -> dict[str, dict]:
    data = json.loads(db_path.read_text(encoding="utf-8"))
    return {_key(item["file"]): item for item in data}


def _compile(
    entry: dict, patch_line: int | None = None
) -> tuple[int | None, int]:
    """编译一次，返回 ``(解析文件数, 返回码)``。

    ``patch_line`` 给出时把那一行注释掉——模拟「删掉这条 include」。源文件替换
    通过编译数据库里的命令行完成，保证与真实构建用同一套参数。
    """
    argv = _strip_output_flag(_split_command(entry["command"]))
    with tempfile.TemporaryDirectory(prefix="hh-precision-") as tmp:
        target = Path(entry["file"])
        if patch_line is not None:
            lines = target.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(keepends=True)
            patched = Path(tmp) / target.name
            patched.write_text(
                "".join(
                    ("// " + line if i == patch_line else line)
                    for i, line in enumerate(lines, 1)
                ),
                encoding="utf-8",
            )
        else:
            patched = target
        argv = [str(patched) if Path(token) == target else token for token in argv]
        out = Path(tmp) / "x.o"
        proc = subprocess.run(
            [*argv, "-H", "-o", str(out)],
            cwd=entry["directory"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if proc.returncode != 0:
            return None, proc.returncode
        return len(parse_include_tree(proc.stderr, patched)), 0


def _codegen_same(entry: dict, patch_line: int, compiler: str) -> str:
    """第三条真值：删掉之后**程序**还是不是同一个。

    前两条真值（编不编得过、少解析几个文件）都不回答这个问题。实测 zlib
    ``zutil.c -> gzguts.h``：编译通过、少解析 15 个文件，按旧口径是「真有用」；
    但汇编指纹从 ``aac1b5f8…`` 变成 ``9b6bde46…``——被激活的条件编译分支变了。
    不看这一维，基准会把「静默改程序」的改动算成成功，然后逼着算法去追它。
    """
    from heavyheader.codegen import compare
    from heavyheader.drivers.base import DriverOptions, detect_driver

    target = Path(entry["file"])
    argv = _strip_output_flag(_split_command(entry["command"]))
    argv = _strip_source_token(argv, str(entry["file"]))
    options = DriverOptions(extra_flags=argv[1:])

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    patched_text = "".join(
        "// " + line if i == patch_line else line
        for i, line in enumerate(lines, 1)
    )
    with tempfile.TemporaryDirectory(prefix="hh-precision-cg-") as tmp:
        work = Path(tmp)
        holder = work / "patched"
        holder.mkdir()
        patched = holder / target.name
        patched.write_text(patched_text, encoding="utf-8")
        before, after = compare(
            detect_driver(compiler),
            target,
            patched,
            options,
            Path(entry["directory"]),
            work / "stage",
        )
    if not (before.ok and after.ok):
        return "unknown"
    return "same" if before.digest == after.digest else "changed"


def precision_at(rows: list[dict], k: int) -> float:
    top = rows[:k]
    if not top:
        return 0.0
    return sum(1 for r in top if r["useful"]) / len(top)


def first_hit(rows: list[dict], key: str) -> int | None:
    for i, r in enumerate(rows, 1):
        if r[key]:
            return i
    return None


def run(args) -> dict:
    entries = _entries(args.db) if args.db else {}
    scan = scan_project(
        ScanOptions(
            root=args.project,
            compiler=args.compiler,
            include_dirs=list(args.includes),
            compile_commands=args.db,
            max_tus=args.tus or 500,  # 0 = 不限（与 CLI 的默认值一致）
            probe_compile=True,
            probe_top=0,
            probe_jobs=args.jobs,
        )
    )

    baseline: dict[str, int] = {}
    rows: list[dict] = []
    for candidate in scan.candidates:
        key = _key(candidate.tu)
        entry = entries.get(key)
        shrink: int | None = None
        if entry is not None and candidate.line is not None:
            if key not in baseline:
                count, _rc = _compile(entry)
                baseline[key] = count if count is not None else 0
            after, _rc = _compile(entry, candidate.line)
            if after is not None:
                shrink = baseline[key] - after
        status = candidate.probe_status or "untested"
        compiles = status in {"compiles", "improved", "no-change"}
        codegen: str | None = None
        if compiles and (shrink or 0) > 0 and args.check_codegen and entry is not None:
            codegen = _codegen_same(entry, candidate.line, args.compiler)
        rows.append(
            {
                "tu": key,
                "header": str(candidate.header),
                "line": candidate.line,
                "usage": candidate.usage_verdict,
                "closure": len(candidate.exclusive_headers),
                "lines_saved": candidate.estimated_lines_saved,
                "seconds_saved": candidate.estimated_seconds_saved,
                "status": status,
                "truth_compiles": compiles,
                "parse_shrink": shrink,
                "codegen": codegen,
                # 「有用」必须三条都成立：编得过、真少解析了文件、程序没变。
                "useful": bool(compiles and (shrink or 0) > 0 and codegen != "changed"),
                "vacuous": bool(compiles and shrink == 0),
                "silent_change": bool(compiles and (shrink or 0) > 0 and codegen == "changed"),
            }
        )

    by_rank = sorted(
        rows,
        key=lambda r: (
            USAGE_TIER.get(r["usage"], 3),
            -(r["seconds_saved"] or 0.0),
            -(r["lines_saved"] or 0),
            str(r["tu"]),
        ),
    )
    by_lines = sorted(rows, key=lambda r: (-(r["lines_saved"] or 0), str(r["tu"])))
    by_usage = sorted(
        rows,
        key=lambda r: (USAGE_TIER.get(r["usage"], 3), -(r["lines_saved"] or 0), str(r["tu"])),
    )

    matrix: dict[str, dict[str, int]] = {}
    for r in rows:
        bucket = matrix.setdefault(
            r["usage"], {"useful": 0, "vacuous": 0, "silent_change": 0, "rejected": 0}
        )
        if r["useful"]:
            bucket["useful"] += 1
        elif r["vacuous"]:
            bucket["vacuous"] += 1
        elif r.get("silent_change"):
            bucket["silent_change"] += 1
        else:
            bucket["rejected"] += 1

    return {
        "project": str(args.project),
        "compiler": args.compiler,
        "tus": len(scan.ok_tus),
        "check_codegen": bool(args.check_codegen),
        "candidates": len(rows),
        "useful": sum(1 for r in rows if r["useful"]),
        "vacuous": sum(1 for r in rows if r["vacuous"]),
        "rejected": sum(1 for r in rows if not r["truth_compiles"]),
        "silent_change": sum(1 for r in rows if r.get("silent_change")),
        "matrix": matrix,
        "metrics": {
            "rank": _metrics(by_rank),
            "usage": _metrics(by_usage),
            "lines": _metrics(by_lines),
        },
        "rows_by_rank": by_rank,
    }


def _metrics(rows: list[dict]) -> dict:
    total_useful = sum(1 for r in rows if r["useful"])
    return {
        "precision@5": round(precision_at(rows, 5), 3),
        "precision@10": round(precision_at(rows, 10), 3),
        "precision@20": round(precision_at(rows, 20), 3),
        "first_useful": first_hit(rows, "useful"),
        "first_compiles": first_hit(rows, "truth_compiles"),
        "recall_at_10": (
            round(sum(1 for r in rows[:10] if r["useful"]) / total_useful, 3)
            if total_useful
            else 0.0
        ),
    }


def render(report: dict) -> str:
    lines = [
        "# 候选精度基准",
        "",
        f"- 工程：`{report['project']}`",
        f"- 编译器：`{report['compiler']}`",
        f"- 编译单元：{report['tus']}；候选：{report['candidates']}",
        f"- **有用**（编得过且真少解析文件）：**{report['useful']}**；"
        f"空头承诺（编得过但差 0 个文件）：**{report['vacuous']}**；"
        f"编不过：**{report['rejected']}**"
        + (
            f"；静默改程序（编得过、少解析了，但汇编不同）：**{report['silent_change']}**"
            if report.get("check_codegen")
            else ""
        ),
        "",
        "## usage 判定 × 真值",
        "",
        "| usage 判定 | 有用 | 空头承诺 | 静默改程序 | 编不过 | 有用率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for verdict, counts in sorted(report["matrix"].items()):
        total = sum(counts.values())
        rate = counts["useful"] / total if total else 0.0
        lines.append(
            f"| {verdict} | {counts['useful']} | {counts['vacuous']} | "
            f"{counts['silent_change']} | {counts['rejected']} | {rate:.0%} |"
        )
    lines += [
        "",
        "## 排序口径对照（真值 = 有用）",
        "",
        "| 口径 | P@5 | P@10 | P@20 | 首条有用 | 首条能编译 | P@10 覆盖有用项 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, label in (
        ("rank", "当前 rank（usage 分层 + 秒数）"),
        ("usage", "usage 分层 + 行数"),
        ("lines", "纯行数（Header Hero 口径）"),
    ):
        m = report["metrics"][name]
        lines.append(
            f"| {label} | {m['precision@5']:.0%} | {m['precision@10']:.0%} | "
            f"{m['precision@20']:.0%} | {m['first_useful'] or '—'} | "
            f"{m['first_compiles'] or '—'} | {m['recall_at_10']:.0%} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path)
    ap.add_argument("--compiler", default="clang++")
    ap.add_argument("--tus", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--include", dest="includes", action="append", default=[])
    ap.add_argument(
        "--check-codegen",
        action="store_true",
        help="对「编得过且少解析了文件」的候选再比一次汇编指纹（第三条真值）",
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    if args.db is None:
        print("注意：未给 --db，无法测真实解析量，只能给出「能编译」这一维真值")

    report = run(args)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.out.with_suffix(".md").write_text(render(report), encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
