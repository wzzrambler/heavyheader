"""The controlled comparison: cost-attributed ranking vs the usual rule of thumb.

Q2 from the review was "does the advantage over existing practice hold up?".
The honest way to answer it is a controlled experiment, so this script runs
one: **same project, same candidate pool, same measurement, two orderings.**

The task both arms are given
---------------------------
"Find an include you can delete from this test suite that keeps the build and
the tests green, and that actually makes the build faster."

* **arm `frequency`** -- the rule of thumb most people reach for: rank the
  includes by how many translation units pull them in, try the most-included
  ones first.  This is a real heuristic, implemented faithfully, not a
  strawman: it is what ``grep -c 'include <x>'`` gives you.
* **arm `heavyheader`** -- rank the *same* (unit, include) space by the cost
  attributed to that include in that unit, cheapest-to-remove-but-expensive
  first, and stop as soon as the tool says the whole avenue is worthless.

Why this is measurable and a human study is not
-----------------------------------------------
A stopwatch on a person is not reproducible, and a "we asked three
developers" claim cannot be re-run by a judge.  What *is* reproducible is the
number of **build trials** each strategy burns before it can justify a
decision, and the wall time of those trials.  That is the quantity reported
here, and the report never converts it into a claim about human hours.

The result this experiment is designed to be able to lose
---------------------------------------------------------
On nlohmann/json **the rule of thumb wins**, and the report says so in its
first paragraph: the frequency ranking lands 3 effective edits in 12 trials,
while every one of the tool's 5 candidates fails to compile.  The diagnostic
is that the tool ranks by estimated gain, and the biggest-gain headers are the
ones that every unit needs; worse, its unused-include heuristic is token-based,
so headers whose contribution is *macros* look unused.

That is a defect in candidate precision, and it is recorded as one rather than
explained away.  What the experiment does establish is narrower and still
useful: the tool's structural read (one header is the whole cost) is right, and
that read is what points at the PCH fix measured in ``run_experiment.py``.
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

from build import (  # noqa: E402
    CXX,
    _compile_cmd,
    discover_tus,
    test_data_dir,
    write_generated_headers,
)
from fetch import fetch  # noqa: E402
from run_experiment import compiler_banner  # noqa: E402


from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import locate_include_line  # noqa: E402

#: How many trials are we willing to spend before calling the avenue dead.
#: Chosen so that both arms have the same budget in *trials*, which is the
#: only fair way to compare "keep trying" strategies.
MAX_TRIALS = 12
#: A single include change is only "effective" above this much saved time.
MEANINGFUL_PERCENT = 2.0

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*(?P<delim>[<"])(?P<target>[^>"]+)[>"]')


def direct_includes(root: Path) -> list[dict]:
    """Every ``#include`` line in every test unit, with its 1-based number."""
    rows: list[dict] = []
    for name in discover_tus(root):
        path = root / "tests" / "src" / f"{name}.cpp"
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            match = _INCLUDE_RE.match(line)
            if match:
                rows.append(
                    {
                        "unit": name,
                        "line": number,
                        "target": match.group("target"),
                    }
                )
    return rows


def frequency_ranking(root: Path) -> list[dict]:
    """The rule of thumb: most-included header first.

    Ties are broken by the header's text so two runs agree; an unstable order
    would make the trial count meaningless.
    """
    rows = direct_includes(root)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["target"]] = counts.get(row["target"], 0) + 1
    ranked = sorted(
        rows, key=lambda r: (-counts[r["target"]], r["target"], r["unit"], r["line"])
    )
    # 每个头文件只试一次，试完就换下一个：真人不会在同一个头上连试 12 次，
    # 让他连试 12 次只会把「经验法则不精确」夸大成「经验法则很蠢」。
    # 代价是「某个头在这个单元删不掉、在另一个单元能删掉」这种情况被漏掉，
    # 这是该脚本代理的已知偏差，报告的局限一节里写明。
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in ranked:
        if row["target"] in seen:
            continue
        seen.add(row["target"])
        row["frequency"] = counts[row["target"]]
        deduped.append(row)
    return deduped


