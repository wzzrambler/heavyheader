#!/usr/bin/env python3
"""伞头拆分实验：每个 TU 只 include 自己需要的，能省多少？

问题来源（2026-10-19）：doctest 的 24 个编译单元都 include 同一个
``prelude.h``，而这个头里的 30+ 个标准头并非每个 TU 都需要。伞头把「大家都要
的宏」和「各取所需的声明」捆在一起，代价由所有 TU 分摊。

**这个实验的第一版是错的，错误值得记下来**：第一版只用「删掉后还能编译」当判据，
得到 24 TU −25%，看起来很漂亮。复核时发现目标文件从 21,289 字节变成 10,183 字节
——编出来的不是同一个程序，而是删掉 `<iomanip>` 后重载解析变了、代码少了一半。
「编译通过」从来不是「语义不变」，这条本项目自己写在方法论里，实验里照样踩了。

所以本版用**双判据**：

1. 编译成功；
2. 生成的**汇编文本**与基线逐字节相同（``-S`` 输出的 sha256 相等）。

为什么不用目标文件：实测同一份输入连编两次，``.o`` 的哈希就不一样（大小相同、
内容有别），换成 ``-frandom-seed`` 也不稳定——用 ``.o`` 比哈希会把所有改动都判成
「语义变了」。汇编文本是确定性的（连跑三次哈希一致），且覆盖前端 + 代码生成，
是「编出来的还是同一个程序」的可靠判据。

第一版更粗糙的地方：只判「编译成功」，于是删掉 ``<iomanip>`` 后被判为可删，
目标文件从 21,289 字节缩到 10,183 字节——重载解析变了，编的根本不是同一个程序，
却报出「24 个 TU 省 25%」。这类错误正是本项目方法论里反复写的「编译通过 ≠
语义不变」，实验里照样踩了一次，因此判据必须硬。

用法：
    python experiments/split_umbrella_probe.py \\
        --db build/compile_commands.json --tus 12 --rounds 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heavyheader.scanner import _split_command, _strip_output_flag  # noqa: E402

PRELUDE_REL = Path("doctest/parts/private/prelude.h")


class Bench:
    """在 prelude.h 上做「删掉若干 include 后编译」的实验台。"""

    def __init__(self, entry: dict, prelude: Path) -> None:
        self.entry = entry
        self.prelude = prelude
        self.original = prelude.read_text(encoding="utf-8")
        self.lines = self.original.splitlines(keepends=True)
        # compile_commands 里带 -c；它与 -S 互斥，留着会让 -S 被忽略、产出仍是
        # .o（于是哈希不稳定，判据全线失效——实测踩过）。
        self.argv = [
            token
            for token in _strip_output_flag(_split_command(entry["command"]))
            if token not in ("-c", "-S")
        ]
        self.includes = [
            n for n, line in enumerate(self.lines, 1) if line.strip().startswith("#include")
        ]
        # 输出路径必须**固定**：clang 会把输出文件名作为 "Object name" 字符串
        # 写进汇编，换一次路径就变一个字节，哈希比对全部失效（实测踩过）。
        self.out_dir = Path(tempfile.mkdtemp(prefix="hh-split-"))
        self.out_path = self.out_dir / "x.s"
        self.baseline_sha = ""
        self.compiles = 0

    def run(self, removed: set[int]) -> tuple[bool, float, str]:
        text = "".join(
            ("// " + line if i in removed else line) for i, line in enumerate(self.lines, 1)
        )
        self.prelude.write_text(text, encoding="utf-8")
        try:
            self.compiles += 1
            started = time.perf_counter()
            proc = subprocess.run(
                self.argv + ["-S", "-o", str(self.out_path)],
                cwd=self.entry["directory"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            wall = time.perf_counter() - started
            asm = (
                self.out_path.read_text(encoding="utf-8", errors="replace")
                if self.out_path.exists()
                else ""
            )
            return proc.returncode == 0, wall, hashlib.sha256(asm.encode()).hexdigest()
        finally:
            self.prelude.write_text(self.original, encoding="utf-8")

    def is_equivalent(self, removed: set[int]) -> bool:
        ok, _wall, digest = self.run(removed)
        return ok and digest == self.baseline_sha


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--tus", type=int, default=12)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    project = args.project.resolve()
    prelude = project / PRELUDE_REL
    entries = json.loads(args.db.read_text(encoding="utf-8"))[: args.tus]

    rows = []
    for index, entry in enumerate(entries):
        bench = Bench(entry, prelude)
        ok, _wall, sha = bench.run(set())
        if not ok:
            print(f"[skip] {Path(entry['file']).name}: 基线编译失败")
            continue
        bench.baseline_sha = sha
        if not bench.is_equivalent(set()):
            print(f"[skip] {Path(entry['file']).name}: 判据不稳定（同输入两次结果不同）")
            continue

        removed: list[int] = []
        for number in bench.includes:
            if bench.is_equivalent(set(removed) | {number}):
                removed.append(number)

        base_times: list[float] = []
        minimal_times: list[float] = []
        for round_index in range(args.rounds):
            for arm in (("b", "m") if round_index % 2 == 0 else ("m", "b")):
                ok, wall, _sha = bench.run(set() if arm == "b" else set(removed))
                if ok:
                    (base_times if arm == "b" else minimal_times).append(wall)
        if not base_times or not minimal_times:
            continue

        base_median = statistics.median(base_times)
        minimal_median = statistics.median(minimal_times)
        rows.append(
            {
                "tu": Path(entry["file"]).name,
                "includes": len(bench.includes),
                "removed": len(removed),
                "baseline_ms": round(base_median * 1000, 1),
                "minimal_ms": round(minimal_median * 1000, 1),
                "delta_ms": round((base_median - minimal_median) * 1000, 1),
                "object_identical": bench.is_equivalent(set(removed)),
                "removed_headers": [bench.lines[n - 1].strip() for n in removed],
                "compiles": bench.compiles,
            }
        )
        print(
            f"[{index + 1:2d}/{len(entries)}] {rows[-1]['tu']:22s} "
            f"删 {len(removed):2d}/{len(bench.includes)}  "
            f"{rows[-1]['baseline_ms']:7.1f} → {rows[-1]['minimal_ms']:7.1f} ms "
            f"（{rows[-1]['delta_ms']:+7.1f}）  目标一致={rows[-1]['object_identical']}"
        )

    total_base = sum(r["baseline_ms"] for r in rows)
    total_min = sum(r["minimal_ms"] for r in rows)
    payload = {
        "project": str(project),
        "tus": len(rows),
        "total_baseline_ms": round(total_base, 1),
        "total_minimal_ms": round(total_min, 1),
        "total_delta_percent": round((1 - total_min / total_base) * 100, 2) if total_base else 0,
        "removed_mean": round(sum(r["removed"] for r in rows) / max(len(rows), 1), 1),
        "all_objects_identical": all(r["object_identical"] for r in rows),
        "distinct_minimal_sets": len({tuple(r["removed_headers"]) for r in rows}),
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\n[summary] {len(rows)} TU 合计 {total_base / 1000:.2f}s → {total_min / 1000:.2f}s "
        f"（{payload['total_delta_percent']:+.2f}%）；平均每 TU 可删 "
        f"{payload['removed_mean']}；最小集合 {payload['distinct_minimal_sets']} 种；"
        f"全部目标文件逐字节一致={payload['all_objects_identical']}"
    )
    print(f"[done] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
