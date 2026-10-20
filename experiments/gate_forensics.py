"""闸门取证：被拦下的候选，删掉之后到底变了什么。

「已验证层级」的安全性全部押在汇编等价闸门上——**只放行不拦截的闸门等于
没有闸门**。所以不能只报告「放了 N 条」，必须回答两件事：

1. 被拦下的那几条是不是**真的**有害（打印汇编差异的性质与样本）；
2. 闸门**没跑完**的那几条（``codegen == unknown``）到底是什么情况——它们
   曾被当成「未证伪」计入「真能删」，实则只是一台机器给不出指纹。

取证必须与闸门**同源**：用 ``_unit_options`` / ``_patched_options`` /
``codegen.fingerprint``，而不是自己拼一条 ``-S`` 命令——早先手工拼的口径
拼不出汇编，「差异」其实只是「基线侧编译失败了」。

用法::

    python experiments/gate_forensics.py \\
        --precision-json experiments/results/candidate-precision-doctest-20261020m.json \\
        --project <dir> --db <build/compile_commands.json> --compiler clang \\
        --out experiments/results/gate-forensics-doctest-20261020.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader import codegen  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402
from heavyheader.verify import _patched_options, _unit_options  # noqa: E402

#: 归一化后仍不同的行里，如何判断「变了什么性质」。
#: 指令助记符（去掉操作数），用来分辨「少了几条指令」还是「指令换了种」。
_MNEMONIC = re.compile(r"^\s*([a-zA-Z][\w.]*)")


def _normalise(text: str) -> list[str]:
    """与 codegen 同源的归一化：去掉预处理行标记与局部标号编号。"""
    out: list[str] = []
    for line in text.splitlines():
        if codegen._NOISE_LINE.match(line):  # noqa: SLF001 - 取证必须与闸门同源
            continue
        out.append(codegen._LOCAL_LABEL.sub(r"\1#", line))  # noqa: SLF001
    return out


def _include_line(lines: list[str], header_name: str) -> int | None:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#") or "include" not in stripped:
            continue
        for token in stripped.split():
            if token.strip("<>\"").replace("\\", "/").endswith(header_name):
                return index
    return None


def run(args) -> dict:
    precision = json.loads(args.precision_json.read_text(encoding="utf-8"))
    scan = scan_project(
        ScanOptions(
            root=args.project,
            compiler=args.compiler,
            compile_commands=args.db,
            max_tus=args.tus or 500,
            probe_compile=False,
            probe_jobs=1,
        )
    )
    driver = detect_driver(args.compiler)

    # 待取证 = 闸门判「变了」的（silent_change）+ 闸门**没跑完**的
    # （codegen == unknown）。后者更要紧：它曾被当成「未证伪」计入可删，
    # 实则只是一台机器给不出指纹。
    targets = [
        r
        for r in precision["rows_by_rank"]
        if r.get("silent_change") or r.get("codegen") == "unknown"
    ]
    print(f"待取证 {len(targets)} 条（silent_change 或 codegen=unknown）")

    findings = []
    for row in targets:
        tu_name = Path(row["tu"]).name
        header_name = Path(row["header"]).name
        matched = [
            c
            for c in scan.candidates
            if Path(c.tu).name == tu_name and c.header.name == header_name
        ]
        finding = {
            "tu": tu_name,
            "header": header_name,
            "lines_saved": row.get("lines_saved"),
            "parse_shrink": row.get("parse_shrink"),
            "precision_codegen": row.get("codegen"),
            "silent_change": row.get("silent_change"),
        }
        if not matched:
            finding["error"] = "扫描结果里找不到这条候选"
            findings.append(finding)
            continue
        candidate = matched[0]
        source = Path(getattr(candidate, "site", None) or candidate.tu)
        lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        target_line = _include_line(lines, header_name)
        if target_line is None:
            finding["error"] = "源码里找不到对应的 #include 行"
            findings.append(finding)
            continue

        unit_options = _unit_options(
            scan, candidate, DriverOptions(standard="c++17"), []
        )
        asm_options = _patched_options(unit_options, [], source)

        with tempfile.TemporaryDirectory(prefix="gate-forensics-") as tmp:
            workdir = Path(tmp)
            staged = workdir / source.name
            asm_path = workdir / "unit.s"  # 固定路径：汇编里嵌着输出文件名
            staged.write_text("\n".join(lines) + "\n", encoding="utf-8")
            before = codegen.fingerprint(
                driver, staged, asm_options, None, asm_path
            )
            base_asm = (
                asm_path.read_text(encoding="utf-8", errors="replace")
                if asm_path.exists()
                else ""
            )
            patched = list(lines)
            indent = len(patched[target_line]) - len(patched[target_line].lstrip())
            patched[target_line] = (
                " " * indent + "// [heavyheader] " + patched[target_line].lstrip()
            )
            staged.write_text("\n".join(patched) + "\n", encoding="utf-8")
            after = codegen.fingerprint(driver, staged, asm_options, None, asm_path)
            patched_asm = (
                asm_path.read_text(encoding="utf-8", errors="replace")
                if asm_path.exists()
                else ""
            )

        if not before.ok or not after.ok:
            finding["error"] = "汇编未产出（编译失败或输出缺失）"
            finding["baseline_note"] = before.note
            finding["patched_note"] = after.note
            findings.append(finding)
            print(f"  {tu_name} -> {header_name}: 汇编未产出（{before.note[:60]}）")
            continue

        a = _normalise(base_asm)
        b = _normalise(patched_asm)
        diff = [
            line
            for line in difflib.unified_diff(a, b, n=0, lineterm="")
            if not line.startswith(("---", "+++", "@@"))
        ]
        added = [d[1:] for d in diff if d.startswith("+")]
        removed = [d[1:] for d in diff if d.startswith("-")]
        mn_a = {m.group(1) for m in (_MNEMONIC.match(x) for x in added) if m}
        mn_r = {m.group(1) for m in (_MNEMONIC.match(x) for x in removed) if m}
        finding.update(
            {
                "baseline_asm_lines": len(a),
                "patched_asm_lines": len(b),
                "diff_added": len(added),
                "diff_removed": len(removed),
                "removed_mnemonics": sorted(mn_r)[:20],
                "added_mnemonics": sorted(mn_a)[:20],
                "sample_removed": removed[:12],
                "sample_added": added[:12],
                "verdict": (
                    "生成代码减少（删掉后少了一段东西）"
                    if len(removed) > len(added)
                    else "生成代码改写（不是单纯变少）"
                ),
            }
        )
        findings.append(finding)
        print(
            f"  {tu_name} -> {header_name}: {len(a)} → {len(b)} 行，"
            f"−{len(removed)} / +{len(added)}，{finding['verdict']}"
        )

    return {"project": str(args.project), "findings": findings}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--precision-json", type=Path, required=True)
    ap.add_argument("--project", type=Path, required=True)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--tus", type=int, default=0)
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