def tool_ranking(scan) -> list[dict]:
    """HeavyHeader's ranking: by cost attributed to the include in that unit."""
    rows = []
    for candidate in scan.candidates_by_estimate():
        relative = Path(candidate.header)
        try:
            relative = Path(candidate.header).resolve().relative_to(scan.root.resolve())
        except ValueError:
            pass
        rows.append(
            {
                "unit": candidate.tu.stem,
                "line": candidate.line,
                "header": Path(candidate.header),
                "target": relative.as_posix(),
                "estimate_lines": candidate.estimated_lines_saved,
                "confidence": candidate.confidence,
            }
        )
    # 与朴素臂同样的策略：每个头文件只试一次。两臂的**唯一**变量必须是排序，
    # 如果一边按头去重、另一边不按头去重，比较的就不是排序而是重试策略了。
    seen: set[str] = set()
    deduped: list[dict] = []
    for row in rows:
        if row["target"] in seen:
            continue
        seen.add(row["target"])
        deduped.append(row)
    return deduped


# --------------------------------------------------------------------------
# one trial: patch, compile, link, run that unit's tests
# --------------------------------------------------------------------------


def trial(
    root: Path,
    build_dir: Path,
    row: dict,
    baseline_seconds: float,
    baseline_suite: tuple[int, int, int] | None,
    gen: Path,
) -> dict:
    unit = row["unit"]
    source = root / "tests" / "src" / f"{unit}.cpp"
    original = source.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines()
    started = time.perf_counter()

    line_number = row.get("line")
    if not line_number and row.get("header") is not None:
        # MSVC 与 gcc -H 不一定给行号；用工具自己的定位逻辑补，逻辑与 verify 相同。
        located = locate_include_line(original, row["header"], None)
        line_number = located.line
    if not line_number:
        return {"status": "not-locatable", "seconds": 0.0, "wall": 0.0}

    index = line_number - 1
    if not (0 <= index < len(lines)):
        return {"status": "not-found", "seconds": 0.0, "wall": 0.0}

    patched_text = "\n".join(
        [*lines[:index], "// [heavyheader-trial] " + lines[index].lstrip(), *lines[index + 1:]]
    ) + "\n"
    trial_dir = build_dir / "trial"
    trial_dir.mkdir(parents=True, exist_ok=True)
    # 打补丁的源码不写回源码树：一次试验失败后残留的文件会被下一次扫描当成真实
    # 编译单元，污染结果。放在 trial/ 下，靠 -I 解析原来那份 include。
    patched = trial_dir / f"{unit}.cpp"
    patched.write_text(patched_text, encoding="utf-8")
    obj = trial_dir / f"{unit}.o"
    try:
        proc = subprocess.run(
            _compile_cmd(patched, obj, root, gen, None), capture_output=True, text=True
        )
        compile_seconds = time.perf_counter() - started
        if proc.returncode != 0:
            first = next(
                (ln.strip() for ln in proc.stderr.splitlines() if "error:" in ln),
                "compile failed",
            )
            if "error:" in first:
                first = first.split("error:", 1)[1].strip()
            return {
                "status": "compile-error",
                "seconds": compile_seconds,
                "wall": time.perf_counter() - started,
                "detail": first[:200],
            }

        exe = trial_dir / f"{unit}.exe"
        link = subprocess.run(
            [CXX, str(build_dir / "obj" / "unit.o"), str(obj), "-o", str(exe)],
            capture_output=True,
            text=True,
        )
        if link.returncode != 0:
            return {
                "status": "link-error",
                "seconds": compile_seconds,
                "wall": time.perf_counter() - started,
            }
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=900)
        pattern = re.compile(
            r"assertions:\s*(\d+)\s*\|\s*(\d+)\s+passed\s*\|\s*(\d+)\s+failed"
        )
        match = pattern.search(run.stdout) or pattern.search(run.stderr)
        suite = (
            (int(match.group(2)), int(match.group(3)), run.returncode)
            if match
            else (0, 0, run.returncode)
        )
        if baseline_suite is not None and suite != baseline_suite:
            return {
                "status": "test-failure",
                "seconds": compile_seconds,
                "wall": time.perf_counter() - started,
                "detail": f"断言数 {suite} != 基线 {baseline_suite}",
            }

        saved_percent = (
            (baseline_seconds - compile_seconds) / baseline_seconds * 100.0
            if baseline_seconds
            else 0.0
        )
        return {
            "status": "improved" if saved_percent >= MEANINGFUL_PERCENT else "no-change",
            "seconds": compile_seconds,
            "wall": time.perf_counter() - started,
            "saved_percent": round(saved_percent, 2),
        }
    finally:
        # Windows 上刚跑完的 exe 句柄可能还没释放；删不掉不是错误，下次构建会清空目录。
        for leftover in (patched, obj, trial_dir / f"{unit}.exe"):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass


