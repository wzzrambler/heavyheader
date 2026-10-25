"""在 nlohmann/json 的单元测试上跑 PCH 实测闭环（`pch-verify`）。

为什么要单独一个脚本，而不是直接贴一条命令
--------------------------------------------
json 的单元测试不能拿 ``tests/src`` 当扫描根：那样 ``include/nlohmann/json.hpp``
会被判成**外部**头（不在 root 下），估算阶段一个项目头都选不出来，实测自然也无从
谈起。所以要把 ``include/`` 与 ``tests/`` 摆进**同一个根**——这个布局是实验的
一部分，写进脚本才不会被下次复现的人踩回去。

另一个坑是并行度：这 72 个单元每个都要实例化 doctest + json.hpp 的完整模板体，
默认 ``-j``（= CPU 核数）会让多个 g++ 同时吃掉几个 GB，机器开始换页，一次扫描从
两分钟变成几十分钟。脚本默认 ``-j 4``，并用 ``--max-units`` 控制串行实测的单元数。

用法
----
::

    python experiments/pch_verify_json.py                 # 24 单元，每侧 2 次
    python experiments/pch_verify_json.py --units 12 --runs 1
    python experiments/pch_verify_json.py --report-only   # 只打印上次的结果

产物落在 ``experiments/_work/pch-verify-json/``（语料副本 + 报告 + ``.gch``）。
``--report-only`` 只重读已存在的报告。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SOURCE = HERE / "_work" / "json"
OUT = HERE / "_work" / "pch-verify-json"

# 与 experiments/run_experiment.py 里 json 那一档完全一致的编译选项。
FLAGS = [
    "--compiler",
    "g++",
    "--std",
    "c++11",
    "--flag=-O0",
    "-D",
    "DOCTEST_CONFIG_SUPER_FAST_ASSERTS",
    "-D",
    "JSON_TEST_KEEP_MACROS",
    "-D",
    "JSON_TEST_USING_MULTIPLE_HEADERS=0",
]
INCLUDE_DIRS = ("include", "tests", "tests/thirdparty/doctest", "tests/src")


def build_corpus(destination: Path) -> Path:
    """把 json 需要的部分摆进同一个根，让项目头真的是「项目」头。"""
    if not SOURCE.is_dir():
        raise SystemExit(
            f"找不到语料 {SOURCE}。先跑 `python experiments/fetch.py` 把 json 取下来。"
        )
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "tests" / "src").mkdir(parents=True)
    (destination / "tests" / "thirdparty").mkdir(parents=True)
    shutil.copytree(SOURCE / "include", destination / "include")
    shutil.copytree(
        SOURCE / "tests" / "thirdparty" / "doctest",
        destination / "tests" / "thirdparty" / "doctest",
    )
    for item in (SOURCE / "tests" / "src").iterdir():
        if item.is_file():
            shutil.copy2(item, destination / "tests" / "src" / item.name)
    for item in (SOURCE / "tests").iterdir():
        if item.is_file() and item.suffix in {".h", ".hpp"}:
            shutil.copy2(item, destination / "tests" / item.name)
    return destination


def command(root: Path, units: int, runs: int, jobs: int) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "heavyheader",
        "pch-verify",
        str(root),
        *FLAGS,
        "--max-tus",
        "72",
        "--max-units",
        str(units),
        "--runs",
        str(runs),
        "-j",
        str(jobs),
        "--workdir",
        str(OUT / "pch"),
    ]
    for name in INCLUDE_DIRS:
        argv += ["-I", str(root / name)]
    return argv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--units", type=int, default=24, help="实测多少个编译单元")
    parser.add_argument("--runs", type=int, default=2, help="每侧重复编译次数")
    parser.add_argument("--jobs", type=int, default=4, help="扫描阶段的并行编译数")
    parser.add_argument("--json", action="store_true", help="再跑一遍导出 JSON（等于重做一轮实测）")
    parser.add_argument("--report-only", action="store_true", help="只重读已有报告")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    report = OUT / "report.md"

    if args.report_only:
        if not report.is_file():
            raise SystemExit(f"还没有报告：{report}")
        print(report.read_text(encoding="utf-8"))
        return 0

    root = build_corpus(OUT / "corpus")
    argv = command(root, args.units, args.runs, args.jobs)
    print("复现命令：\n  " + " ".join(f'"{a}"' if " " in a else a for a in argv))
    # 只跑一次：一次就是 24 单元 × 2 侧 × 2 遍编译。要 JSON 再加 --json——
    # 默认把这一遍也跑掉，等于为了另一种输出格式把整轮实测重做一遍。
    subprocess.run([*argv, "--format", "md", "-o", str(report)], cwd=str(REPO), check=False)
    if args.json:
        subprocess.run(
            [*argv, "--format", "json", "-o", str(OUT / "report.json")],
            cwd=str(REPO),
            check=False,
        )
    print(f"\n报告：{report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
