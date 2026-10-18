"""min-of-3 的 A/A 噪声：回答「2% 判定线是否安全」。

verify 对每侧编译 3 次取最小值。单次测量的 p90 不能直接套到它头上，
所以这里用同一物理动作测两轮「min-of-3」，看两轮之间差多少。
"""

from __future__ import annotations

import json
import shlex
import statistics
import subprocess
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / "_work" / "compile_commands.json"
OUT = ROOT / "experiments" / "results" / f"noise-min3-{date.today():%Y%m%d}.json"


def _clean(cmd: str) -> list[str]:
    # Windows 反斜杠会被 shlex 当转义吃掉，先统一成正斜杠。
    args = shlex.split(cmd.replace("\\", "/"))
    out: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "-c":
            i += 1
            continue
        if args[i] == "-o":
            i += 2
            continue
        out.append(args[i])
        i += 1
    out.append("-fsyntax-only")
    return out


def _min_of(args: list[str], cwd: str, runs: int = 3) -> float:
    best = float("inf")
    for _ in range(runs):
        start = time.perf_counter()
        proc = subprocess.run(args, cwd=cwd, capture_output=True)
        elapsed = time.perf_counter() - start
        if proc.returncode != 0:
            return 0.0
        best = min(best, elapsed)
    return best


def main() -> int:
    entries = json.loads(DB.read_text(encoding="utf-8"))[:10]
    rows = []
    for entry in entries:
        args = _clean(entry["command"])
        a = _min_of(args, entry["directory"])
        b = _min_of(args, entry["directory"])
        if a <= 0 or b <= 0:
            continue
        mean = (a + b) / 2
        rows.append(
            {
                "file": Path(entry["file"]).name,
                "group_a": round(a, 5),
                "group_b": round(b, 5),
                "spread_percent": round(abs(a - b) / mean * 100, 2),
            }
        )

    spreads = sorted(r["spread_percent"] for r in rows)
    pos = (len(spreads) - 1) * 0.9
    lo, hi = int(pos), min(int(pos) + 1, len(spreads) - 1)
    p90 = spreads[lo] + (spreads[hi] - spreads[lo]) * (pos - lo)

    payload = {
        "tu_count": len(rows),
        "median_percent": round(statistics.median(spreads), 2),
        "p90_percent": round(p90, 2),
        "max_percent": round(max(spreads), 2),
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
