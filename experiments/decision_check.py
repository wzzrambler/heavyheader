"""Does the advice layer actually produce decisions, and how do they compare?

Round 3 of the review asked for one thing: **every removal suggestion must
carry an explicit verdict ("worth deleting or not") with reproducible
measured evidence** -- not a "疑似未使用" that pushes the thinking back onto
the reader.  This experiment answers three questions on the real project
(nlohmann/json's own test suite, 64 units, same machine as every other
experiment here):

1. **覆盖**：探过针的候选里，有多少拿到了明确结论（建议删除/保留/需人工
   判断）？「需人工判断」是否真的带着判断依据，而不是把模糊原样交还？
2. **维度**：实测数据是否真的覆盖多个维度（编译时间 / 编译器内存分配 /
   目标文件体积），而不是只有一行秒数？
3. **竞品**：把每个结论与 clang-include-cleaner 对同一单元的输出交叉
   核对——我们建议删的它怎么说，它建议删的我们怎么说。谁覆盖谁、谁
   证伪谁，都用数据说，不用修辞说。

口径（与 competitor.py 相同，不重复发明）：HeavyHeader 侧的数字全部经过
真实编译；竞品侧只记录它建议什么，不替它验证——那不是它的用法。
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

from build import CXX, build_and_run_one, test_data_dir, write_generated_headers  # noqa: E402
from competitor import run_cleaner  # noqa: E402
from fetch import fetch  # noqa: E402
from run_experiment import compiler_banner  # noqa: E402

from heavyheader.attrib.advice import MAINTENANCE_LEAN_LINES, TIME_NOISE_FLOOR  # noqa: E402
from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.models import AdviceVerdict  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402


def _unit_name(path: Path) -> str:
    return path.stem


def _make_test_runner(root: Path):
    """A ``test_runner`` for verify: compile+link+run the unit's real tests.

    这是「测试证据等级」的落地：``verify`` 证明补丁版还能编译，这里证明
    它的**测试还能过**。两侧（基线/补丁）各跑一次，断言数记在
    ``assertion_log`` 里供报告引用——「编译通过」与「测试通过」是两条
    不同的证据，报告必须分开写。

    任一步失败（编译/链接/运行/无断言摘要）都返回 ``(False, 原因)``，
    由 verify 的协议决定记 failed 还是 error——这里不替它圆场。
    """
    assertion_log: dict[str, tuple[int, int]] = {}

    def runner(source_path: Path) -> tuple[bool, str]:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="hh-decision-test-") as tmp:
            passed, failed, error = build_and_run_one(Path(source_path), Path(tmp), root)
        if error:
            return False, error
        assertion_log[str(source_path)] = (passed, failed)
        return failed == 0 and passed > 0, f"{passed} passed / {failed} failed"

    return runner, assertion_log


def main() -> int:
    manifest = fetch()
    root = Path(manifest["source_dir"])
    work = HERE / "_work"
    gen = write_generated_headers(root, work / "decision", test_data_dir())
    compile_commands = work / "compile_commands.json"
    # 复用 competitor.py 已经写好、且与真实构建逐字节一致的命令库。
    from build import write_compile_commands

    write_compile_commands(root, compile_commands)

    units = sorted(p.stem for p in (root / "tests" / "src").glob("unit-*.cpp"))

    # ---- our side: scan + probe + measure + advice ----------------------
    print(f"[decision] scan + probe + measure on {len(units)} units …", flush=True)
    options = ScanOptions(
        root=root,
        compiler=CXX,
        compile_commands=compile_commands,
        jobs=8,
    )
    started = time.perf_counter()
    scan = scan_project(options, driver=detect_driver(CXX))
    scan_seconds = time.perf_counter() - started
    scan.reproduce_command = (
        f"heavyheader scan {root} --compiler {CXX} "
        f"--compile-commands {compile_commands} --probe"
    )

    probe_started = time.perf_counter()
    test_runner, assertion_log = _make_test_runner(root)
    results = verify_candidates(
        scan,
        detect_driver(CXX),
        options.driver_options(),
        verify=VerifyOptions(
            top=0, runs=3, probe_runs=1, jobs=4, test_runner=test_runner
        ),
    )
    probe_seconds = time.perf_counter() - probe_started
    print(
        f"[decision] {len(results)} candidates probed in {probe_seconds:.0f}s",
        flush=True,
    )

    # ---- per-candidate record -------------------------------------------
    rows = []
    for result in results:
        candidate = result.candidate
        advice = candidate.advice
        rows.append(
            {
                "unit": _unit_name(candidate.tu),
                "header": candidate.header.name,
                "line": candidate.line,
                "status": result.status,
                "verdict": advice.verdict.value if advice else None,
                "verdict_label": advice.verdict_label if advice else None,
                "lean": advice.lean if advice else None,
                "usage_verdict": candidate.usage_verdict,
                "usage_label": candidate.usage_label,
                "exclusive_headers": len(candidate.exclusive_headers),
                "estimated_lines_saved": candidate.estimated_lines_saved,
                "baseline_ms": round((result.baseline_seconds or 0) * 1000),
                "patched_ms": round((result.patched_seconds or 0) * 1000),
                "saved_percent": round(result.percent_saved or 0.0, 1),
                "baseline_memory_kb": result.baseline_memory_kb,
                "patched_memory_kb": result.patched_memory_kb,
                "baseline_object_bytes": result.baseline_object_bytes,
                "patched_object_bytes": result.patched_object_bytes,
                "timing_note": result.timing_note,
                "test_status": result.test_status,
                "test_detail": result.test_detail,
                "message": " ".join(str(result.message).split())[:160],
                "reasons": (
                    [
                        {
                            "aspect": reason.aspect.value,
                            "text": reason.text,
                            "evidence": reason.evidence,
                        }
                        for reason in advice.reasons
                    ]
                    if advice
                    else []
                ),
            }
        )

    # ---- competitor side, on the same units ------------------------------
    print("[decision] clang-include-cleaner on the same units …", flush=True)
    cleaner = []
    for unit in units:
        cleaner.append(
            run_cleaner(
                root, work, root / "tests" / "src" / f"{unit}.cpp"
            )
        )
    cleaner_by_unit = {entry["unit"]: entry for entry in cleaner}
    cleaner_failed = sorted(
        entry["unit"] for entry in cleaner if not entry["ok"]
    )

    # ---- cross-check ------------------------------------------------------
    ours_remove = [r for r in rows if r["verdict"] == AdviceVerdict.REMOVE.value]
    ours_human_remove_lean = [
        r for r in rows if r["verdict"] == AdviceVerdict.HUMAN.value and r["lean"] == "remove"
    ]

    def _cleaner_says(unit: str, header: str) -> str:
        entry = cleaner_by_unit.get(unit)
        if entry is None or not entry["ok"]:
            return "unit-not-analysed"
        for target in entry["removals"]:
            if Path(target).name == header:
                return "remove"
        return "no-removal-suggestion"

    cross = []
    for row in ours_remove + ours_human_remove_lean:
        cross.append(
            {
                "unit": row["unit"],
                "header": row["header"],
                "our_verdict": row["verdict"],
                "our_lean": row["lean"],
                "cleaner": _cleaner_says(row["unit"], row["header"]),
            }
        )

    cleaner_removals = [
        {"unit": entry["unit"], "header": Path(target).name}
        for entry in cleaner
        if entry["ok"]
        for target in entry["removals"]
    ]
    for removal in cleaner_removals:
        match = next(
            (
                r
                for r in rows
                if r["unit"] == removal["unit"] and r["header"] == removal["header"]
            ),
            None,
        )
        removal["our_verdict"] = match["verdict"] if match else "not-a-candidate"

    counts: dict[str, int] = {}
    for row in rows:
        key = row["verdict"] or "none"
        counts[key] = counts.get(key, 0) + 1

    with_memory = sum(
        1
        for r in rows
        if r["baseline_memory_kb"] is not None and r["patched_memory_kb"] is not None
    )
    with_size = sum(
        1
        for r in rows
        if r["baseline_object_bytes"] is not None
        and r["patched_object_bytes"] is not None
    )

    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project": f"{manifest['project']} {manifest['tag']}",
        "compiler": compiler_banner(),
        "noise_floor": TIME_NOISE_FLOOR,
        "maintenance_lean_lines": MAINTENANCE_LEAN_LINES,
        "heavyheader": {
            "units": len(scan.ok_tus),
            "candidates": len(scan.candidates),
            "probed": len(results),
            "verdicts": counts,
            "measured_with_memory": with_memory,
            "measured_with_object_size": with_size,
            "tested": sum(1 for r in rows if r["test_status"]),
            "test_passed": sum(1 for r in rows if r["test_status"] == "passed"),
            "test_failed": sum(1 for r in rows if r["test_status"] == "failed"),
            "assertion_log": {
                _unit_name(Path(k)): list(v) for k, v in assertion_log.items()
            },
            "scan_seconds": round(scan_seconds, 1),
            "probe_seconds": round(probe_seconds, 1),
            "reproduce_command": scan.reproduce_command,
            "rows": rows,
        },
        "competitor": {
            "name": "clang-include-cleaner",
            "units": len(units),
            "units_failed": cleaner_failed,
            "removal_recommendations": len(cleaner_removals),
            "insertion_recommendations": sum(len(e["insertions"]) for e in cleaner),
        },
        "cross_check": {
            "our_delete_leaning": cross,
            "cleaner_removals_vs_ours": cleaner_removals,
        },
        "note": (
            "HeavyHeader 侧每条结论都经过真实编译；竞品侧只记录它建议什么。"
            "两侧口径不同，不做数值对比，只做「同一单元各自找到什么」的对照。"
        ),
    }

    results_dir = HERE / "results"
    stem = f"decision-{time.strftime('%Y%m%d')}"
    (results_dir / f"{stem}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (results_dir / f"{stem}.md").write_text(render(report), encoding="utf-8")
    print(f"[decision] wrote results/{stem}.{{json,md}}")
    return 0


def render(report: dict) -> str:
    ours = report["heavyheader"]
    comp = report["competitor"]
    cross = report["cross_check"]
    rows = ours["rows"]

    verdicts = ours["verdicts"]
    remove_rows = [r for r in rows if r["verdict"] == "remove"]
    human_rows = [r for r in rows if r["verdict"] == "human"]
    keep_rows = [r for r in rows if r["verdict"] == "keep"]
    human_remove = [r for r in human_rows if r["lean"] == "remove"]
    human_keep = [r for r in human_rows if r["lean"] == "keep"]
    human_nolean = [r for r in human_rows if not r["lean"]]

    lines = [
        "# 迭代实测：删除建议的明确结论、取舍依据与可复现数据",
        "",
        f"> 由 `experiments/decision_check.py` 生成。工程：**{report['project']}**　"
        f"编译器：`{report['compiler']}`",
        "",
        "## 本轮要回答的问题",
        "",
        "上一版报告用「疑似未使用」描述每个候选——一个可能性，不是判断。",
        "本轮把每个候选推到一个明确结论上，并回答三个问题：结论覆盖全不全、",
        "实测维度够不够、与竞品各自看到了什么。",
        "",
        "## 实测规模",
        "",
        f"- 工程测试单元 **{ours['units']}** 个，候选 **{ours['candidates']}** 个，"
        f"全部探针实测（逐个真实编译）",
        f"- 扫描 {ours['scan_seconds']:.1f} s + 实测 {ours['probe_seconds']:.1f} s",
        f"- 噪声线 {report['noise_floor'] * 100:.0f}%（实测经验值，非统计置信界）；"
        f"维护倾向阈值 {report['maintenance_lean_lines']:,} 行（策略值，非物理值）",
        "",
        "## 结论覆盖：每个候选都有明确答案",
        "",
        "| 结论 | 数量 | 含义 |",
        "|---|---:|---|",
        f"| 建议删除 | {len(remove_rows)} | 编译通过 **且** 实测超过噪声线 |",
        f"| 保留 | {len(keep_rows)} | 编译器已否决或定位失败，不必再试 |",
        f"| 需人工判断 | {len(human_rows)} | 编译可行但证据不足；"
        f"其中偏删 {len(human_remove)}、偏留 {len(human_keep)}、无倾向 {len(human_nolean)} |",
        "",
        f"**{len(remove_rows) + len(keep_rows) + len(human_rows)}/{len(rows)} "
        "个探过针的候选都有明确结论**，没有一条停留在「疑似」。",
        "",
    ]

    if remove_rows:
        lines += [
            "### 实测有效的改动（含全部三个维度）",
            "",
            "| 单元 | include | 基线 | 移除后 | 节省 | GGC 内存 | 目标文件 | 单元测试 |",
            "|---|---|---:|---:|---:|---|---|---|",
        ]
        for row in remove_rows:
            memory = "—"
            if row["baseline_memory_kb"] is not None and row["patched_memory_kb"] is not None:
                memory = (
                    f"{row['baseline_memory_kb'] / 1024:.0f} kB → "
                    f"{row['patched_memory_kb'] / 1024:.0f} kB"
                )
            size = "—"
            if (
                row["baseline_object_bytes"] is not None
                and row["patched_object_bytes"] is not None
            ):
                delta = row["patched_object_bytes"] - row["baseline_object_bytes"]
                size = f"{row['baseline_object_bytes']:,} B → {row['patched_object_bytes']:,} B（{delta:+,} B）"
            test = {
                "passed": "✅ 通过",
                "failed": "❌ 失败（已降级）",
                "error": "⚠️ 未能取证",
            }.get(row["test_status"], "未跑")
            lines.append(
                f"| `{row['unit']}` | `{row['header']}` | {row['baseline_ms']} ms "
                f"| {row['patched_ms']} ms | **{row['saved_percent']:.1f}%** "
                f"| {memory} | {size} | {test} |"
            )
        lines += [
            "",
            "> GGC 内存为编译器自报分配峰值（非进程 RSS）；目标文件为一次完整编译实测。",
            "> 「—」表示该维度本轮未测或工具链不提供——不是 0。",
            "",
        ]

    if ours["tested"]:
        lines += [
            "### 测试证据：从「编译通过」到「测试通过」",
            "",
            f"- 跑了测试的候选：**{ours['tested']}** 个"
            f"（通过 {ours['test_passed']} / 失败 {ours['test_failed']}）",
            "- 协议：基线版先跑（基线就不过记 error——不冤枉候选，但也不取证），",
            "  基线过了才跑补丁版；补丁挂 → 结论从「建议删除」降级为「需人工判断」。",
        ]
        if ours["assertion_log"]:
            lines.append("- 断言数（真实执行，逐套件口径）：")
            for unit, (passed, failed) in sorted(ours["assertion_log"].items()):
                lines.append(f"  - `{unit}`：{passed:,} passed / {failed:,} failed")
        lines.append("")

    lines += [
        "### 维度覆盖",
        "",
        f"- 编译时间：{ours['probed']} 个候选全部实测（每侧 3 次取最小）；",
        f"- 编译器内存分配（GGC）：{ours['measured_with_memory']} 个候选两侧都有数据；",
        f"- 目标文件体积：{ours['measured_with_object_size']} 个候选两侧都有数据"
        "（只测实测有收益者——为噪声内的候选测体积是浪费预算）；",
        "- 复现命令随报告输出，见 `reproduce_command` 字段。",
        "",
        "## 与 clang-include-cleaner 的交叉核对",
        "",
        f"- 竞品跑了 {comp['units']} 个单元，分析失败 {len(comp['units_failed'])} 个"
        + (f"（{', '.join(comp['units_failed'])}）" if comp["units_failed"] else ""),
        f"- 竞品删除类建议 **{comp['removal_recommendations']}** 条；"
        f"补充类建议 {comp['insertion_recommendations']} 条（它确实在工作，只是在回答另一个问题）",
        "",
    ]

    if cross["our_delete_leaning"]:
        lines += [
            "### 我们倾向删除的，竞品怎么说",
            "",
            "| 单元 | include | 我们的结论 | 竞品对该 include |",
            "|---|---|---|---|",
        ]
        for row in cross["our_delete_leaning"]:
            lines.append(
                f"| `{row['unit']}` | `{row['header']}` "
                f"| {row['our_verdict']}（{row['our_lean'] or '—'}） | {row['cleaner']} |"
            )
        lines.append("")

    if cross["cleaner_removals_vs_ours"]:
        lines += [
            "### 竞品建议删除的，我们怎么说",
            "",
            "| 单元 | include | 我们的结论 |",
            "|---|---|---|",
        ]
        for row in cross["cleaner_removals_vs_ours"]:
            lines.append(
                f"| `{row['unit']}` | `{row['header']}` | {row['our_verdict']} |"
            )
        lines.append("")

    lines += [
        "## 怎么读这份对比（诚实的部分）",
        "",
        f"1. **精度上我们仍然落后。** 竞品用 AST 判断「用没用」，"
        f"{comp['removal_recommendations']} 条删除建议；我们有 {len(remove_rows)} 条"
        f"实测有效——但这是从 {ours['probed']} 个候选里筛出来的，"
        f"其中 {len(keep_rows)} 个编译都过不了。",
        "   「能给明确结论」不等于「结论都对」；本轮的进步在结论形态与证据维度，",
        "   不在命中率。",
        "2. **收益验证上竞品没有对应能力。** 成本量化（时间/内存/体积）与复现命令",
        "   是它输出里不存在的维度，这不是它做得不好，是它回答另一个问题。",
        "3. **两侧失败的单元必须点名。** 竞品分析失败的单元若恰好覆盖我们的有效",
        "   改动，「竞品没发现」就有一部分是「竞品没能看」，上文已如实列出。",
        "",
        "## 下一步迭代建议",
        "",
        f"1. **降候选数比升排序更急。** 当前 {len(keep_rows)}/{ours['probed']} "
        "编译不过，开发者的多次点击里有大比例落在编译不过的行上。"
        "接入 libclang 做语义判定（ROADMAP 既有项）是提高精度的正路，"
        "优先于任何报告层美化。",
        "2. **「需人工判断」要给审查成本口径。** 现在 lean 只有行数阈值，",
        "   下一步应按「独占闭包 × 被引用 TU 数」给每个 HUMAN 候选一个明确的",
        "   预计审查收益（行），让「偏删/偏留」也有量化依据。",
        "3. **把测试执行纳入 REMOVE 的前置。** 当前 REMOVE 只保证编译通过；",
        "   对能定位到测试二进制的候选，追加「跑对应测试」这一证据等级，",
        "   否则「编译通过 ≠ 功能正确」永远只是脚注。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
