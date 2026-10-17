"""Same-task comparison against a real competitor: clang-include-cleaner.

Q2 from the review was "does the advantage over existing tools hold up?".  The
only honest way to answer it is to run a real competitor on the same project,
with the same task, and record what it actually did -- not what its README
says it does.

The competitor here is **clang-include-cleaner** (LLVM 23, shipped in this
machine's LLVM toolchain).  It is the right one to compare against because it
is the gold standard for the *other* half of the question: it uses a real
clang AST to decide which includes a translation unit actually uses.  Where
HeavyHeader is textual and cost-oriented, clang-include-cleaner is semantic
and correctness-oriented.  Both produce "delete this include" advice, so they
can be scored on the same task.

The task, stated once and applied to both sides
----------------------------------------------
"Point at includes in this project's test units that can be deleted, and rank
them so the most worth trying come first."

What is recorded per tool
-------------------------
* what it recommends (add / remove / nothing), per unit;
* how many of its recommendations are *removals* (the only kind that can save
  build time);
* whether each removal survives a real compile (the probe stage, shared with
  ``scan --probe`` so both sides are judged by the same machine);
* wall time spent getting there.

Getting clang to run at all is half the work and is documented in
``--clang-args``: the default target on this box is MSVC, which makes clang
ignore the project's MinGW ``-I`` paths and fail on its own bundled headers.
"""

from __future__ import annotations

import json
import re
import subprocess
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
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402

#: clang-include-cleaner, from the LLVM toolchain that is actually installed.
CLANG_INCLUDE_CLEANER = Path("C:/Program Files/LLVM/bin/clang-include-cleaner.exe")

#: MinGW 16.2.0's libstdc++ headers.  clang must be told to look here or it
#: falls back to its default (MSVC) toolchain and fails on its own headers.
MINGW_GCC_INCLUDE = Path("C:/mingw64/lib/gcc/x86_64-w64-mingw32/16.2.0/include")
MINGW_INCLUDE = Path("C:/mingw64/x86_64-w64-mingw32/include")

#: A removal recommendation: ``- "some/header.h"`` in ``--print=changes`` output.
_REMOVE_RE = re.compile(r'^\s*-\s*"(?P<target>[^"]+)"\s*$')
#: An insertion recommendation: ``+ "some/header.h"``.
_INSERT_RE = re.compile(r'^\s*\+\s*"(?P<target>[^"]+)"\s*$')


def clang_extra_args() -> list[str]:
    """Arguments that make clang accept this project's MinGW build.

    Without them clang-include-cleaner targets MSVC (the host default), cannot
    find ``<string>`` where the project expects it, and dies on its own
    ``__stddef_max_align_t.h``.  This is not a workaround for a competitor
    defect -- it is the same information g++ gets implicitly, stated
    explicitly, which is what any cross-toolchain comparison has to do.
    """
    gcc = MINGW_GCC_INCLUDE
    return [
        "--target=x86_64-w64-windows-gnu",
        "-nostdinc++",
        "-nobuiltininc",
        f"-I{gcc / 'c++'}",
        f"-I{gcc / 'c++' / 'x86_64-w64-mingw32'}",
        f"-I{MINGW_INCLUDE}",
        f"-I{gcc}",
    ]


