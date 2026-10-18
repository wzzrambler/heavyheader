"""G2：优化闭环是否成立——候选可采纳率、朴素基线对照、A/A 伪阳性。

G2 要回答的问题
---------------
「HeavyHeader 推荐的候选，有多少是**真能删且真省时**的？」如果大部分候选删了
编译不过、或者过了也省不出噪声以上的时间，那么「可验证优化」这个定位就不成立，
无论报告做得多漂亮。

三条臂，同一批编译单元、同一个 clang、同一套测量协议（每侧 3 次取最小、墙钟）：

1. **tool**：`candidates_by_estimate()` 的前 K 个，也就是用户跑
   `heavyheader verify --top K` 会拿到的列表。
2. **random**：从同一批 TU 的所有 `#include` 里均匀随机抽 K 条，模拟「不看分析、
   随手挑一条试着删」。这是朴素基线——如果两者命中率相同，工具没有价值。
3. **heuristic**：启发式标为「疑似未使用」的那几条。它回答的是「结构推断本身
   准不准」，与排序无关。

另加 **A/A**：对若干单元做两次独立的 min-of-3 测量，两次之间**不改任何东西**。
delta 超过 `TIME_NOISE_FLOOR` 就说明判定阈值会把噪声当收益——那是比命中率更
致命的问题，因为所有结论都建立在它上面。

判读口径
--------
- 采纳（adopted）= 编译通过 **且** 省时超过噪声线。二者缺一不可。
- 只报计数与比例，不做显著性检验：样本只有几十条，检验会给出虚假的确定感。
"""

from __future__ import annotations

import json
import random
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.advice import TIME_NOISE_FLOOR  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import (  # noqa: E402
    CompilerFamily,
    HeaderKind,
    ProjectScan,
    RemovalCandidate,
)
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, _measure, verify_candidates  # noqa: E402

PROJECT = ROOT / "experiments" / "_work" / "json"
DB = ROOT / "experiments" / "_work" / "compile_commands.json"
CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]

