"""证据层实测：同一批候选，看每条结论最终站在哪一级证据上。

为什么需要它（2026-10-20）：供给侧漏报诊断发现，8 条落地收益里 7 条被文本
判据判成 ``in_use`` / ``unknown``，却全都过了汇编等价闸门。也就是说——**判据
说不清的地方，编译器说得清**。但旧界面把两者混在同一句「建议删除」里，读者
无从分辨哪句是证据、哪句是推断。

本脚本回答三个问题：

1. 送测池里每条结论分别落在 ``verified`` / ``equivalent`` / ``compiles_only``
   哪一档；
2. **文本判据与证据层交叉**：多少条被判据判为「在用/无法判定」的候选，最终
   站在最高的 ``verified`` 档（这是「已验证层级」的全部价值所在）；
3. 闸门拦下了几条（``codegen-changed``）——这是新层级安全性的分母：**只放行
   不拦截的闸门等于没有闸门**，所以这里必须同时报告拦下数。

选池复用 ``timing_funnel.select_pool``，与归档的 25%、repeat_probe 的 40%
**同一个分母**，三个数字才可比。用法::

    python experiments/evidence_tiers.py \\
        --precision-json experiments/results/candidate-precision-lua-20261020m.json \\
        --project <dir> --db <compile_commands.json> --compiler clang \\
        --out experiments/results/evidence-tiers-lua-20261020.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from timing_funnel import select_pool  # noqa: E402

from heavyheader.attrib.advice import TIER_EQUIVALENT  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import VerifyOptions, verify_candidates  # noqa: E402


def run(args) -> dict:
    precision = json.loads(args.precision_json.read_text(encoding="utf-8"))
    scan = scan_project(
        ScanOptions(
            root=args.project,
            compiler=args.compiler,
            compile_commands=args.db,
            max_tus=args.tus or 500,
            probe_compile=False,
            probe_jobs=args.jobs,
        )
    )
    selected = select_pool(scan, precision["rows_by_rank"], args.noise_fraction)
    pool = selected["pool"]
    print(f"送测池 {len(pool)} 条（与归档 25% / repeat_probe 40% 同一分母）")
    if not pool:
        return {"pool": 0}

    driver = detect_driver(args.compiler)
    saved = scan.candidates
    scan.candidates = list(pool)
    try:
        results = verify_candidates(
            scan,
            driver,
            DriverOptions(standard="c++17"),
            verify=VerifyOptions(
                top=len(pool),
                runs=args.runs,
                jobs=args.jobs,
                confirm_passes=args.confirm,
                codegen_check=True,
            ),
        )
    finally:
        scan.candidates = saved

    #: 这一层真正推向用户多少条建议：闸门证明等价 + 维护收益判据成立。
    equivalent_lean_remove = [
        r
        for r in results
        if r.evidence_tier == TIER_EQUIVALENT
        and r.candidate.advice is not None
        and r.candidate.advice.lean == "remove"
    ]
    verdicts = Counter(
        (
            r.evidence_tier or "rejected",
            getattr(r.candidate.advice, "verdict", None)
            and r.candidate.advice.verdict.value
            or "-",
        )
        for r in results
    )
    tiers = Counter(r.evidence_tier or "rejected" for r in results)
    cross = Counter(
        (r.candidate.usage_verdict or "?", r.evidence_tier or "rejected")
        for r in results
    )
    gated = [r for r in results if r.codegen_checked]
    blocked = [r for r in results if r.status == "codegen-changed"]
    improved = [r for r in results if r.status == "improved"]

    print(f"闸门跑过 {len(gated)} 条，拦下 {len(blocked)} 条（codegen-changed）")
    print(f"分层：{dict(tiers)}")
    print("文本判据 × 证据层：")
    for (usage, tier), count in sorted(cross.items()):
        print(f"    {usage:18} {tier:16} {count}")

    return {
        "project": str(args.project),
        "pool": len(pool),
        "runs": args.runs,
        "confirm_passes": args.confirm,
        "gate_ran": len(gated),
        "gate_blocked": len(blocked),
        "gate_blocked_detail": [
            {
                "tu": Path(r.candidate.tu).name,
                "header": Path(r.candidate.header).name,
                "usage": r.candidate.usage_verdict,
                "side_effect_suspected": r.candidate.side_effect_suspected,
                "message": r.message[:200],
            }
            for r in blocked
        ],
        "tiers": dict(tiers),
        "verdicts": {f"{t}|{v}": c for (t, v), c in sorted(verdicts.items())},
        "equivalent_lean_remove": len(equivalent_lean_remove),
        "equivalent_lean_remove_lines": sum(
            (r.candidate.advice.review_gain_lines or 0)
            for r in equivalent_lean_remove
        ),
        "cross": {f"{u}|{t}": c for (u, t), c in sorted(cross.items())},
        "improved": len(improved),
        "adopt_rate_on_pool": round(len(improved) / len(pool), 3),
        #: 站在最高档的候选里，文本判据没推荐的有几条——「已验证层级」的价值
        "verified_outside_recommendation": sum(
            1
            for r in results
            if r.evidence_tier == "verified"
            and r.candidate.usage_verdict != "plausibly_unused"
        ),
        "details": [
            {
                "tu": Path(r.candidate.tu).name,
                "header": Path(r.candidate.header).name,
                "usage": r.candidate.usage_verdict,
                "tier": r.evidence_tier,
                "status": r.status,
                "codegen_checked": r.codegen_checked,
                "codegen_identical": r.codegen_identical,
                "confirm_passes": r.confirm_passes,
                "percent_saved": (
                    None if r.percent_saved is None else round(r.percent_saved, 3)
                ),
                "verdict": (
                    None
                    if r.candidate.advice is None
                    else r.candidate.advice.verdict.value
                ),
                "lean": (
                    "" if r.candidate.advice is None else r.candidate.advice.lean
                ),
                "review_gain_lines": (
                    None
                    if r.candidate.advice is None
                    else r.candidate.advice.review_gain_lines
                ),
                "message": r.message[:200],
            }
            for r in results
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precision-json", type=Path, required=True)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--tus", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--confirm", type=int, default=2)
    ap.add_argument("--noise-fraction", type=float, default=0.01)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    payload = run(args)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
