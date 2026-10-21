"""闸门的统计口径错了：判「在不在用」时只看拥有 ``#include`` 行的那个文件，
不看**会因为这个 include 消失而失去符号**的那些文件。

为什么怀疑这一点
----------------
G2 的 16 条闸门候选里有 8 条编译不过，报错长这样::

    context.cpp:524:18: error: no member named 'sort' in namespace 'std'
    traversal.h:18:10: error: no template named 'vector' in namespace 'std'

``sort`` / ``vector`` 写在**别的文件**里：那一行 ``#include <algorithm>`` 在
``prelude.h``，用的是 ``context.cpp``。闸门只读 ``prelude.h`` 的正文，自然
判「没人用」，于是放行了一条注定会挂的改动。这不是启发式不够聪明，而是
**问错了人**——要问的不是「写这一行的文件用不用」，而是「所有靠这一行拿到
这个头的文件里，有没有人在用」。

本脚本只做测量，不改产品代码。对同一批 include 行算三种判定:

* ``owner``        —— 现状：只看 site_file 自身正文。
* ``consumer_any`` —— 看所有受影响文件（使用方），任一判 IN_USE 就拦。
* ``consumer_all`` —— 同上，但 UNKNOWN 也拦（更保守的变体）。

同时打印「谁触发了拦截」「命中的是什么标识符」，让每条判定都能被核对。

用法::

    python experiments/gate_scope.py --project experiments/_work/doctest \
        --db experiments/_work/doctest/compile_commands.json --tus 24
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.graph import HeaderSizes, build_header_stats, classify_headers  # noqa: E402
from heavyheader.attrib.sites import (  # noqa: E402
    _closures,
    build_include_graph,
    tu_parsed_files,
)
from heavyheader.attrib.usage import (  # noqa: E402
    IdentifierIndex,
    UsageVerdict,
    _read,
    analyse_usage,
)
from decl_probe import declared_names  # noqa: E402
from heavyheader.drivers.base import detect_driver  # noqa: E402
from heavyheader.models import HeaderKind  # noqa: E402
from heavyheader.scanner import ScanOptions, scan_project  # noqa: E402

CLANG = "C:/Program Files/LLVM/bin/clang++.exe"
TARGET_FLAGS = ["--target=x86_64-w64-windows-gnu", "--gcc-toolchain=C:/mingw64"]

# doctest 上一轮实测编译不过的 8 对（site 文件名, header 名），来自
# experiments/results/g2-sites-doctest-all16-20261018.json。
DOCTEST_BROKEN = {
    ("prelude.h", "algorithm"),
    ("prelude.h", "vector"),
    ("prelude.h", "set"),
    ("prelude.h", "iomanip"),
    ("prelude.h", "atomic"),
    ("prelude.h", "map"),
    ("prelude.h", "iostream"),
    ("doctest_fwd.h", "debugger.h"),
}
# 经复测确认省时的两条（doctest / zlib），改口径不能把它们弄丢。
KNOWN_WINS = {
    ("prelude.h", "unordered_set"),
    ("zutil.h", "stdlib.h"),
}


def collect(scan, project: Path, index=None):
    """复刻 find_header_candidates 的枚举，但不做闸门判定。"""
    sizes = HeaderSizes()
    build_header_stats(scan, sizes)
    kinds = classify_headers(project, {h for tu in scan.ok_tus for h in tu.dependencies})
    index = index or IdentifierIndex()

    children = build_include_graph(scan)
    closures = _closures(children)
    parsed = tu_parsed_files(scan, closures)
    tu_sources = set(parsed)
    all_parsed: set[Path] = set()
    for files in parsed.values():
        all_parsed |= files

    lines_by_edge = {}
    for tu in scan.ok_tus:
        for event in tu.include_events:
            key = (event.includer, event.included)
            if key not in lines_by_edge or (lines_by_edge[key] is None and event.line):
                lines_by_edge[key] = event.line

    rows = []
    for site_file, directs in children.items():
        if site_file in tu_sources:
            continue
        if kinds.get(site_file) == HeaderKind.SYSTEM:
            continue
        affected = sorted(s for s, files in parsed.items() if site_file in files)
        if len(affected) < 2:
            continue
        for header in directs:
            shared: set[Path] = set()
            for other in directs:
                if other == header:
                    continue
                shared |= closures.get(other, frozenset())
            shared.discard(site_file)
            exclusive = set(closures.get(header, frozenset()))
            if header not in shared:
                exclusive.add(header)
            exclusive -= shared
            if not exclusive:
                continue
            saved = sizes.total(exclusive)
            if saved <= 0:
                continue
            rows.append(
                {
                    "site_file": site_file,
                    "header": header,
                    "exclusive": exclusive,
                    "affected": affected,
                    "saved": saved,
                    "line": lines_by_edge.get((site_file, header)),
                }
            )
    return rows, parsed, closures, all_parsed, index


def shadowed_names(exclusive: set[Path], index, freq: dict[str, int]) -> set[str]:
    """只由这个消失集合提供的名字。

    判据：``全项目声明它的文件数 == 消失集合里声明它的文件数``。相等意味着
    删掉这一行之后，这个名字在任何被这些 TU 解析过的地方都不再出现——它才是
    「删了就会坏」的证据。

    为什么需要它：使用方文件的正文很大，任何大正文都会提到 ``at`` / ``size``
    / ``type`` 这类标准库环境词，而它们由**别的**头提供。上一轮实测
    ``prelude.h -> <unordered_set>`` 被判「在用」，证据是 ``data.cpp`` 里的
    ``at``——可 ``at`` 是 ``<string>``/``<vector>`` 提供的，跟 ``<unordered_set>``
    无关。用「全体声明者都在消失集合里」这一条，可以无歧义地把这种命中排除掉。
    """
    inside: dict[str, int] = {}
    for path in exclusive:
        for name in index.header_ids(path):
            inside[name] = inside.get(name, 0) + 1
    return {name for name, count in inside.items() if freq.get(name, 0) == count}


def consumer_verdict(row, consumers, index, freq) -> dict:
    """使用方口径：任一使用方用了「只由消失集合提供」的名字，就判在用。"""
    doomed = shadowed_names(row["exclusive"], index, freq)
    for path in consumers:
        ids = index.source_ids(path)
        if not ids:
            continue
        hits = ids & doomed
        if hits:
            return {"verdict": "in_use", "file": str(path), "ids": sorted(hits)[:5]}
    return {"verdict": "plausibly_unused", "file": None, "ids": []}


class DeclIndex(IdentifierIndex):
    """用「声明作用域」取名字的索引（原型，见 experiments/decl_probe.py）。"""

    __slots__ = ()

    def header_ids(self, path: Path) -> frozenset[str]:
        cached = self._headers.get(path)
        if cached is not None:
            return cached
        text = _read(path)
        ids = frozenset(declared_names(text)) if text else frozenset()
        self._headers[path] = ids
        return ids


def consumers_of(row, closures, all_parsed, parsed) -> list[Path]:
    """会因为这一行消失而看不见 header 的文件：使用方 + 受影响 TU 的源文件。"""
    site_file = row["site_file"]
    found = {tu for tu in row["affected"]}
    for path in all_parsed:
        if site_file in closures.get(path, frozenset()):
            found.add(path)
    return sorted(p for p in found if p.is_file())


def global_frequency(all_parsed: set[Path], index) -> dict[str, int]:
    """一个名字在全项目多少个头文件里出现。

    用来回答「``sort`` 与 ``at`` 能不能分开」：如果两者在全项目里的出现次数
    差不多，那「按普遍性过滤」这条路就走不通，只能换判据。
    """
    from collections import Counter

    freq: Counter[str] = Counter()
    for path in all_parsed:
        for name in index.header_ids(path):
            freq[name] += 1
    return dict(freq)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--tus", type=int, default=24)
    parser.add_argument("--compiler", default=CLANG)
    parser.add_argument("--tag", default="")
    parser.add_argument("--ids", default="tokens", choices=["tokens", "decls"],
                        help="取名字的口径：tokens=正文里出现过的词（现状）/ decls=声明作用域")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    db = Path(args.db).resolve()
    driver = detect_driver(args.compiler)
    started = time.perf_counter()
    scan = scan_project(
        ScanOptions(
            root=project,
            compiler=args.compiler,
            compile_commands=db,
            max_tus=args.tus,
            jobs=1,
            extra_flags=list(TARGET_FLAGS),
        ),
        driver=driver,
    )
    if not scan.ok_tus:
        print("扫描没有成功单元")
        return 1
    scan_seconds = time.perf_counter() - started

    index = DeclIndex() if args.ids == "decls" else IdentifierIndex()
    rows, parsed, closures, all_parsed, index = collect(scan, project, index)
    freq = global_frequency(all_parsed, index)
    print(f"TU={len(scan.ok_tus)}  可枚举行={len(rows)}  扫描 {scan_seconds:.1f}s")
    print(f"参与统计的已解析文件 {len(all_parsed)} 个，不同标识符 {len(freq)} 个")

    out = []
    for row in rows:
        site_file, header = row["site_file"], row["header"]
        owner = analyse_usage(site_file, header, closure=row["exclusive"], index=index)
        consumers = consumers_of(row, closures, all_parsed, parsed)
        verdicts = []
        trigger = None
        for path in consumers:
            signal = analyse_usage(path, header, closure=row["exclusive"], index=index)
            verdicts.append({"file": str(path), "verdict": signal.verdict.value,
                             "ids": list(signal.shared_identifiers[:5]),
                             "provided_by": signal.provided_by.name if signal.provided_by else None,
                             "freq": {n: freq.get(n, 0) for n in signal.shared_identifiers[:5]}})
            if trigger is None and signal.verdict is UsageVerdict.IN_USE:
                trigger = verdicts[-1]
        any_in_use = any(v["verdict"] == "in_use" for v in verdicts)
        any_unknown = any(v["verdict"] == "unknown" for v in verdicts)
        unknown_trigger = next((v for v in verdicts if v["verdict"] == "unknown"), None)
        shadow = consumer_verdict(row, consumers, index, freq)
        try:
            site_rel = str(site_file.relative_to(project))
        except ValueError:
            site_rel = str(site_file)
        out.append(
            {
                "site": site_rel,
                "site_name": site_file.name,
                "header": header.name,
                "line": row["line"],
                "reach": len(row["affected"]),
                "saved": row["saved"],
                "amplified": row["saved"] * len(row["affected"]),
                "owner": owner.verdict.value,
                "owner_ids": list(owner.shared_identifiers[:5]),
                "consumers": len(consumers),
                "any_in_use": any_in_use,
                "any_unknown": any_unknown,
                "trigger": trigger,
                "unknown_trigger": unknown_trigger,
                "shadow": shadow,
                "was_broken": (site_file.name, header.name) in DOCTEST_BROKEN,
                "known_win": (site_file.name, header.name) in KNOWN_WINS,
            }
        )

    owner_pass = [r for r in out if r["owner"] == "plausibly_unused"]
    any_pass = [r for r in out if not r["any_in_use"]]
    all_pass = [r for r in out if not r["any_in_use"] and not r["any_unknown"]]
    shadow_pass = [r for r in out if r["shadow"]["verdict"] == "plausibly_unused"]

    print(f"闸门开（owner 口径，现状）: {len(owner_pass)} 条")
    print(f"使用方口径，IN_USE 就拦   : {len(any_pass)} 条")
    print(f"使用方口径，UNKNOWN 也拦 : {len(all_pass)} 条")
    print(f"使用方 + 遮蔽判据         : {len(shadow_pass)} 条")
    print("\n遮蔽判据下放行的（按累计行）：")
    for r in sorted(shadow_pass, key=lambda r: -r["amplified"])[:20]:
        print(f"  {r['site_name']:18s} -> {r['header']:24s} 累计 {r['amplified']:8d} 行 "
              f"（{r['was_broken'] and '上轮编不过' or ''}{r['known_win'] and '经复测省时' or ''}）")

    shadow_blocked = [r for r in out if r["owner"] == "plausibly_unused" and r["shadow"]["verdict"] != "plausibly_unused"]
    print("\nowner 口径放行、遮蔽判据拦下的：")
    for r in shadow_blocked:
        s = r["shadow"]
        print(f"  {r['site_name']:18s} -> {r['header']:24s} "
              f"{Path(s['file']).name if s['file'] else '-'}: {','.join(s['ids'])}"
              f"{'   ← 上轮实测编不过' if r['was_broken'] else ''}"
              f"{'   ← 上轮实测省时（会丢）' if r['known_win'] else ''}")

    flagged = [r for r in out if r["was_broken"]]
    if flagged:
        print("\n上一轮实测编译不过的 8 条，新口径下还放行吗：")
        for r in flagged:
            owner_v = r["owner"]
            new = "放行" if not r["any_in_use"] else f"拦下（{Path(r['trigger']['file']).name}: {','.join(r['trigger']['ids'])}）"
            print(f"  {r['site_name']:16s} -> {r['header']:16s} owner={owner_v:16s} 使用方={new}")

    wins = [r for r in out if r["known_win"]]
    if wins:
        print("\n经复测确认省时的改动，新口径下还放行吗：")
        for r in wins:
            new = "放行" if not r["any_in_use"] else f"拦下（{Path(r['trigger']['file']).name}: {','.join(r['trigger']['ids'])}）"
            print(f"  {r['site_name']:16s} -> {r['header']:16s} owner={r['owner']:16s} 使用方={new}")

    # 能不能用「这个名字在全项目有多常见」把真依赖和环境噪音分开？
    # 真依赖的例子：sort / vector / setw / ostream（删了就编不过）。
    # 噪音的例子：at / insert / type / value（上一轮实测这些候选放行后省时为 0）。
    true_names = {"sort", "vector", "set", "setw", "atomic", "map", "ostream",
                  "unordered_set", "malloc", "free", "atoi", "strtoul", "abort"}
    hits: dict[str, int] = {}
    for r in out:
        for v in [r["trigger"], r["unknown_trigger"]]:
            if not v:
                continue
            for name in v["ids"]:
                hits.setdefault(name, freq.get(name, 0))
    if hits:
        print("\n名字在全项目多少个文件里出现（真依赖 vs 噪音）：")
        for name, count in sorted(hits.items(), key=lambda kv: kv[1]):
            tag = "真依赖" if name in true_names else "噪音?"
            print(f"  {name:24s} {count:5d} 个文件   {tag}")

    payload = {
        "date": date.today().isoformat(),
        "project": project.name,
        "tus": len(scan.ok_tus),
        "scan_seconds": round(scan_seconds, 2),
        "enumerable_rows": len(rows),
        "pass": {
            "owner": len(owner_pass),
            "consumer_any": len(any_pass),
            "consumer_all": len(all_pass),
        },
        "name_frequency": hits,
        "rows": out,
    }
    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{args.tag}" if args.tag else ""
    stem = out_dir / f"gate-scope-{args.ids}-{project.name}{suffix}-{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    def mark(r) -> str:
        if r["owner"] == "plausibly_unused" and not r["any_in_use"]:
            return "两口径都放行"
        if r["owner"] == "plausibly_unused" and r["any_in_use"]:
            return "**放行是错的**（使用方在用）"
        if r["owner"] != "plausibly_unused" and not r["any_in_use"]:
            return "owner 拦下，使用方没人用（现状偏保守）"
        return "两口径都拦"

    lines = [
        f"# 闸门的统计口径：只看 owner 还是看使用方（{payload['date']}，{payload['project']}）",
        "",
        f"- 工程：{payload['project']}，{payload['tus']} 个 TU，可枚举 include 行 {payload['enumerable_rows']} 条。",
        f"- 放行条数：owner 口径 {len(owner_pass)} / 使用方(IN_USE) {len(any_pass)} / 使用方(+UNKNOWN) {len(all_pass)}。",
        "",
        "| 改动位置 | 头文件 | 行 | 受影响 TU | 累计行 | owner 判定 | 使用方人数 | 使用方口径 | 说明 |",
        "|---|---|---:|---:|---:|---|---:|---|---|",
        *[
            f"| `{r['site']}` | `{r['header']}` | {r['line'] if r['line'] is not None else '-'} | "
            f"{r['reach']} | {r['amplified']} | {r['owner']} | {r['consumers']} | "
            f"{'拦下' if r['any_in_use'] else '放行'} | {mark(r)} |"
            for r in out
        ],
        "",
    ]
    stem.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written: {stem.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