TUS = 25
TOOL_TOP = 10
RANDOM_N = 10
RUNS = 3
SEED = 20261018
AA_UNITS = 5
#: 临界候选重复测几次。3 次足够看出「结论是否会翻面」，再多只是加时间。
BORDERLINE_REPEATS = 3

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*(?P<delim>[<"])(?P<target>[^>"]+)[>"]')


def clone_candidate(candidate: RemovalCandidate) -> RemovalCandidate:
    """复制一个候选，丢掉上一轮的探针/测量状态。

    verify 会把 ``probe_status``、``verified``、``measured_seconds_saved``
    写回候选对象；不复制就重跑，第二次会带着第一次的结论，测出来的「复现」
    其实是缓存。
    """
    return RemovalCandidate(
        tu=candidate.tu,
        header=candidate.header,
        line=candidate.line,
        kind=candidate.kind,
        exclusive_headers=list(candidate.exclusive_headers),
        estimated_lines_saved=candidate.estimated_lines_saved,
        estimated_percent=candidate.estimated_percent,
        usage_label=candidate.usage_label,
        usage_verdict=candidate.usage_verdict,
        confidence=candidate.confidence,
    )


def build_scan() -> ProjectScan:
    driver = detect_driver(CLANG)
    options = ScanOptions(
        root=PROJECT,
        compiler=CLANG,
        compile_commands=DB,
        max_tus=TUS,
        jobs=1,
        extra_flags=list(TARGET_FLAGS),
    )
    return scan_project(options, driver=driver)


def include_sites(scan: ProjectScan) -> list[tuple[Path, str]]:
    """每个 TU 里的每一条 ``#include``，作为随机臂的抽取池。"""
    sites: list[tuple[Path, str]] = []
    for tu in scan.ok_tus:
        try:
            text = tu.source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = _INCLUDE_RE.match(line)
            if match:
                sites.append((tu.source, match.group("target")))
    return sites


def random_candidates(scan: ProjectScan, count: int, rng: random.Random) -> list[RemovalCandidate]:
    """把随机抽到的 include 位置包装成候选，交给同一套 verify 流程。

    ``header`` 只填 include 文本本身：verify 的弱匹配按文件名比对，
    足以定位到那一行；这里不需要（也不应该）假装自己解析过预处理。
    """
    sites = include_sites(scan)
    picked = rng.sample(sites, min(count, len(sites)))
    out: list[RemovalCandidate] = []
    for source, target in picked:
        out.append(
            RemovalCandidate(
                tu=source,
                header=Path(target),
                line=None,
                kind=HeaderKind.PROJECT if "/" not in target else HeaderKind.PROJECT,
            )
        )
    return out


def run_arm(
    scan: ProjectScan,
    driver,
    candidates: list[RemovalCandidate],
    label: str,
    confirm: int = 1,
):
    """在同一份扫描上验证一组候选，返回结果列表。

    ``confirm`` 控制复测遍数：1 = 旧协议（单遍定论），2 = 新协议（improved
    必须再独立测一遍）。两种都跑，才能看出协议改动对结论的影响。
    """
    saved = scan.candidates
    scan.candidates = list(candidates)
    try:
        fallback = DriverOptions(
            standard="c++17",
            include_dirs=[PROJECT / "include", PROJECT / "tests" / "thirdparty" / "doctest"],
            extra_flags=list(TARGET_FLAGS),
        )
        return verify_candidates(
            scan,
            driver,
            fallback,
            verify=VerifyOptions(
                top=len(candidates), runs=RUNS, jobs=1, confirm_passes=confirm
            ),
        )
    finally:
        scan.candidates = saved


def summarize(results, label: str) -> dict:
    total = len(results)
    compiled = [r for r in results if r.status not in ("compile-error", "error", "not-found", "ambiguous")]
    improved = [r for r in results if r.status == "improved"]
    return {
        "arm": label,
        "candidates": total,
        "compiled": len(compiled),
        "improved": len(improved),
        "adopt_rate": round(len(improved) / total, 3) if total else 0.0,
        "details": [
            {
                "tu": Path(r.candidate.tu).name,
                "header": Path(r.candidate.header).name,
                "status": r.status,
                "baseline_ms": None if r.baseline_seconds is None else round(r.baseline_seconds * 1000, 1),
                "patched_ms": None if r.patched_seconds is None else round(r.patched_seconds * 1000, 1),
                "percent_saved": None if r.percent_saved is None else round(r.percent_saved, 2),
                "message": r.message[:160],
            }
            for r in results
        ],
    }


def aa_control(scan: ProjectScan, driver) -> dict:
    """A/A：同一单元、同一参数、连续两次独立的 min-of-N 测量。"""
    deltas = []
    for tu in scan.ok_tus[:AA_UNITS]:
        options = getattr(tu, "compile_options", None) or DriverOptions(
            standard="c++17",
            include_dirs=[PROJECT / "include"],
            extra_flags=list(TARGET_FLAGS),
        )
        first, _, err1, _ = _measure(driver, tu.source, options, None, RUNS, 300.0)
        second, _, err2, _ = _measure(driver, tu.source, options, None, RUNS, 300.0)
        if first is None or second is None:
            deltas.append(
                {"tu": tu.source.name, "error": (err1 or err2 or "unknown")[:120]}
            )
            continue
        deltas.append(
            {
                "tu": tu.source.name,
                "a_ms": round(first * 1000, 1),
                "b_ms": round(second * 1000, 1),
                "delta_percent": round(abs(first - second) / min(first, second) * 100, 2),
            }
        )
    valid = [d["delta_percent"] for d in deltas if "delta_percent" in d]
    return {
        "units": deltas,
        "max_delta_percent": max(valid) if valid else None,
        # 单位必须一致：valid 是百分数，阈值也要换成百分数再比。
        # 直接拿 0.97（百分数）去比 0.02（小数）会把每次 A/A 都判成伪阳性。
        "false_positive": any(d > TIME_NOISE_FLOOR * 100 for d in valid),
        "noise_floor_percent": TIME_NOISE_FLOOR * 100,
    }


def main() -> int:
    driver = detect_driver(CLANG)
    scan = build_scan()
    if not scan.ok_tus:
        print("扫描没有成功单元，G2 无法进行")
        return 1

    ranked = scan.candidates_by_estimate()
    tool_arm = ranked[:TOOL_TOP]
    heuristic_arm = [c for c in scan.candidates if c.usage_verdict == "plausibly_unused"]
    rng = random.Random(SEED)
    random_arm = random_candidates(scan, RANDOM_N, rng)

    aa = aa_control(scan, driver)
    tool_res = run_arm(scan, driver, tool_arm, "tool")
    random_res = run_arm(scan, driver, random_arm, "random")
    heuristic_res = run_arm(scan, driver, heuristic_arm, "heuristic") if heuristic_arm else []

    arms = [summarize(tool_res, "tool"), summarize(random_res, "random")]
    if heuristic_res:
        arms.append(summarize(heuristic_res, "heuristic"))

    # 新协议（复测 2 遍）：只对 tool / heuristic 重跑——random 臂本来 0 命中，
    # 复测不会有任何改动，重跑纯属浪费编译预算。
    tool_res_confirm = run_arm(
        scan, driver, [clone_candidate(c) for c in tool_arm], "tool-confirm", confirm=2
    )
    confirm_arms = [summarize(tool_res_confirm, "tool+confirm")]
    if heuristic_arm:
        confirm_arms.append(
            summarize(
                run_arm(
                    scan,
                    driver,
                    [clone_candidate(c) for c in heuristic_arm],
                    "heuristic-confirm",
                    confirm=2,
                ),
                "heuristic+confirm",
            )
        )

    # -- 复现性 ---------------------------------------------------------
    # 只看 tool 臂会漏掉启发式臂的结果，而后者才是唯一有 improved 的臂。
    improved_candidates = [
        c
        for c in list(tool_arm) + list(heuristic_arm)
        if getattr(c, "verified", False) and (c.measured_seconds_saved or 0) > 0
    ]
    repeat = run_arm(scan, driver, improved_candidates, "repeat") if improved_candidates else []
    repeat_ok = sum(1 for r in repeat if r.status == "improved")

    # -- 临界候选的可复现性 ---------------------------------------------
    # 现象：同样 3 条「疑似未使用」候选，第一次跑 0/3 improved（1.04–1.88%），
    # 第二次跑 1/3 improved。差别全在 2% 线附近。这说明**单次 verify 对
    # 临界候选的结论是不稳定的**，必须量化，否则「已验证」只是抽签结果。
    borderline = []
    if heuristic_arm:
        for rep in range(BORDERLINE_REPEATS):
            fresh = [clone_candidate(c) for c in heuristic_arm]
            res = run_arm(scan, driver, fresh, f"borderline-{rep}")
            borderline.append(
                {
                    "repeat": rep + 1,
                    "rows": [
                        {
                            "header": Path(r.candidate.header).name,
                            "tu": Path(r.candidate.tu).name,
                            "percent_saved": None
                            if r.percent_saved is None
                            else round(r.percent_saved, 2),
                            "status": r.status,
                        }
                        for r in res
                    ],
                }
            )
    by_header: dict[str, list[float]] = {}
    for entry in borderline:
        for row in entry["rows"]:
            if row["percent_saved"] is not None:
                by_header.setdefault(row["header"], []).append(row["percent_saved"])
    borderline_summary = {
        "repeats": BORDERLINE_REPEATS,
        "per_candidate": {
            name: {
                "samples": values,
                "min": min(values),
                "max": max(values),
                "spread": round(max(values) - min(values), 2),
                "above_floor": sum(1 for v in values if v >= TIME_NOISE_FLOOR * 100),
                "runs": len(values),
            }
            for name, values in by_header.items()
        },
    }

    payload = {
        "date": date.today().isoformat(),
        "tus": len(scan.ok_tus),
        "candidate_pool": len(scan.candidates),
        "tool_top": TOOL_TOP,
        "random_n": RANDOM_N,
        "runs_per_side": RUNS,
        "noise_floor_percent": TIME_NOISE_FLOOR * 100,
        "aa_control": aa,
        "arms": arms,
        "arms_with_confirmation": confirm_arms,
        "borderline": {"repeats": borderline, **borderline_summary},
        "repeat_check": {
            "improved_in_first_pass": len(improved_candidates),
            "still_improved": repeat_ok,
            "details": [{"header": Path(r.candidate.header).name, "status": r.status,
                         "percent_saved": None if r.percent_saved is None else round(r.percent_saved, 2)}
                        for r in repeat],
        },
    }

    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"g2-loop-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"# G2 优化闭环实测（{payload['date']}）",
        "",
        f"- 工程：nlohmann/json，前 {payload['tus']} 个 TU；候选池 {payload['candidate_pool']} 条。",
        f"- 协议：clang 23.1.3，每侧 {RUNS} 次取最小墙钟，噪声线 {TIME_NOISE_FLOOR * 100:.0f}%。",
        "",
        "## 各臂命中率",
        "",
        "| 臂 | 候选 | 编译通过 | improved | 采纳率 |",
        "|---|---:|---:|---:|---:|",
        *[f"| {a['arm']} | {a['candidates']} | {a['compiled']} | {a['improved']} | **{a['adopt_rate']:.0%}** |" for a in arms],
        "",
        "### 同两臂改用「复测 2 遍」协议（improved 必须复测仍成立）",
        "",
        "| 臂 | 候选 | 编译通过 | improved | 采纳率 |",
        "|---|---:|---:|---:|---:|",
        *[f"| {a['arm']} | {a['candidates']} | {a['compiled']} | {a['improved']} | **{a['adopt_rate']:.0%}** |" for a in confirm_arms],
        "",
        "## A/A 对照（不改任何东西，测两次）",
        "",
        f"- 单元数：{len(aa['units'])}；最大 delta **{aa['max_delta_percent']}%**；"
        f"噪声线 {aa['noise_floor_percent']:.0f}%；伪阳性：**{'有' if aa['false_positive'] else '无'}**",
        "",
        "| TU | 第一次 | 第二次 | delta |",
        "|---|---:|---:|---:|",
        *[
            f"| `{u['tu']}` | {u.get('a_ms', '—')} ms | {u.get('b_ms', '—')} ms | {u.get('delta_percent', u.get('error', '—'))} |"
            for u in aa["units"]
        ],
        "",
        "## 复现性（对第一轮 improved 的候选再测一次）",
        "",
        f"- 第一轮 improved：{payload['repeat_check']['improved_in_first_pass']}，"
        f"复测仍 improved：{payload['repeat_check']['still_improved']}",
        "",
        "## 临界候选：同一候选重复 3 次，结论会翻面吗",
        "",
        "「疑似未使用」的候选收益都在 1–2% 之间，正好压在噪声线上。"
        "下表是对同一批候选重复测 "
        f"{BORDERLINE_REPEATS} 次的结果——它衡量的是**单次 verify 的结论可不可靠**。",
        "",
        "| 候选 | 各次 percent_saved | 极差 | 高于噪声线次数 |",
        "|---|---|---:|---:|",
        *[
            f"| `{name}` | {', '.join(f'{v:.2f}%' for v in info['samples'])} | "
            f"{info['spread']:.2f}pp | {info['above_floor']}/{info['runs']} |"
            for name, info in borderline_summary["per_candidate"].items()
        ],
        "",
        "## tool 臂逐条",
        "",
        "| TU | 头文件 | 结论 | 基线 ms | 移除后 ms | 变化 |",
        "|---|---|---|---:|---:|---:|",
        *[
            f"| `{d['tu']}` | `{d['header']}` | {d['status']} | {d['baseline_ms']} | {d['patched_ms']} | {d['percent_saved']}% |"
            for d in arms[0]["details"]
        ],
        "",
        "## 判读边界",
        "",
        "- 采纳率只回答「推荐列表里有多少条能用」，不回答「省了多少总时间」。",
        "- 随机臂用同一套 verify 流程，因此差异只来自**候选来源**，不来自测量方法。",
        "- 样本量只有几十条，不做显著性检验：那会给出虚假的确定感。",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for a in arms + confirm_arms:
        print(f"{a['arm']:20s} candidates={a['candidates']:3d} improved={a['improved']:3d} "
              f"adopt={a['adopt_rate']:.0%}")
    print(f"A/A max delta = {aa['max_delta_percent']}% (floor {TIME_NOISE_FLOOR*100:.0f}%) "
          f"false_positive={aa['false_positive']}")
    print(f"repeat: {payload['repeat_check']['still_improved']}/{payload['repeat_check']['improved_in_first_pass']}")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
