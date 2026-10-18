"""同任务对照：ClangBuildAnalyzer vs HeavyHeader（同一批 TU）。

流程：
1. 取 compile_commands.json 前 N 个 TU，用 clang++ -ftime-trace 编译到临时目录；
2. ClangBuildAnalyzer --start / --stop / --analyze 得到「Expensive headers」；
3. HeavyHeader 用 clang 扫同一批 TU，得到按归因行数排序的 Top-N；
4. 比较两个 Top-N 集合的重合度，并原样保存双方输出。

注意：两边指标不同——CBA 是编译时间，我们是解析行数。这里比的是
「热点排序是否指向同一批头文件」，不是数值等价。结论必须按此解读。
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "experiments" / "_work" / "compile_commands.json"
PROJECT = ROOT / "experiments" / "_work" / "json"
CBA = ROOT / "experiments" / "_work" / "ClangBuildAnalyzer" / "build" / "ClangBuildAnalyzer.exe"
N = 25


def _args(cmd: str, out_obj: Path) -> list[str]:
    parts = shlex.split(cmd.replace("\\", "/"))
    out: list[str] = []
    i = 0
    while i < len(parts):
        if parts[i] == "-o":
            i += 2
            continue
        if parts[i] == "-c" or parts[i].startswith("-ftime-trace"):
            i += 1
            continue
        out.append(parts[i])
        i += 1
    # 编译器换成 clang++；-c -o 指向临时目录，避免污染工程。
    out[0] = "C:/Program Files/LLVM/bin/clang++.exe"
    # Windows 上没有 MSVC SDK（vcvars 依赖被沙箱拦截的 reg.exe），
    # 改用 clang + MinGW 工具链，用同一套 libstdc++ 头文件。
    out[1:1] = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]
    out += ["-c", "-ftime-trace", "-o", str(out_obj)]
    return out


def _headers_from_cba(text: str) -> list[str]:
    names = []
    for line in text.splitlines():
        m = re.match(r"^\s*\d+ ms: (.+?) \(included \d+ times", line)
        if m:
            names.append(Path(m.group(1).strip()).name)
    return names


def main() -> int:
    entries = json.loads(DB.read_text(encoding="utf-8"))[:N]
    tmp = Path(tempfile.mkdtemp(prefix="cba-"))
    subprocess.run([str(CBA), "--start", str(tmp)], check=True, capture_output=True)

    failures = []
    for i, entry in enumerate(entries):
        out_obj = tmp / f"u{i}.o"
        proc = subprocess.run(
            _args(entry["command"], out_obj),
            cwd=entry["directory"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            failures.append(Path(entry["file"]).name)

    cap = tmp / "capture.bin"
    subprocess.run([str(CBA), "--stop", str(tmp), str(cap)], check=True, capture_output=True)
    analysis = subprocess.run(
        [str(CBA), "--analyze", str(cap)], check=True, capture_output=True, text=True
    ).stdout
    cba_top = _headers_from_cba(analysis)[:10]

    # HeavyHeader 扫同一批 TU，用 clang。
    scan_out = tmp / "scan.json"
    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    subprocess.run(
        [
            sys.executable, "-m", "heavyheader", "scan", str(PROJECT),
            "--compile-commands", str(DB), "--max-tus", str(N),
            "--jobs", "1", "--format", "json",
            "-o", str(scan_out),
        ],
        check=True, capture_output=True, env=env, cwd=str(ROOT),
    )
    scan = json.loads(scan_out.read_text(encoding="utf-8"))
    rows = sorted(
        (h for h in scan["headers"] if h.get("kind") == "project"),
        key=lambda h: h["attributed_lines"],
        reverse=True,
    )
    hh_top = [Path(h["path"]).name for h in rows[:10]]

    inter = sorted(set(cba_top) & set(hh_top))
    payload = {
        "tus": N,
        "failed_compiles": failures,
        "cba_top10": cba_top,
        "hh_top10": hh_top,
        "overlap": inter,
        "overlap_count": len(inter),
        "analysis_head": analysis[:2000],
    }
    out_dir = ROOT / "experiments" / "results"
    md = out_dir / f"cba-compare-{date.today():%Y%m%d}.md"
    md.write_text(
        "\n".join(
            [
                f"# CBA vs HeavyHeader 同任务对照（{date.today().isoformat()}）",
                "",
                f"- 同一批 {N} 个 TU，clang++ 23.1.3，`-ftime-trace`",
                f"- 编译失败：{len(failures)}（{', '.join(failures[:3]) or '无'}）",
                "",
                "## Top-10 对照",
                "",
                "| # | ClangBuildAnalyzer（按时间） | HeavyHeader（按归因行数） |",
                "|---|---|---|",
                *[
                    f"| {i + 1} | {a} | {b} |"
                    for i, (a, b) in enumerate(zip(cba_top, hh_top))
                ],
                "",
                f"**重合 {len(inter)}/10**：{', '.join(inter) or '无'}",
                "",
                "## 解读边界",
                "",
                "- 两边指标不同：CBA 是编译时间，我们是解析行数。重合度只说明",
                "  「是否指向同一批热点头文件」，不能说明数值高低。",
                "- 不重合项需要逐个看：可能是时间与行数确实不同序（模板重头），",
                "  也可能是我们的归因口径问题。",
                "",
                "## CBA 原始输出（前 2000 字符）",
                "",
                "```",
                payload["analysis_head"],
                "```",
            ]
        ),
        encoding="utf-8",
    )
    md.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"written: {md}")
    print(f"overlap {len(inter)}/10 | failures {len(failures)}")
    print("CBA:", cba_top)
    print("HH :", hh_top)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
