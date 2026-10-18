"""交替测量能把噪声压到多少？——G2 能不能做，全看这个数。

上一轮（``g2_aggregate_aa.py``）的结论是个坏消息：把 24 个 TU 的耗时加
起来测两次，差 **2.005%**，和单 TU 的噪声线一样大。原因写在数据里——
三次聚合耗时是 20299 / 20269 / 20675 ms，前两次几乎相同，第三次整体高
2%。这不是每个 TU 各自抖（那种误差聚合时会互相抵消），而是**整批测量
期间的机器漂移**：一次测量要跑 3 分多钟，这期间 CPU 降频、后台任务、
热缓存都会让后测的一侧整体偏慢。

而现有协议是「先测完基线 3 遍，再测补丁 3 遍」（基线还被缓存复用，
可能几十秒前就测好了）。漂移全部落在**两侧之差**上，于是 2% 的机器漂移
直接伪装成 2% 的收益——这正是 G2 里那些 1.0–1.9% 的候选无法判定的根源。

修法是交替（interleave）：把 B/P/B/P/B/P 排在同一时间窗里，每对之间只隔
一次编译，漂移对两侧同时作用，求和时抵消。

这个脚本就是标定「交替协议下的噪声有多大」：``--aa`` 模式下**两侧是同一
份代码**，所以测出来的任何差异都是纯噪声。

用法::

    python experiments/g2_interleaved.py --project experiments/_work/doctest \
        --db experiments/_work/doctest/compile_commands.json --tus 24 --pairs 2
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import _measure  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]


def interleave(
    driver, source: Path, options: DriverOptions, patched_text: str | None,
    runs: int, timeout: float,
) -> tuple[float | None, float | None, str | None]:
    """交替测基线 / 补丁各 ``runs`` 次，各取最小值。

    ``patched_text`` 为 None 时两侧是同一份代码（A/A 对照）。
    """
    best_base: float | None = None
    best_patched: float | None = None
    error: str | None = None
    with tempfile.TemporaryDirectory(prefix="heavyheader-interleave-") as tmp:
        patched_path: Path | None = None
        if patched_text is not None:
            patched_path = Path(tmp) / source.name
            patched_path.write_text(patched_text, encoding="utf-8")
        patched_options = DriverOptions(
            standard=options.standard,
            include_dirs=[*options.include_dirs, source.parent],
            defines=list(options.defines),
            extra_flags=list(options.extra_flags),
            full_compile=options.full_compile,
        )
        for _ in range(runs):
            base, _m, err_b, _d = _measure(
                driver, source, options, None, 1, timeout
            )
            if base is None:
                error = f"基线编译失败：{err_b}"
                return None, None, error
            best_base = base if best_base is None else min(best_base, base)
            target = patched_path if patched_path is not None else source
            patched, _m2, err_p, _d2 = _measure(
                driver, target, patched_options, None, 1, timeout
            )
            if patched is None:
                error = f"补丁编译失败：{err_p}"
                return None, None, error
            best_patched = (
                patched if best_patched is None else min(best_patched, patched)
            )
    return best_base, best_patched, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    project = Path(args.project).resolve()
    db = Path(args.db).resolve()
    driver = detect_driver(CLANG)
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=CLANG,
            compile_commands=db,
            max_tus=args.tus,
            jobs=1,
            extra_flags=list(TARGET_FLAGS),
        ),
        driver=driver,
    )
    if not scan.ok_tus:
        print("扫描没有成功单元")
        return 1

    rounds: list[dict] = []
    for pair in range(args.pairs):
        base_total = 0.0
        patched_total = 0.0
        per_unit = []
        for tu in scan.ok_tus:
            options = getattr(tu, "compile_options", None) or DriverOptions(
                standard="c++17", extra_flags=list(TARGET_FLAGS)
            )
            base, patched, err = interleave(
                driver, tu.source, options, None, args.runs, args.timeout
            )
            if base is None or patched is None:
                print(f"{tu.source.name}: {err}")
                return 1
            base_total += base
            patched_total += patched
            per_unit.append(
                {
                    "tu": tu.source.name,
                    "base_ms": round(base * 1000, 1),
                    "patched_ms": round(patched * 1000, 1),
                    "delta_percent": round(abs(patched - base) / min(base, patched) * 100, 3),
                }
            )
        delta = abs(patched_total - base_total) / min(base_total, patched_total) * 100
        rounds.append(
            {
                "pair": pair + 1,
                "base_ms": round(base_total * 1000, 1),
                "patched_ms": round(patched_total * 1000, 1),
                "aggregate_delta_percent": round(delta, 3),
                "max_unit_delta_percent": max(u["delta_percent"] for u in per_unit),
                "units": per_unit,
            }
        )
        print(
            f"第 {pair + 1} 对：聚合 {base_total * 1000:.0f} vs {patched_total * 1000:.0f} ms "
            f"→ {delta:.3f}%（单 TU 最大 {rounds[-1]['max_unit_delta_percent']:.3f}%）",
            flush=True,
        )

    deltas = [r["aggregate_delta_percent"] for r in rounds]
    unit_deltas = [u["delta_percent"] for r in rounds for u in r["units"]]
    unit_deltas.sort()
    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "protocol": "interleaved min-of-N, both sides identical (A/A)",
        "tus": len(scan.ok_tus),
        "runs_per_side": args.runs,
        "pairs": args.pairs,
        "rounds": rounds,
        "aggregate_delta_percent": deltas,
        "max_aggregate_delta_percent": max(deltas),
        "unit_delta_percent": {
            "p50": round(unit_deltas[len(unit_deltas) // 2], 3),
            "p90": round(unit_deltas[int(len(unit_deltas) * 0.9)], 3),
            "max": round(unit_deltas[-1], 3),
        },
    }
    out = ROOT / "experiments" / "results" / f"g2-interleaved-aa-{project.name}-{date.today():%Y%m%d}.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "rounds"}, indent=2, ensure_ascii=False))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