def run_cleaner(root: Path, build_path: Path, unit: Path) -> dict:
    """Run clang-include-cleaner on one unit and parse its recommendations."""
    started = time.perf_counter()
    proc = subprocess.run(
        [
            str(CLANG_INCLUDE_CLEANER),
            "-p",
            str(build_path),
            "--print=changes",
            *[
                arg
                for extra in clang_extra_args()
                for arg in ("--extra-arg-before", extra)
            ],
            str(unit),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    elapsed = time.perf_counter() - started

    removals: list[str] = []
    insertions: list[str] = []
    for line in proc.stdout.splitlines():
        remove = _REMOVE_RE.match(line)
        if remove:
            removals.append(remove.group("target"))
            continue
        insert = _INSERT_RE.match(line)
        if insert:
            insertions.append(insert.group("target"))

    return {
        "unit": unit.stem,
        "removals": removals,
        "insertions": insertions,
        "seconds": round(elapsed, 3),
        "ok": proc.returncode == 0,
        "stderr_tail": proc.stderr.strip().splitlines()[-1][:200] if proc.returncode else "",
    }


def _unit_path(root: Path, unit: str) -> Path:
    return root / "tests" / "src" / f"{unit}.cpp"


def main() -> int:
    if "--render-only" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--render-only") + 1])
        report = json.loads(path.read_text(encoding="utf-8"))
        path.with_suffix(".md").write_text(render(report), encoding="utf-8")
        print(f"[compare] re-rendered {path.with_suffix('.md').name}")
        return 0

    manifest = fetch()
    root = Path(manifest["source_dir"])
    work = HERE / "_work"
    gen = write_generated_headers(root, work / "control", test_data_dir())
    build_path = work

    units = sorted(p.stem for p in (root / "tests" / "src").glob("unit-*.cpp"))

    print(f"[compare] clang-include-cleaner on {len(units)} units …", flush=True)
    cleaner = []
    for unit in units:
        result = run_cleaner(root, build_path, _unit_path(root, unit))
        cleaner.append(result)
        if result["ok"] and result["removals"]:
            print(
                f"[compare] {unit}: remove {result['removals']}",
                flush=True,
            )

    total_removals = sum(len(r["removals"]) for r in cleaner)
    failed = [r["unit"] for r in cleaner if not r["ok"]]

    # ---- our side, on the same units, through the same probe stage --------
    print("[compare] HeavyHeader scan + probe on the same units …", flush=True)
    options = ScanOptions(
        root=root,
        compiler=CXX,
        compile_commands=work / "compile_commands.json",
        jobs=8,
    )
    started = time.perf_counter()
    scan = scan_project(options, driver=detect_driver(CXX))
    scan_seconds = time.perf_counter() - started

    probe_started = time.perf_counter()
    results = verify_candidates(
        scan,
        detect_driver(CXX),
        options.driver_options(),
        verify=VerifyOptions(top=0, runs=1, probe_runs=1, jobs=4),
    )
    probe_seconds = time.perf_counter() - probe_started

    improved = [
        r
        for r in results
        if r.status == "improved"
    ]
    compiles = [r for r in results if r.status in ("improved", "no-change")]

    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project": f"{manifest['project']} {manifest['tag']}",
        "compiler": compiler_banner(),
        "competitor": {
            "name": "clang-include-cleaner",
            "version": _cleaner_version(),
            "units": len(units),
            "units_failed": failed,
            "removal_recommendations": total_removals,
            "insertion_recommendations": sum(len(r["insertions"]) for r in cleaner),
            "units_with_insertions": sum(1 for r in cleaner if r["insertions"]),
            "units_with_removals": sum(1 for r in cleaner if r["removals"]),
            "wall_seconds": round(sum(r["seconds"] for r in cleaner), 1),
            "per_unit": [
                {
                    "unit": r["unit"],
                    "removals": r["removals"],
                    "insertions": r["insertions"],
                    "ok": r["ok"],
                }
                for r in cleaner
            ],
        },
        "heavyheader": {
            "units": len(scan.ok_tus),
            "candidates": len(scan.candidates),
            "probed": len(results),
            "compiled_after_removal": len(compiles),
            "measured_improved": len(improved),
            "scan_seconds": round(scan_seconds, 1),
            "probe_seconds": round(probe_seconds, 1),
            "improvements": [
                {
                    "unit": r.candidate.tu.stem,
                    "header": r.candidate.header.name,
                    "baseline_ms": round((r.baseline_seconds or 0) * 1000),
                    "patched_ms": round((r.patched_seconds or 0) * 1000),
                    "saved_percent": round(r.percent_saved or 0.0, 1),
                }
                for r in improved
            ],
        },
        "note": (
            "同一工程、同一任务（找出可删除的 include），两侧都用同一台机器判定"
            "「删了之后还能不能编译」。竞品侧只记录它**建议删什么**；"
            "HeavyHeader 侧记录建议 + 实测。两侧的口径差异在报告里写明，"
            "不做跨口径的数值比较。"
        ),
    }

    results_dir = HERE / "results"
    stem = f"competitor-{time.strftime('%Y%m%d')}"
    (results_dir / f"{stem}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (results_dir / f"{stem}.md").write_text(render(report), encoding="utf-8")
    print(f"[compare] wrote results/{stem}.{{json,md}}")
    return 0


def _cleaner_version() -> str:
    try:
        proc = subprocess.run(
            [str(CLANG_INCLUDE_CLEANER), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
        # 第一行是 "LLVM (http://llvm.org/):"，第二行才是版本与 target。
        return lines[1][:120] if len(lines) > 1 else (lines[0][:120] if lines else "unknown")
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"


def render(report: dict) -> str:
    comp = report["competitor"]
    ours = report["heavyheader"]

    per_unit = comp.get("per_unit", [])
    insertions = sum(len(r.get("insertions", [])) for r in per_unit)
    units_with_insertions = sum(1 for r in per_unit if r.get("insertions"))

    lines = [
        "# 同类竞品真实对比：clang-include-cleaner vs HeavyHeader",
        "",
        f"> 由 `experiments/competitor.py` 生成。工程：**{report['project']}**　"
        f"编译器：`{report['compiler']}`",
        "",
        "## 为什么是这一个竞品",
        "",
        "`clang-include-cleaner` 是回答「这个 include 还用不用」这件事的业内标准做法，"
        "用的是真 clang AST，不是文本匹配。它和本工具正好覆盖同一件事的两半："
        "它管**正确性**（该包含什么），我们管**成本**（每个 include 让你多等几秒）。"
        "所以把它拉到同一工程、同一任务上跑，比跟任何「听起来类似」的工具对比都有意义。",
        "",
        "## 任务（两侧一致）",
        "",
        "在工程的测试单元里找出**可以删除**的 include。删除是唯一可能省下构建时间的"
        "改动，所以只统计删除类建议。",
        "",
        "## 竞品侧：它建议删什么",
        "",
        f"- 版本：`{comp['version']}`",
        f"- 跑了 {comp['units']} 个单元，失败 {len(comp['units_failed'])} 个"
        + (f"（{', '.join(comp['units_failed'][:5])}）" if comp["units_failed"] else ""),
        f"- **删除类建议合计 {comp['removal_recommendations']} 条**，"
        f"分布在 {comp['units_with_removals']} 个单元",
        f"- 补充类建议 {insertions} 条，分布在 {units_with_insertions} 个单元"
        "（这说明它**在工作**，只是在回答另一个问题）",
        f"- 竞品侧墙钟时间：{comp['wall_seconds']:.1f} s（只分析，不编译验证）",
        "",
    ]

    failed = set(comp["units_failed"])
    if failed:
        lines += [
            "> **必须说清的一点**：竞品分析失败的单元是 "
            + "、".join(f"`{u}`" for u in sorted(failed))
            + "。如果本工具的有效改动恰好落在这个单元里，那「竞品 0 条删除建议」"
            "是**在少了这一个单元的前提下**成立的，不是一次完整的覆盖。"
            "本节末尾会把它和我们的实测结果对照着写。",
            "",
        ]

    with_removals = [r for r in comp["per_unit"] if r["removals"]]
    if with_removals:
        lines += [
            "| 单元 | 建议删除 | 建议补充 |",
            "|---|---|---|",
        ]
        for row in with_removals[:20]:
            lines.append(
                f"| `{row['unit']}` | {', '.join(f'`{x}`' for x in row['removals'])} | "
                f"{', '.join(f'`{x}`' for x in row['insertions']) or '—'} |"
            )
        lines.append("")

    lines += [
        "## HeavyHeader 侧：建议 + 实测",
        "",
        f"- 候选 **{ours['candidates']}** 个，全部探针实测（逐个真的编译）",
        f"- 删除后仍能编译：**{ours['compiled_after_removal']}** 个",
        f"- 其中实测变快（超过 2% 噪声线）：**{ours['measured_improved']}** 个",
        f"- 扫描 {ours['scan_seconds']:.1f} s + 探针 {ours['probe_seconds']:.1f} s "
        f"= **{ours['scan_seconds'] + ours['probe_seconds']:.1f} s**",
        "",
    ]

    if ours["improvements"]:
        lines += [
            "| 单元 | include | 基线 | 移除后 | 节省 |",
            "|---|---|---:|---:|---:|",
        ]
        for row in ours["improvements"]:
            lines.append(
                f"| `{row['unit']}` | `{row['header']}` | {row['baseline_ms']} ms "
                f"| {row['patched_ms']} ms | **{row['saved_percent']:.1f}%** |"
            )
        lines.append("")

    overlap = sorted(failed & {row["unit"] for row in ours["improvements"]})
    if overlap:
        lines += [
            "### 一个必须对齐的事实",
            "",
            "本工具实测到的那个有效改动，落在 "
            + "、".join(f"`{u}`" for u in overlap)
            + " ——**正是竞品没能分析的那个单元**。所以严格地说，这次对比里"
            "「竞品没有发现它」有一部分是「竞品没能看这个单元」，"
            "不全是「竞品不关心成本」。这两件事都要写下来，"
            "只写对自己有利的那一半就是挑选数据。",
            "",
        ]

    lines += [
        "## 怎么读这张表（口径差异必须先说清）",
        "",
        "**竞品侧的数字是「建议条数」，不是「验证过的条数」。** clang-include-cleaner "
        "给出的是基于 AST 的判断，本报告没有把它的每条建议都编译验证——那不是它的用法，"
        "把它没承诺的东西算成它的缺陷同样不公平。",
        "",
        "**HeavyHeader 侧的数字是「实测条数」。** 每一条都经过真的编译，附基线秒数。",
        "",
        "所以两侧可比的只有一件事：**在同一工程、同一任务上，各自找到了什么。** "
        "条数不可比，因为一边是「它认为可以删」，一边是「我们试过并且真的更快」。",
        "",
        "## 两侧各自回答不了的问题",
        "",
        f"- 竞品找到了 **{insertions} 条我们给不出**"
        " 的补充建议（`nlohmann/json_fwd.hpp` 之类）——那是 AST 级别的"
        "「你该显式包含它」，我们的文本启发式做不到，也不假装能做到；",
        "- 竞品回答不了「这个 include 让我多等几秒」——它的输出里没有成本维度；",
        "- 我们回答不了「这行 include 是不是**应该**在那里」——文本启发式给不出"
        " AST 级别的确定性，这也是 `ROADMAP.md` 里 libclang 后端仍是可选项的原因；",
        "- 两者是互补关系，不是替代关系。把竞品输出当成本输入、把我们的成本排序"
        "当竞品的优先级，是更现实的组合用法。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
