#!/usr/bin/env python3
"""include 删除的**组合效应**实验：逐条安全 ≠ 一起安全。

背景：2026-10-19 的伞头拆分实验先报出「删掉 35/36 条标准头可省 25% 墙钟」，
复核后发现是假的——编译成功，但目标文件从 21,289 字节变成 10,183 字节，
编出来的不是同一个程序。改成「编译成功 + 目标文件大小不变」的双判据后：

* 单独删 34/36 条 → 目标文件逐字节大小不变（`<iomanip>` 与伞头本身除外）；
* 但把这 34 条**一起**删掉 → 目标文件又变了。

这说明删除之间存在**相互作用**，而整个行业通行的做法（deheader、
cleanup-headers、以及本项目的 verify）都是**逐条验证**。若逐条结论可以
直接合并，那没问题；本实验要回答：能不能找到一个最小反例组合。

方法：ddmin（Zeller）在「逐条安全的集合」上搜索，谓词 =
「编译成功，但目标文件大小不等于基线」。找到的就是最小反例。

用法：
    python experiments/include_interaction_probe.py \\
        --db build/compile_commands.json --tu expression.cpp
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heavyheader.scanner import _split_command, _strip_output_flag  # noqa: E402

PRELUDE_REL = Path("doctest/parts/private/prelude.h")


class Probe:
    """在 prelude.h 上做「删掉若干 include 后编译」的一次性实验台。"""

    def __init__(self, project: Path, entry: dict, prelude: Path) -> None:
        self.entry = entry
        self.prelude = prelude
        self.original = prelude.read_text(encoding="utf-8")
        self.lines = self.original.splitlines(keepends=True)
        self.argv = _strip_output_flag(_split_command(entry["command"]))
        self.includes = [
            n for n, line in enumerate(self.lines, 1) if line.strip().startswith("#include")
        ]
        self.baseline_size = 0
        self.compiles = 0

    def run(self, removed: set[int]) -> tuple[bool, int]:
        text = "".join(
            ("// " + line if i in removed else line) for i, line in enumerate(self.lines, 1)
        )
        with tempfile.TemporaryDirectory() as tmp:
            self.prelude.write_text(text, encoding="utf-8")
            try:
                out = Path(tmp) / "x.o"
                self.compiles += 1
                rc = subprocess.run(
                    self.argv + ["-o", str(out)],
                    cwd=self.entry["directory"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                ).returncode
                return rc == 0, (out.stat().st_size if out.exists() else -1)
            finally:
                self.prelude.write_text(self.original, encoding="utf-8")

    def changes_program(self, removed: set[int]) -> bool:
        """谓词：编译成功 **但** 目标文件不是基线那个。"""
        ok, size = self.run(removed)
        return ok and size != self.baseline_size


def ddmin(probe: Probe, items: list[int]) -> list[int]:
    """搜索最小反例（判据：changes_program）。"""
    current = list(items)
    granularity = 2
    while len(current) >= 2:
        chunk = max(1, len(current) // granularity)
        parts = [current[i : i + chunk] for i in range(0, len(current), chunk)]
        reduced = False
        for part in parts:
            if probe.changes_program(set(part)):
                current = part
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        for part in parts:
            complement = [x for x in current if x not in part]
            if complement and probe.changes_program(set(complement)):
                current = complement
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        if granularity >= len(current):
            break
        granularity = min(len(current), granularity * 2)
    return current


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--tu", default="expression.cpp")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    project = args.project.resolve()
    prelude = project / PRELUDE_REL
    entries = json.loads(args.db.read_text(encoding="utf-8"))
    entry = next(e for e in entries if Path(e["file"]).name == args.tu)
    probe = Probe(project, entry, prelude)

    ok, size = probe.run(set())
    if not ok:
        print("[fatal] 基线编译失败", file=sys.stderr)
        return 2
    probe.baseline_size = size
    print(f"[baseline] {args.tu}: obj={size} bytes, {len(probe.includes)} includes")

    benign: list[int] = []
    for n in probe.includes:
        ok, size = probe.run({n})
        if ok and size == probe.baseline_size:
            benign.append(n)
    print(f"[single] 逐条删除且目标文件不变：{len(benign)}/{len(probe.includes)}")

    ok, size = probe.run(set(benign))
    joint = (not ok) or size != probe.baseline_size
    print(f"[joint ] 一起删掉这 {len(benign)} 条：编译={'成功' if ok else '失败'} obj={size}"
          f" → {'目标文件变了' if joint else '与基线一致'}")
    if not joint:
        print("[result] 本例没有组合效应：逐条结论可以直接合并", file=sys.stderr)
        payload = {
            "tu": args.tu,
            "baseline_size": probe.baseline_size,
            "benign_singles": len(benign),
            "joint_changes_program": False,
            "minimal_counterexample": [],
            "compiles_used": probe.compiles,
        }
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    minimal = ddmin(probe, benign)
    names = [probe.lines[n - 1].strip() for n in minimal]
    print(f"\n[result] 最小反例：{len(minimal)} 条 —— {', '.join(names)}")
    print(f"         每条单独删都保持目标文件不变，合起来却改变程序")
    payload = {
        "tu": args.tu,
        "baseline_size": probe.baseline_size,
        "benign_singles": len(benign),
        "joint_changes_program": True,
        "minimal_counterexample": names,
        "minimal_lines": minimal,
        "compiles_used": probe.compiles,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {args.out}（共 {probe.compiles} 次编译）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
