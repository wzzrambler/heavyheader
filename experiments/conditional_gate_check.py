"""条件编译类候选的等价性复核：删掉之后**程序**变没变？

候选精度基准的真值只有两条：编不编得过、少解析几个文件。这两条都不回答
「编译出来的还是不是同一个程序」。把「只用于 ``#ifdef`` 存在性判断」的宏
降级成「不算依赖」会让一批候选从「在用」变成「疑似未使用」——但如果那个宏
真的被定义着，删掉包含它的头就会**静默改变**哪一段代码被编译进去：编译照样
通过，行为悄悄变了。

这个脚本用代码等价闸门（``-S`` 汇编指纹）复核这类候选：编得过 ≠ 没变。

用法::

    python experiments/conditional_gate_check.py --db <compile_commands.json> \
        --compiler <exe> --file <源文件> --line <include 行号>
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heavyheader.codegen import compare  # noqa: E402
from heavyheader.drivers.base import DriverOptions, detect_driver  # noqa: E402
from heavyheader.scanner import _split_command, _strip_output_flag, _strip_source_token  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--file", required=True, help="源文件路径（可与库里的写法不同）")
    parser.add_argument("--line", type=int, required=True)
    args = parser.parse_args(argv)

    entries = json.loads(Path(args.db).read_text(encoding="utf-8"))
    target = Path(args.file).resolve()
    entry = next(e for e in entries if Path(e["file"]).resolve() == target)
    argv = _strip_source_token(
        _strip_output_flag(_split_command(entry["command"])), str(entry["file"])
    )
    options = DriverOptions(extra_flags=argv[1:])

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    patched_text = "".join(
        "// " + line if i == args.line else line for i, line in enumerate(lines, 1)
    )
    print(f"改动行     : {lines[args.line - 1].strip()}")

    with tempfile.TemporaryDirectory(prefix="hh-cg-") as tmp:
        work = Path(tmp)
        patched = work / "patched"
        patched.mkdir()
        patched_file = patched / target.name
        patched_file.write_text(patched_text, encoding="utf-8")
        driver = detect_driver(args.compiler)
        before, after = compare(
            driver,
            target,
            patched_file,
            options,
            Path(entry["directory"]),
            work / "stage",
        )

    print(f"源文件     : {target.name}")
    print(f"注释掉第   : {args.line} 行")
    print(f"原始指纹   : ok={before.ok} {before.digest or before.note}")
    print(f"删除后指纹 : ok={after.ok} {after.digest or after.note}")
    if not (before.ok and after.ok):
        print("结论       : 指纹没取到，无法判定")
        return 1
    same = before.digest == after.digest
    print("结论       :", "汇编一致（程序没变）" if same else "**汇编不同：程序被静默改变了**")
    return 0 if same else 2


if __name__ == "__main__":
    raise SystemExit(main())