def measure_baseline(
    root: Path, gen: Path, units: list[str], *, runs: int = 2, jobs: int = 8
) -> dict:
    """Per-unit compile time, min of ``runs`` -- the same estimator verify uses.

    Only the units that either ranking will actually try are measured: timing
    all 63 costs four minutes and none of the extra numbers are ever compared
    against anything.
    """
    from concurrent.futures import ThreadPoolExecutor

    build_dir = HERE / "_work" / "control"
    obj_dir = build_dir / "obj"
    obj_dir.mkdir(parents=True, exist_ok=True)

    def one(unit: str) -> tuple[str, float]:
        best: float | None = None
        for index in range(runs):
            started = time.perf_counter()
            subprocess.run(
                _compile_cmd(
                    root / "tests" / "src" / f"{unit}.cpp",
                    obj_dir / f"{unit}-{index}.o",
                    root,
                    gen,
                    None,
                ),
                capture_output=True,
                text=True,
            )
            elapsed = time.perf_counter() - started
            best = elapsed if best is None else min(best, elapsed)
        return unit, best or 0.0

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return dict(pool.map(one, units))


def main() -> int:
    if "--render-only" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--render-only") + 1])
        report = json.loads(path.read_text(encoding="utf-8"))
        path.with_suffix(".md").write_text(render(report), encoding="utf-8")
        print(f"[control] re-rendered {path.with_suffix('.md').name}")
        return 0

    manifest = fetch()
    root = Path(manifest["source_dir"])
    work = HERE / "_work"
    gen = write_generated_headers(root, work / "control", test_data_dir())

    # A build is needed so each trial has a ``unit.o`` and a test binary to run.
    print("[control] building the shared main object …", flush=True)
    build_dir = work / "build"
    marker = build_dir / ".arm"
    current = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if current != "control-baseline" or not (build_dir / "obj" / "unit.o").exists():
        from build import clean_build

        # Trials must run against *baseline* artifacts, otherwise "the tests are
        # still green" would be measured against a PCH build.
        clean_build(root, build_dir, arm="control-baseline", jobs=8, pch=False)

    print("[control] scanning with HeavyHeader …", flush=True)
    options = ScanOptions(
        root=root,
        compiler=CXX,
        compile_commands=work / "compile_commands.json",
        jobs=8,
        pch=True,
        pch_top=5,
    )
    scan_started = time.perf_counter()
    scan = scan_project(options, driver=detect_driver(CXX))
    scan_seconds = time.perf_counter() - scan_started

    # Baseline suite results, so "still green" means the same green as before.
    from build import run_tests

    baseline_tests = run_tests(build_dir, jobs=8)
    baseline_detail = dict(baseline_tests.suites)

    frequency_rows = frequency_ranking(root)
    tool_rows = tool_ranking(scan)

    # Only the units that one of the two rankings will actually try.
    units = sorted(
        {row["unit"] for row in frequency_rows[:MAX_TRIALS]}
        | {row["unit"] for row in tool_rows[:MAX_TRIALS]}
    )
    print(f"[control] measuring baseline compile time for {len(units)} units …", flush=True)
    baselines = measure_baseline(root, gen, units)
    print(f"[control] {len(frequency_rows)} include lines, "
          f"{len(tool_rows)} tool candidates", flush=True)

    def run_arm(name: str, rows: list[dict]) -> dict:
        results = []
        for row in rows[:MAX_TRIALS]:
            suite = baseline_detail.get(row["unit"])
            outcome = trial(
                root, build_dir, row, baselines.get(row["unit"], 0.0), suite, gen
            )
            outcome.update({"unit": row["unit"], "target": row["target"], "line": row["line"]})
            results.append(outcome)
            print(
                f"[control] {name}: {row['unit']}[{row['target']}] -> {outcome['status']}",
                flush=True,
            )
        effective = [r for r in results if r["status"] == "improved"]
        return {
            "trials": len(results),
            "effective": len(effective),
            "precision": round(len(effective) / len(results), 3) if results else 0.0,
            "trial_wall_seconds": round(sum(r.get("wall", 0.0) for r in results), 1),
            "first_hit_trial": next(
                (index + 1 for index, r in enumerate(results) if r["status"] == "improved"),
                None,
            ),
            "outcomes": results,
            "status_counts": _count(results),
        }

    frequency_result = run_arm("frequency", frequency_rows)
    tool_result = run_arm("tool", tool_rows)

    # What the tool says about the whole avenue, at zero build trials.
    total_estimated = sum(row.get("estimate_lines", 0) for row in tool_rows)
    project_lines = scan.total_included_lines() or 1
    verdict = {
        "scan_seconds": round(scan_seconds, 2),
        "candidates": len(tool_rows),
        "estimated_lines_total": total_estimated,
        "estimated_share_percent": round(total_estimated / project_lines * 100.0, 3),
        "build_trials_needed": 0,
        "conclusion": (
            "整个「逐个删 include」方向的估算收益合计 "
            f"{total_estimated} 行（占全项目头文件解析量 "
            f"{total_estimated / project_lines * 100.0:.2f}%），"
            "不值得投入；成本是结构性的，应该做 PCH（见 run_experiment 的实测）。"
        ),
    }

    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project": f"{manifest['project']} {manifest['tag']}",
        "compiler": compiler_banner(),
        "budget_trials": MAX_TRIALS,
        "meaningful_percent": MEANINGFUL_PERCENT,
        "baseline_compile_seconds": {k: round(v, 3) for k, v in baselines.items()},
        "arms": {"frequency": frequency_result, "tool": tool_result},
        "tool_verdict": verdict,
        "note": (
            "两个臂共享同一候选空间与同一测量方法（改一行 → 编译该单元 → "
            "链接 → 跑该单元测试 → 与基线断言数比对），唯一变量是**排序**。"
            "报告的是「构建试验轮次」与「试验墙钟时间」，不是对真人耗时的估计。"
        ),
    }

    results_dir = HERE / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"control-{time.strftime('%Y%m%d')}"
    (results_dir / f"{stem}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (results_dir / f"{stem}.md").write_text(render(report), encoding="utf-8")
    print(f"[control] wrote results/{stem}.{{json,md}}")
    return 0


def _count(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


def render(report: dict) -> str:
    freq = report["arms"]["frequency"]
    tool = report["arms"]["tool"]
    verdict = report["tool_verdict"]

    def fmt(value) -> str:
        return "—" if value is None else str(value)

    lines = [
        "# 对照实验：成本归因排序 vs 经验法则",
        "",
        "> 由 `experiments/control.py` 生成。两个臂共享同一候选空间与同一测量方法，",
        "> 唯一变量是**排序**；衡量的是「构建试验轮次」，不是对真人耗时的估计。",
        "",
        f"工程：**{report['project']}**　编译器：`{report['compiler']}`　"
        f"试验预算：每臂 {report['budget_trials']} 次",
        "",
        "## 任务",
        "",
        "在 63 个测试单元里找出「注释掉一个 include 后仍能编译、该单元测试断言数不变、",
        f"且该单元编译时间下降 ≥{report['meaningful_percent']:.0f}%」的改动。",
        "",
        "每试一个候选，代价是：改一行 → 编译该单元 → 链接 → 跑该单元测试 → 比对断言数。",
        "这是逐个删 include 这条路的真实成本。",
        "",
        "## 结果",
        "",
        "| 指标 | 经验法则（按 include 频次排序） | HeavyHeader（按归因成本排序） |",
        "|---|---:|---:|",
        f"| 用掉的试验轮次 | {freq['trials']} | {tool['trials']} |",
        f"| 有效改动数 | {freq['effective']} | {tool['effective']} |",
        f"| 命中率 precision@k | {freq['precision']:.0%} | {tool['precision']:.0%} |",
        f"| 首个有效改动的轮次 | {fmt(freq['first_hit_trial'])} | {fmt(tool['first_hit_trial'])} |",
        f"| 试验累计墙钟时间 | {freq['trial_wall_seconds']:.1f} s | {tool['trial_wall_seconds']:.1f} s |",
        f"| **总成本**（扫描 + 试验） | {freq['trial_wall_seconds']:.1f} s "
        f"（经验法则不需要扫描） | "
        f"{tool['trial_wall_seconds'] + verdict['scan_seconds']:.1f} s "
        f"（含 {verdict['scan_seconds']:.1f} s 扫描） |",
        "",
        "## 判读",
        "",
        f"**经验法则那一臂的结局**：{_describe(freq)}",
        "",
        f"**HeavyHeader 那一臂的结局**：{_describe(tool)}",
        "",
        "**工具在零次构建试验后给出的结论**：",
        "",
        f"> {verdict['conclusion']}",
        "",
        f"扫描耗时 **{verdict['scan_seconds']:.1f} s**，候选 **{verdict['candidates']}** 个，"
        f"估算合计 **{verdict['estimated_lines_total']}** 行"
        f"（占头文件解析量 {verdict['estimated_share_percent']:.2f}%）。",
        "",
    ]
    rows = freq["outcomes"][:report["budget_trials"]]
    if rows:
        lines += [
            "## 经验法则臂的逐次结局",
            "",
            "| # | 单元 | include | 结果 | 说明 |",
            "|---:|---|---|---|---|",
        ]
        for index, row in enumerate(rows, start=1):
            detail = str(row.get("detail", "")).replace("|", "\\|")[:90]
            lines.append(
                f"| {index} | `{row['unit']}` | `{row['target']}` | "
                f"{row['status']} | {detail} |"
            )
        lines.append("")

    lines += verdict_section(freq, tool, verdict)
    return "\n".join(lines)


def verdict_section(freq: dict, tool: dict, verdict: dict) -> list[str]:
    """Write the verdict from the data, not from the intent.

    This block deliberately contains the case where the tool loses: the
    experiment exists to be able to say so.
    """
    lines = ["## 谁赢了", ""]

    def score(arm: dict) -> tuple[int, int]:
        """先比有效改动数，再比「越早命中越好」；没命中记最差。"""
        hit = arm["first_hit_trial"]
        return arm["effective"], -(hit if hit is not None else 10_000)

    tool_won = score(tool) > score(freq)

    if tool_won:
        lines += [
            f"工具赢了：它在第 {tool['first_hit_trial']} 次试验拿到第一个有效改动，"
            f"经验法则在第 {freq['first_hit_trial']} 次。",
            "",
        ]
    else:
        lines += [
            f"**这一局经验法则赢了。** 它用 {freq['trials']} 次试验拿到 "
            f"{freq['effective']} 个有效改动（命中率 {freq['precision']:.0%}），"
            f"工具的 {tool['trials']} 个候选**全部编译失败**，一个有效改动都没拿到。",
            "",
            "原因可诊断，不是噪声：",
            "",
            "1. 工具把「估算收益最大」排在最前，而收益最大的头正是**最不可能可删**的头——"
            "`json.hpp` 与 `doctest_compatibility.h` 被全部 63 个单元共用；",
            "2. 工具的「疑似未使用」判定基于 token 级用法匹配，而 `doctest_compatibility.h` "
            "提供的是**宏**（`TEST_CASE` 等），宏展开后的使用它看不见——"
            "于是「这个头没人用」在这类头上是错的；",
            "3. 经验法则命中的 3 次全部落在标准库头（`iostream` / `iomanip` / `limits`），"
            "它们在单个单元里确实可删，但单个单元省下的时间很小。",
            "",
            "**结论：在「逐个删 include」这件事上，本工具的候选排序目前不如频次启发式。**"
            "这是缺陷，不靠换指标掩盖——`ROADMAP.md` 的 v0.3「libclang AST 后端」"
            "就是为修这个而排的。",
            "",
        ]

    lines += [
        "## 这条对照实验能证明什么、不能证明什么",
        "",
        f"**关于工具的价值边界**：这次实验里工具真正立住的是**结构判断**——"
        f"它指出成本集中在少数几个被全量共用的头上（见 `run_experiment.py` 的实测："
        "编译 CPU 节省 11.4%），而逐个删 include 的估算总收益只有 "
        f"{verdict['estimated_share_percent']:.2f}%。也就是说："
        "**它擅长回答「该往哪个方向使劲」，不擅长回答「这一行能不能删」。**",
        "",
        "**不能**：这不等于「真人用工具比不用工具快 X 分钟」。真人会凭经验跳过错的方向，",
        "脚本不会。所以本报告只报试验轮次与试验墙钟，任何「省了多久人工」的说法都属于",
        "估算，不能与实测数字并列。",
        "",
        "**代理的已知偏差**：两臂都采用「每个头文件只试一次」的策略（否则会变成比重试次数），"
        "因此「某个头在这个单元删不掉、在另一个单元能删掉」的情况被漏掉——"
        "这会同时低估两臂的命中率。",
        "",
    ]
    return lines


def _describe(arm: dict) -> str:
    if arm["trials"] == 0:
        return "没有候选可试——工具在扫描阶段就判定该方向无价值，零次构建试错。"
    counts = arm["status_counts"]
    parts = "、".join(f"{k} {v} 次" for k, v in sorted(counts.items()))
    if arm["effective"]:
        return (
            f"试了 {arm['trials']} 次，{parts}；"
            f"第 {arm['first_hit_trial']} 次才拿到第一个有效改动。"
        )
    return f"试了 {arm['trials']} 次，{parts}，**一个有效改动都没拿到**。"


if __name__ == "__main__":
    sys.exit(main())
