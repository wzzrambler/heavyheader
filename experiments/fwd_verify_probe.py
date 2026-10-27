"""前向声明实测闭环（`forward-verify`）的语料探针 + 机制验证。

两件事，分开报
--------------
1. **真实语料上有多少候选**：json（63 TU）、spdlog v1.14.1（整棵树）、
   ``benchmarks/cases/demo``。这一步只扫描，不实测——候选数为 0 时没有可测的东西，
   而「0 候选」本身就是结论：文本判据的前置条件在现代 C++ 里很少成立。
2. **机制本身对不对**：一个合成语料，刻意做成前向声明收益最高的经典形状
   （头里只有类的声明 + 一个 1,500 行的负载头，单元只把类当指针用）。

合成语料能证明「机制跑得通」，**不能**证明「真实工程能省多少」——两者在归档里
必须分开写，否则合成语料会被当成效能证据。

用法::

    python experiments/fwd_verify_probe.py            # 全部
    python experiments/fwd_verify_probe.py --real     # 只跑真实语料的候选计数
    python experiments/fwd_verify_probe.py --demo     # 只跑合成语料的实测

产物落在 ``experiments/_work/fwd-verify/``。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORK = HERE / "_work"
OUT = WORK / "fwd-verify"

#: 合成语料里负载头的行数。取与 fragile-demo 同量级：足够让「省了多少」明显，
#: 又不至于让一次实测跑成几分钟。
PAYLOAD_LINES = 1500


def _scan(root: Path, includes: list[str]):
    """跑一次扫描（进程内），返回 ProjectScan。"""
    from heavyheader.drivers.base import detect_driver
    from heavyheader.scanner import ScanOptions, scan_project

    options = ScanOptions(
        root=root,
        compiler="g++",
        standard="c++17",
        include_dirs=[root / inc for inc in includes],
        forward_decl=True,
        jobs=4,
    )
    return scan_project(options, driver=detect_driver("g++"))


# --------------------------------------------------------------------------
# 1. 真实语料：有多少候选
# --------------------------------------------------------------------------

_JSON_INCLUDES = ("include", "tests", "tests/thirdparty/doctest", "tests/src")
_JSON_FLAGS = [
    "--flag=-O0",
    "-D",
    "DOCTEST_CONFIG_SUPER_FAST_ASSERTS",
    "-D",
    "JSON_TEST_KEEP_MACROS",
    "-D",
    "JSON_TEST_USING_MULTIPLE_HEADERS=0",
]


def _scan_json():
    from heavyheader.drivers.base import detect_driver
    from heavyheader.scanner import ScanOptions, scan_project

    root = WORK / "pch-verify-json" / "corpus"
    if not root.is_dir():
        return None
    options = ScanOptions(
        root=root,
        compiler="g++",
        standard="c++11",
        include_dirs=[root / inc for inc in _JSON_INCLUDES],
        defines=[
            "DOCTEST_CONFIG_SUPER_FAST_ASSERTS",
            "JSON_TEST_KEEP_MACROS",
            "JSON_TEST_USING_MULTIPLE_HEADERS=0",
        ],
        extra_flags=["-O0"],
        forward_decl=True,
        jobs=4,
    )
    return scan_project(options, driver=detect_driver("g++"))


def probe_real() -> list[dict]:
    rows: list[dict] = []

    root = WORK / "pch-verify-json" / "corpus"
    if root.is_dir():
        scan = _scan_json()
        rows.append(_row("nlohmann/json v3.11.3（63 TU 语料）", scan))
    else:
        print("跳过 json：语料不在 _work/pch-verify-json/corpus", file=sys.stderr)

    root = WORK / "spdlog"
    if root.is_dir():
        try:
            scan = _scan(root, ["include", "tests"])
            rows.append(_row("spdlog v1.14.1（整棵树）", scan))
        except Exception as exc:  # noqa: BLE001 - 探针不该因语料崩掉
            print(f"spdlog 扫描失败：{exc}", file=sys.stderr)
    else:
        print("跳过 spdlog：语料不在 _work/spdlog", file=sys.stderr)

    demo = REPO / "benchmarks" / "cases" / "demo"
    if demo.is_dir():
        scan = _scan(demo, ["include"])
        rows.append(_row("benchmarks/cases/demo", scan))

    return rows


def _row(label: str, scan) -> dict:
    return {
        "corpus": label,
        "tus_ok": len(scan.ok_tus),
        "candidates": len(scan.forward_candidates or []),
        "blocking": sum(
            1 for c in (scan.forward_candidates or []) if c.blocking_types
        ),
    }


# --------------------------------------------------------------------------
# 2. 合成语料：机制验证
# --------------------------------------------------------------------------


def build_corpus(root: Path) -> Path:
    """经典形状：头里只有类的声明，单元只拿它当指针用。"""
    if root.exists():
        shutil.rmtree(root)
    include = root / "include"
    src = root / "src"
    include.mkdir(parents=True)
    src.mkdir(parents=True)

    # 负载头：与前向声明无关，只用来看「省了多少」。
    # 刻意让它依赖几个真实的标准库头：纯内联小函数的解析成本太低，省下来的
    # 那十几毫秒会落在噪声地板附近，信号读不出来。
    payload = [
        "#pragma once",
        "#include <string>",
        "#include <vector>",
        "#include <map>",
        "#include <algorithm>",
        "namespace demo {",
    ]
    payload += [
        f"inline std::string payload_{i}(const std::string& x) {{ "
        f'return x + "{i}"; }}'
        for i in range(PAYLOAD_LINES // 4)
    ]
    payload.append("}")
    (include / "payload.h").write_text("\n".join(payload) + "\n", encoding="utf-8")

    (include / "widget.h").write_text(
        "#pragma once\n"
        '#include "payload.h"\n'
        "namespace ui {\n"
        "class Widget {\n"
        " public:\n"
        "  int width() const { return static_cast<int>(demo::payload_0(\"x\").size()); }\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )

    # (a) 只当指针用 —— 前向声明应当成立。
    (src / "a.cpp").write_text(
        '#include "widget.h"\n'
        "int measure(ui::Widget* w) { return w ? 1 : 0; }\n",
        encoding="utf-8",
    )

    return root


def add_smart_ptr_unit(root: Path) -> Path:
    """对照：同一个头，换一个单元改用 ``std::shared_ptr<Widget>``。

    这一条用来确认规则的**保守方向**：新增的单元不该凭空多出一条建议。
    ``shared_ptr`` 在函数定义里销毁时要实例化删除器，那时 ``Widget`` 必须完整，
    所以这个单元本来就不该被建议前向声明。
    """
    src = root / "blind"
    src.mkdir(parents=True, exist_ok=True)
    (src / "c.cpp").write_text(
        '#include "widget.h"\n'
        "#include <memory>\n"
        "int measure(std::shared_ptr<ui::Widget> w) { return w ? 1 : 0; }\n",
        encoding="utf-8",
    )
    return root


def run_demo() -> dict:
    from heavyheader.attrib.fwdverify import run_forward_trial
    from heavyheader.drivers.base import detect_driver
    from heavyheader.scanner import ScanOptions, scan_project

    root = build_corpus(OUT / "demo")
    scan = scan_project(
        ScanOptions(
            root=root,
            compiler="g++",
            standard="c++17",
            include_dirs=[root / "include"],
            forward_decl=True,
            jobs=2,
        ),
        driver=detect_driver("g++"),
    )
    pointer_only = {
        "candidates": len(scan.forward_candidates or []),
        "tus_ok": len(scan.ok_tus),
    }
    trial = run_forward_trial(scan, driver=detect_driver("g++"), runs=2)

    # 规则盲区：加了 shared_ptr 之后，同一个头在 c.cpp 里就变成「需要完整定义」。
    add_smart_ptr_unit(root)
    scan2 = scan_project(
        ScanOptions(
            root=root,
            compiler="g++",
            standard="c++17",
            include_dirs=[root / "include"],
            forward_decl=True,
            jobs=2,
        ),
        driver=detect_driver("g++"),
    )
    smart_ptr_control = {
        "tus_ok": len(scan2.ok_tus),
        "candidates": len(scan2.forward_candidates or []),
        "blocking": sum(1 for c in (scan2.forward_candidates or []) if c.blocking_types),
    }

    return {
        "pointer_only": pointer_only,
        "trial": trial.to_dict(),
        "smart_ptr_control": smart_ptr_control,
    }


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="只跑真实语料的候选计数")
    parser.add_argument("--demo", action="store_true", help="只跑合成语料的实测")
    args = parser.parse_args(argv)
    both = not (args.real or args.demo)

    OUT.mkdir(parents=True, exist_ok=True)
    result: dict = {}

    if both or args.real:
        result["real"] = probe_real()
        print("真实语料候选计数：")
        for row in result["real"]:
            print(
                f"  {row['corpus']}: {row['tus_ok']} TU, "
                f"{row['candidates']} 候选（其中 {row['blocking']} 个含阻塞类型）"
            )

    if both or args.demo:
        result["demo"] = run_demo()
        trial = result["demo"]["trial"]
        print("合成语料实测：")
        print(
            f"  {trial['measured_units']} 条被测，{trial['equivalent_units']} 条已证等价，"
            f"省 {trial['equivalent_saved_seconds']:.3f} s"
            f"（{trial['equivalent_saved_percent']:.1f}%）"
        )

    (OUT / "probe.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"结果写入 {OUT / 'probe.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
