"""G2 阳性对照：在一个**明确可删**的构造样本上，验证管线能不能测出收益。

为什么需要它
------------
G2 在 nlohmann/json 上得到 0 条可复现优化。这个结果有两种完全不同的解释：

1. 该工程的候选池里确实没有值得删的 include（每个测试 TU 都要 json.hpp，
   删掉就编译不过；`iostream` 只省 1–2%，压在噪声线上）；
2. 我们的测量管线本身失灵，任何收益都测不出来。

两者对产品的含义完全相反。用一个「构造时就留了可删 include」的样本做阳性
对照，可以把它们分开：如果这里也测不出收益，问题在管线；如果这里能测出，
那么 json 上的 0 命中是工程的真实结论，而不是工具故障。

样本：``benchmarks/cases/demo``——项目自带的构造用例，其中一个 TU 包含了一个
只用一次的重量级头。它是**构造样本**，不能用来证明「真实工程能提速」，
只能证明「有收益时管线能看见」。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from heavyheader.attrib.advice import TIME_NOISE_FLOOR  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

from g2_loop import CLANG, RUNS, summarize  # noqa: E402

DEMO = ROOT / "benchmarks" / "cases" / "demo"
OUT = ROOT / "experiments" / "results"


def main() -> int:
    driver = detect_driver(CLANG)
    scan = scan_project(
        ScanOptions(
            root=DEMO,
            compiler=CLANG,
            include_dirs=[DEMO / "include"],
            standard="c++17",
            jobs=1,
        ),
        driver=driver,
    )
    if not scan.candidates:
        print("demo 样本没有候选，阳性对照无法进行")
        return 1

    from heavyheader.verify import VerifyOptions, verify_candidates

    ranked = scan.candidates_by_estimate()
    options = DriverOptions(
        standard="c++17", include_dirs=[DEMO / "include"], extra_flags=[]
    )
    results = verify_candidates(
        scan,
        driver,
        options,
        verify=VerifyOptions(top=len(ranked), runs=RUNS, jobs=1, confirm_passes=2),
    )

    arm = summarize(results, "demo-control")
    payload = {
        "date": date.today().isoformat(),
        "sample": str(DEMO.relative_to(ROOT)).replace("\\", "/"),
        "tus": len(scan.ok_tus),
        "candidates": len(scan.candidates),
        "runs_per_side": RUNS,
        "noise_floor_percent": TIME_NOISE_FLOOR * 100,
        "arm": arm,
        "note": "构造样本，仅作阳性对照；不能作为「真实工程可提速」的证据。",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stem = OUT / f"g2-control-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"# G2 阳性对照（构造样本，{payload['date']}）",
        "",
        f"- 样本：`{payload['sample']}`（{payload['tus']} 个 TU，{payload['candidates']} 条候选）",
        f"- 协议：clang，每侧 {RUNS} 次取最小墙钟，复测 2 遍，噪声线 "
        f"{TIME_NOISE_FLOOR * 100:.0f}%",
        "",
        "| TU | 头文件 | 结论 | 基线 ms | 移除后 ms | 变化 |",
        "|---|---|---|---:|---:|---:|",
        *[
            f"| `{d['tu']}` | `{d['header']}` | {d['status']} | {d['baseline_ms']} "
            f"| {d['patched_ms']} | {d['percent_saved']}% |"
            for d in arm["details"]
        ],
        "",
        f"**结果：{arm['improved']}/{arm['candidates']} 条 improved。**",
        "",
        "## 这条对照能说明什么",
        "",
        "- 能：当收益真实存在且明显时，管线测得出来，且能通过复测。",
        "- 不能：证明真实工程也一定存在这种收益。构造样本是按「留一条可删 include」"
        "设计的，真实工程的候选池通常没有这么干净。",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"demo candidates={arm['candidates']} improved={arm['improved']} "
          f"adopt={arm['adopt_rate']:.0%}")
    for d in arm["details"]:
        print(f"  {d['tu']} | {d['header']} | {d['status']} | {d['percent_saved']}%")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
