"""批量反事实实验：单条在噪声线下，同一单元删掉一整组能不能测出来。

背景：删一条 include 通常只省 1–3 ms，而单条编译 ~220 ms、复测线 2%，也就是
**单条在物理上测不准**。但开发者不会一条一条删——他会把同一个 .c 里几条没用
的 include 一起删掉、重编一次。本脚本量的就是那个单位：

    基线 = 原文件          （min of N 次）
    补丁 = 同一个文件、注释掉该单元全部候选行（min of N 次）

判定仍用 `TIME_NOISE_FLOOR`（2%）并且复测一遍；收益归到**整组**，绝不摊到
单条（单条的贡献在这个尺度上不可分辨）。

用法::

    python experiments/batch_probe.py --corpus <dir> --compiler <clang> \\
        --max-batch 6 --runs 3 --confirm 2
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader import codegen  # noqa: E402
from heavyheader.attrib.advice import TIME_NOISE_FLOOR  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.models import candidate_rank_key  # noqa: E402
from heavyheader.scanner import (  # noqa: E402
    ScanOptions,
    default_include_dirs,
    scan_project,
)
from heavyheader.verify import (  # noqa: E402
    _measure,
    _patched_options,
    _unit_options_for_path,
)


def comment_out_many(text: str, line_numbers: list[int]) -> str:
    """把多行一起注释掉。行号是**原始文本**的，注释不会移动任何一行。"""
    lines = text.splitlines()
    for number in line_numbers:
        index = number - 1
        if not 0 <= index < len(lines):
            continue
        target = lines[index]
        bom = ""
        if target.startswith("\ufeff"):
            bom, target = "\ufeff", target[1:]
        indent = len(target) - len(target.lstrip())
        lines[index] = bom + " " * indent + "// [heavyheader] " + target.lstrip()
    return "\n".join(lines) + "\n"


def measure(driver, source: Path, options: DriverOptions, runs: int, timeout: float):
    seconds, _memory, error, _discarded = _measure(
        driver, source, options, None, runs, timeout
    )
    return seconds, error


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--compiler", required=True)
    ap.add_argument("--max-batch", type=int, default=6)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--confirm", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--only", default="open", choices=["open", "all"])
    ap.add_argument("--skip-probe", action="store_true", help="不做逐条编译探针（旧行为）")
    ap.add_argument("--check-codegen", action="store_true", help="对 improved 的组比汇编指纹")
    ap.add_argument("--out", default=None, help="把逐组结果写成 JSON")
    args = ap.parse_args()

    root = Path(args.corpus)
    db = Path(args.db) if args.db else root / "compile_commands.json"
    scan = scan_project(
        ScanOptions(
            root=root,
            compiler=args.compiler,
            compile_commands=db,
            max_tus=500,
            probe_compile=False,
            probe_jobs=1,
        )
    )
    options = next((t.compile_options for t in scan.ok_tus if t.compile_options), None)
    options = options or DriverOptions()
    effective = list(options.include_dirs) or default_include_dirs(scan.root)
    driver = detect_driver(args.compiler)

    pool = [c for c in scan.candidates if c.line is not None and c.tu.is_file()]
    if args.only == "open":
        pool = [c for c in pool if c.usage_verdict != "in_use"]
    pool.sort(key=candidate_rank_key)

    groups: dict[Path, list] = defaultdict(list)
    for candidate in pool:
        groups[candidate.tu].append(candidate)
    # 只做「多于一條」的单元：单条已经有 verify 的逐条测量。
    groups = {tu: items for tu, items in groups.items() if len(items) >= 2}

    print(f"候选 {len(pool)} 条，落在 {len(groups)} 个单元里（每单元 ≥2 条）")

    # 先逐条探一遍「单独删掉能不能编过」。不探的后果实测可见：24 个组里 9 组
    # 因为混进一条编不过的候选整组报废——批量实验的前提是**每一条都合法**。
    def safe_alone(candidate) -> bool:
        """单独删掉这一条：编得过，且**汇编不变**。

        第二道判据不是可选项。实测（Lua，24 个单元）：不加闸门时有 8 组看起来
        「省 2%~50%」，加了之后 **0 组** 站得住——几乎每组都混着 ``lprefix.h``，
        它设的 ``_XOPEN_SOURCE`` / ``_FILE_OFFSET_BITS`` 会改变系统头的声明，
        删掉之后生成的是另一个程序。既证明闸门有用，也说明批量必须建立在
        「每一条单删都安全」的地基上。
        """
        text = candidate.tu.read_text(encoding="utf-8", errors="replace")
        unit_options = _unit_options_for_path(scan, candidate.tu, options, effective)
        patched_options = _patched_options(unit_options, effective, candidate.tu)
        with tempfile.TemporaryDirectory(prefix="heavyheader-batch-probe-") as tmp:
            patched = Path(tmp) / candidate.tu.name
            patched.write_text(
                comment_out_many(text, [candidate.line]), encoding="utf-8"
            )
            seconds, _error = measure(
                driver, patched, patched_options, 1, args.timeout
            )
            if seconds is None:
                return False
            if not args.check_codegen:
                return True
            before, after = codegen.compare(
                driver,
                candidate.tu,
                patched,
                patched_options,
                None,
                Path(tmp) / "asm",
                args.timeout,
            )
            return before.same_as(after)

    if not args.skip_probe:
        kept: dict[Path, list] = {}
        for tu, items in groups.items():
            ok = [c for c in items if safe_alone(c)]
            if len(ok) >= 2:
                kept[tu] = ok
        dropped = sum(len(v) for v in groups.values()) - sum(len(v) for v in kept.values())
        rule = "编不过或汇编变了" if args.check_codegen else "编不过"
        left = sum(len(v) for v in kept.values())
        print(f"逐条探针：{dropped} 条不进批量（{rule}），剩 {left} 条")
        groups = kept
    rows = []
    for tu in sorted(groups, key=lambda p: p.name):
        items = groups[tu][: args.max_batch]
        lines = [c.line for c in items]
        text = tu.read_text(encoding="utf-8", errors="replace")
        with tempfile.TemporaryDirectory(prefix="heavyheader-batch-") as tmp:
            patched = Path(tmp) / tu.name
            patched.write_text(comment_out_many(text, lines), encoding="utf-8")
            unit_options = _unit_options_for_path(scan, tu, options, effective)
            patched_options = _patched_options(unit_options, effective, tu)

            base, error = measure(driver, tu, unit_options, args.runs, args.timeout)
            if base is None:
                print(f"{tu.name:<14}基线编译失败：{error}")
                continue
            after, error = measure(
                driver, patched, patched_options, args.runs, args.timeout
            )
            if after is None:
                rows.append(
                    {
                        "tu": tu.name,
                        "count": len(items),
                        "status": "compile-error",
                        "base_ms": base * 1000,
                        "patched_ms": None,
                        "percent": None,
                        "confirm": None,
                        "message": (error or "")[:80],
                        "headers": [c.header.name for c in items],
                    }
                )
                print(f"{tu.name:<14}{len(items)} 条  编译失败：{(error or '')[:60]}")
                continue
            percent = (base - after) / base * 100 if base else 0.0
            confirm = None
            status = "improved" if percent >= TIME_NOISE_FLOOR * 100 else "no-change"
            if status == "improved" and args.check_codegen:
                # 收益大不等于改对了：lua.c 删掉 windows.h 能省 230 ms（−50%），
                # 但汇编指纹变了——那是一个被 #ifdef 关掉的实现块，不是优化。
                before, after_fp = codegen.compare(
                    driver, tu, patched, patched_options, None, Path(tmp) / "asm", args.timeout
                )
                if not before.same_as(after_fp):
                    status = "silent-change"
                    confirm = None
                    print(f"!{tu.name:<14}{len(items)} 条  汇编变了，不算收益")
                    rows.append(
                        {
                            "tu": tu.name,
                            "count": len(items),
                            "status": status,
                            "base_ms": base * 1000,
                            "patched_ms": after * 1000,
                            "percent": percent,
                            "confirm": None,
                            "message": "删除后生成的代码不同",
                            "headers": [c.header.name for c in items],
                        }
                    )
                    continue
            if status == "improved" and args.confirm > 1:
                percents = [percent]
                ok = True
                for _ in range(args.confirm - 1):
                    fresh_base, _e1 = measure(
                        driver, tu, unit_options, args.runs, args.timeout
                    )
                    fresh_after, _e2 = measure(
                        driver, patched, patched_options, args.runs, args.timeout
                    )
                    if fresh_base is None or fresh_after is None:
                        ok = False
                        break
                    again = (fresh_base - fresh_after) / fresh_base * 100
                    percents.append(again)
                    if again < TIME_NOISE_FLOOR * 100:
                        ok = False
                confirm = percents
                if not ok:
                    status = "no-change"

        rows.append(
            {
                "tu": tu.name,
                "count": len(items),
                "status": status,
                "base_ms": base * 1000,
                "patched_ms": after * 1000,
                "percent": percent,
                "confirm": confirm,
                "message": "",
                "headers": [c.header.name for c in items],
            }
        )
        mark = "*" if status == "improved" else " "
        extra = " 复测 " + str([round(p, 2) for p in confirm]) if confirm else ""
        print(
            f"{mark}{tu.name:<14}{len(items)} 条  {base * 1000:6.1f} → "
            f"{after * 1000:6.1f} ms  {percent:+.2f}%{extra}  "
            + ",".join(c.header.name for c in items)
        )

    json_out = Path(args.out) if args.out else None
    if json_out is not None:
        import json

        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(
                {
                    "project": str(root),
                    "compiler": args.compiler,
                    "max_batch": args.max_batch,
                    "runs": args.runs,
                    "confirm": args.confirm,
                    "check_codegen": args.check_codegen,
                    "pool": len(pool),
                    "groups": len(groups),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"已写入 {json_out}")

    measured = [r for r in rows if r["patched_ms"] is not None]
    total_base_all = sum(r["base_ms"] for r in measured)
    total_after_all = sum(r["patched_ms"] for r in measured)
    improved = [r for r in rows if r["status"] == "improved"]
    total_saved = sum(r["base_ms"] - r["patched_ms"] for r in improved)
    total_base = sum(r["base_ms"] for r in improved)
    print(
        f"\n可分辨组 {len(improved)}/{len(rows)}；这些组合计省 {total_saved:.0f} ms，"
        f"占其基线 {total_saved / total_base * 100 if total_base else 0:.1f}%"
    )
    print(f"平均每组 {sum(r['count'] for r in improved) / len(improved) if improved else 0:.1f} 条")
    print(
        f"这 {len(measured)} 个单元合起来：{total_base_all / 1000:.2f}s → "
        f"{total_after_all / 1000:.2f}s"
        f"（{(total_after_all - total_base_all) / total_base_all * 100:+.2f}%）"
        "——单条省 1–3 ms 在这里才凑成一个能说出口的数"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
