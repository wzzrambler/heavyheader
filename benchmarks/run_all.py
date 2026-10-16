"""一键跑全部真实项目基准。

每个项目的 flags 都是实际跑通过、能产出干净数据的那组。写在这里而不是
让人记，因为下次跑要能复现——而「复现不了的数字」在评审眼里等于没测。

关于失败的编译单元：本项目刻意**不**为了凑数字而跳过它们。真实项目经常
需要 CMake 生成的配置头才能编译，本机没有 CMake，所以有些项目会有失败
单元。工具会把失败数如实报在报告顶部，基准脚本也一样处理。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Windows 上 "/tmp" 会解析成无盘符的 "\tmp"，必须用真实临时目录。
CLONE_DIR = Path(tempfile.gettempdir()) / "heavyheader-bench"

# CLI 可执行文件：优先找已安装的入口，其次退回 python -m。
_CLI = shutil.which("heavyheader") or [sys.executable, "-m", "heavyheader"]


@dataclass
class Case:
    name: str
    repo: str
    includes: list[str]
    std: str = "c++17"
    excludes: list[str] = field(default_factory=list)
    defines: list[str] = field(default_factory=list)
    max_tus: int = 25
    note: str = ""


CASES = [
    Case(
        name="fmt",
        repo="https://github.com/fmtlib/fmt.git",
        includes=["include"],
        excludes=["test"],
        note="src/fmt.cc 是 C++20 module，MinGW GCC 16.2 不支持 -fmodules，"
             "该单元失败属工具链限制而非工具缺陷。",
    ),
    Case(
        name="nlohmann-json",
        repo="https://github.com/nlohmann/json.git",
        includes=["include"],
        excludes=["test"],
        note="header-only 库，测试目录依赖 gtest，已排除。全部编译单元成功。",
    ),
    Case(
        name="spdlog",
        repo="https://github.com/gabime/spdlog.git",
        includes=["include"],
        excludes=["test", "bench"],
        defines=["SPDLOG_COMPILED_LIB"],
        note="src/*.cpp 需要 SPDLOG_COMPILED_LIB（平时由 CMake 定义），"
             "已用 -D 提供。bundled_fmtlib_format.cpp 等仍需完整构建配置。",
    ),
    Case(
        name="cpp-httplib",
        repo="https://github.com/yhirose/cpp-httplib.git",
        includes=["."],
        note="单体 httplib.h 达 23,343 行，是本组数据里最极端的成本热点样本。"
             "少数单元因缺少 OpenSSL 头失败。",
    ),
]


def clone(case: Case) -> Path:
    dest = CLONE_DIR / case.name
    if dest.is_dir():
        return dest
    print(f"  克隆 {case.repo} ...")
    subprocess.run(
        ["git", "clone", "--depth", "1", "-q", case.repo, str(dest)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return dest


def scan(case: Case, dest: Path) -> dict:
    out = ROOT / "benchmarks" / "results" / f"{case.name}-baseline.json"
    cmd = [
        *_CLI,
        "scan",
        str(dest),
        "--compiler", "g++",
        "--std", case.std,
        "--max-tus", str(case.max_tus),
        "--format", "json",
        "-o", str(out),
        "--save", str(out),
    ]
    for inc in case.includes:
        cmd += ["-I", str(dest / inc)]
    for exc in case.excludes:
        cmd += ["--exclude", exc]
    for define in case.defines:
        cmd += ["-D", define]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        raise RuntimeError(f"{case.name} 扫描失败：{result.stderr[-400:]}")
    return json.loads(out.read_text(encoding="utf-8"))


def summarise(case: Case, data: dict) -> dict:
    totals = data["totals"]
    project_headers = [h for h in data["headers"] if h["kind"] == "project"]
    top = sorted(project_headers, key=lambda h: -h["attributed_lines"])[:3]
    return {
        "name": case.name,
        "tu_ok": totals["translation_units"],
        "tu_failed": totals["failed_translation_units"],
        "edges": totals["dependency_edges"],
        "attributed_lines": totals["attributed_lines"],
        "candidates": len(data["candidates"]),
        "plausibly_unused": sum(
            1 for c in data["candidates"] if c.get("usage_label") == "疑似未使用"
        ),
        "top_headers": [
            {
                "path": h["path"].split(case.name)[-1].lstrip("\\/"),
                "lines": h["line_count"],
                "reach": h["tu_reach"],
                "attributed": h["attributed_lines"],
            }
            for h in top
        ],
        "note": case.note,
    }


def render(rows: list[dict]) -> str:
    lines = [
        "# 真实项目基准（横向对比）",
        "",
        "由 `python benchmarks/run_all.py` 生成。每个项目的 flags 都是实际跑通过、",
        "能产出干净数据的那组——写下来是为了下次能复现。",
        "",
        "## 汇总",
        "",
        "| 项目 | 编译单元 | 依赖边 | 头文件解析量 | 成本候选 | 疑似未使用 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        tu = f"{r['tu_ok']} 成功 / {r['tu_failed']} 失败"
        lines.append(
            f"| {r['name']} | {tu} | {r['edges']:,} | {r['attributed_lines']:,} "
            f"| {r['candidates']} | {r['plausibly_unused']} |"
        )
    lines.append("")

    total_attr = sum(r["attributed_lines"] for r in rows)
    total_ok = sum(r["tu_ok"] for r in rows)
    total_cand = sum(r["candidates"] for r in rows)
    total_unused = sum(r["plausibly_unused"] for r in rows)
    lines += [
        "## 合计",
        "",
        f"- 编译单元成功：**{total_ok}** 个",
        f"- 头文件解析量合计：**{total_attr:,}** 行",
        f"- 成本候选合计：**{total_cand}** 个，其中疑似未使用 **{total_unused}** 个",
        "",
        "## 各项目成本热点 Top 3",
        "",
    ]
    for r in rows:
        lines.append(f"### {r['name']}")
        lines.append("")
        lines.append("| 头文件 | 行数 | 被引用 TU | 归因行数 |")
        lines.append("|---|---:|---:|---:|")
        for h in r["top_headers"]:
            lines.append(
                f"| `{h['path']}` | {h['lines']:,} | {h['reach']} | {h['attributed']:,} |"
            )
        lines.append("")
        if r["note"]:
            lines.append(f"> {r['note']}")
            lines.append("")

    lines += [
        "## 读这些数字时要注意",
        "",
        "1. **失败的编译单元被如实报告，没有为了凑数字跳过。** 真实项目常需 CMake",
        "   生成的配置头才能编译，本机没有 CMake。工具把失败数报在报告顶部，",
        "   基准脚本同样处理。",
        "2. **具体数字随编译器版本、平台、负载变化，不应逐位复现。** 可复现的是",
        "   结论方向与数量级。",
        "3. **`plausibly_unused` 是启发式，不是安全性结论。** 权威判定需要 `verify`：",
        "   真的删掉、重新编译。",
        "",
        f"数据取自 GCC 16.2.0 (MinGW-w64) + Python 3.13，{total_ok} 个成功编译单元。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    CLONE_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for case in CASES:
        print(f"[{case.name}]")
        dest = clone(case)
        data = scan(case, dest)
        row = summarise(case, data)
        rows.append(row)
        print(
            f"  {row['tu_ok']} TU 成功 / {row['tu_failed']} 失败 · "
            f"{row['edges']:,} 边 · {row['attributed_lines']:,} 行 · "
            f"{row['candidates']} 候选"
        )

    report = render(rows)
    out = ROOT / "benchmarks" / "results" / "summary.md"
    out.write_text(report, encoding="utf-8")
    print(f"\n汇总已写入 {out}")


if __name__ == "__main__":
    main()
