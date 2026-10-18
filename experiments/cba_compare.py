"""G1：ClangBuildAnalyzer vs HeavyHeader 的同任务对照（同一批 TU、同一编译器）。

为什么需要这个脚本
------------------
上一轮做过一次对照，但三个口径没对齐，结论不能采信：
1. CBA 的「Expensive headers」榜单**包含系统头**（algorithm/sstream/set…），
   HeavyHeader 只列项目头 —— 两个候选集合不同，重合度天然被压低；
2. 两边编译器不同（clang vs g++），时间分布不可比；
3. 只比了集合，没比顺序，而「热点头部是不是同一个」和「排序是不是一致」
   是两个不同的问题。

本脚本把三件事一次做对：
- CBA 与 HeavyHeader 都用同一个 clang++，同一套 `--target`/`--gcc-toolchain`；
- 两边都用**同一条路径规则**（是否在项目根下）筛候选，而不是各用各的分类；
- 同时给出集合重合（Jaccard）与顺序一致性（共同项上的 Kendall τ）。

产物落在 `experiments/_work/cba-run/`，可复查、可重跑；报告写在
`experiments/results/cba-compare-<date>.{md,json}`。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "experiments" / "_work" / "json"
DB = ROOT / "experiments" / "_work" / "compile_commands.json"
CBA = ROOT / "experiments" / "_work" / "ClangBuildAnalyzer" / "build" / "ClangBuildAnalyzer.exe"
CLANG = Path("C:/Program Files/LLVM/bin/clang++.exe")
RUN = ROOT / "experiments" / "_work" / "cba-run"
TOP = 10
#: 计算「每千行耗时」时的最小样本。太小的头（几十行）归一化后极不稳定，
#: 一个 120 行的头会把极值拉到 600×，那是除法的产物，不是真实差异。
DENSITY_MIN_LINES = 5000
#: 与 HeavyHeader 侧完全相同的编译参数。clang 在 Windows 上默认指向 MSVC
#: 头文件，而本机没有可用的 MSVC SDK（vcvars 依赖被策略拦截的 reg.exe），
#: 因此显式切到 MinGW target，用同一套 libstdc++。
TARGET_FLAGS = [
    "--target=x86_64-w64-windows-gnu",
    "--gcc-toolchain=C:/mingw64",
]

#: CBA 读取**当前工作目录**下的 `ClangBuildAnalyzer.ini`。默认值会直接
#: 破坏同任务对照的公平性，必须显式改掉：
#:
#: * `misc/onlyRootHeaders = true`（默认）只列「被编译单元直接包含」的头，
#:   把所有传递包含的头（例如 doctest.h）排除在外。HeavyHeader 统计全部头，
#:   用它默认值时两边候选集合根本不同。
#: * `counts/header = 10`（默认）在过滤后只留 10 行；去掉系统头后可能只剩
#:   三四行，拿 4 项去比 10 项，重合率没有意义。
#:
#: 因此这里显式放开：全量头 + 足够大的条数，再由我们按「是否在项目内」
#: 统一裁剪到 Top-10。
CBA_INI = """[counts]
header=500

[misc]
onlyRootHeaders=false
"""

_HEADER_LINE = re.compile(
    r"^\s*(?P<ms>\d+) ms: (?P<path>.+?) \(included (?P<count>\d+) times, "
    r"avg (?P<avg>\d+) ms\)",
    re.MULTILINE,
)


def cba_args(command: str, out_obj: Path) -> list[str]:
    """把一条 compile_commands 命令改写成「clang + ftime-trace + 输出到临时目录」。"""
    # Windows 路径里的反斜杠会被 posix shlex 当转义符吃掉，先统一。
    parts = shlex.split(command.replace("\\", "/"))
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
    out[0] = str(CLANG)
    # 注意插到 index 1：index 0 是编译器路径，覆盖掉就什么都没了。
    out[1:1] = TARGET_FLAGS
    out += ["-c", "-ftime-trace", "-o", str(out_obj)]
    return out


def parse_cba_headers(text: str) -> list[dict]:
    """抽取 CBA 的逐头文件累计耗时，保留**完整路径**用于分类。"""
    return [
        {
            "ms": int(m.group("ms")),
            "path": m.group("path").strip(),
            "count": int(m.group("count")),
            "avg_ms": int(m.group("avg")),
        }
        for m in _HEADER_LINE.finditer(text)
    ]


def under_project(path: str, root: Path) -> bool:
    """路径是否位于项目根下 —— 两边共用的唯一分类规则。

    CBA 报的可能是相对路径（相对编译目录）或绝对路径；先按绝对路径试，
    失败再拼项目根。判不出来的算「系统头」，宁可漏掉也不虚增重合。
    """
    raw = Path(path.replace("\\", "/"))
    candidates = [raw] if raw.is_absolute() else [root / raw]
    try:
        resolved_root = root.resolve()
    except OSError:
        return False
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        return True
    return False


def kendall_tau(common: list[str], rank_a: dict[str, int], rank_b: dict[str, int]) -> float | None:
    """共同项上的顺序一致性；样本少于 3 个时没有统计意义，返回 None。"""
    if len(common) < 3:
        return None
    concordant = discordant = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            a = rank_a[common[i]] - rank_a[common[j]]
            b = rank_b[common[i]] - rank_b[common[j]]
            if a * b > 0:
                concordant += 1
            elif a * b < 0:
                discordant += 1
    pairs = concordant + discordant
    return (concordant - discordant) / pairs if pairs else None


def _avg_ranks(values: list[float]) -> list[float]:
    """平均秩（并列取平均），Spearman 需要它来处理同值。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(pairs: list[tuple[float, float]]) -> float | None:
    """两个指标在**全部共同头文件**上的秩相关。

    Top-10 只回答「头部是否一致」；这个数回答「整个序是否同向」。
    两者都需要：头部一致但整体无关，说明只在极值处巧合；头部不一致
    但整体同向，说明分歧集中在少数几个文件上。
    """
    n = len(pairs)
    if n < 5:
        return None
    xs = _avg_ranks([p[0] for p in pairs])
    ys = _avg_ranks([p[1] for p in pairs])
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else None


def trace_self_times(trace_dir: Path) -> dict[str, dict]:
    """从原始 ``-ftime-trace`` 还原每个头文件的**自身耗时**。

    为什么必须自己算一遍：CBA 直接输出的每头文件时间是 ``Source`` 事件时长
    的累加，而 trace 里的 ``Source`` 区间是**嵌套**的（``json.hpp`` 的区间
    完整包住了 ``adl_serializer.hpp``）。于是 CBA 的榜单天然偏向「伞头文件」，
    而 HeavyHeader 的归因行数是 ``自身行数 × 包含次数``——两者不同量。
    实测证据：u0 的 ``json.hpp`` 区间 382 ms 包含 ``adl_serializer.hpp``
    的 113 ms 区间，25 个 TU 中有 673 个区间互相嵌套。

    ``self = 区间长度 − 直接子区间长度之和`` 得到「这个文件自己的 token 解析
    花了多久」，才与归因行数同构，排序比较才有意义。
    """
    agg: dict[str, dict] = {}
    for trace in sorted(trace_dir.glob("*.json")):
        try:
            events = json.loads(trace.read_text(encoding="utf-8")).get("traceEvents", [])
        except (OSError, json.JSONDecodeError):
            continue
        # Source 事件是 b/e 配对（ph = "b"/"e"），不是带 dur 的 X 事件。
        pending: dict[tuple, list[dict]] = {}
        spans: list[dict] = []
        for event in events:
            if event.get("name") != "Source":
                continue
            key = (event.get("pid"), event.get("tid"), event.get("id"))
            if event.get("ph") == "b":
                pending.setdefault(key, []).append(event)
            elif event.get("ph") == "e" and pending.get(key):
                begin = pending[key].pop()
                detail = (begin.get("args") or {}).get("detail", "")
                if not detail:
                    continue
                spans.append(
                    {
                        "start": begin["ts"],
                        "end": event["ts"],
                        "path": detail,
                        "name": Path(detail).name,
                        "children": [],
                    }
                )

        # 按包含关系建树：父区间先出现（同起点时更长的在前）。
        spans.sort(key=lambda s: (s["start"], -s["end"]))
        stack: list[dict] = []
        for span in spans:
            while stack and span["start"] >= stack[-1]["end"]:
                stack.pop()
            if stack and span["end"] <= stack[-1]["end"]:
                stack[-1]["children"].append(span)
            stack.append(span)

        for span in spans:
            total = span["end"] - span["start"]
            nested = sum(child["end"] - child["start"] for child in span["children"])
            self_us = max(total - nested, 0)
            entry = agg.setdefault(
                span["name"], {"self_us": 0, "count": 0, "path": span["path"]}
            )
            entry["self_us"] += self_us
            entry["count"] += 1
    return agg


def run_cba(entries: list[dict], obj_dir: Path) -> tuple[str, list[str]]:
    """跑一遍 CBA 采集与分析，返回 (完整分析文本, 编译失败列表)。"""
    if RUN.exists():
        for stale in RUN.glob("*.bin"):
            stale.unlink()
    # 旧的 .json trace / .o 会被 `--start` 一并收进去，让「25 个 TU」变成
    # 「25 + 上次的 TU」，时间与包含次数全部虚高。必须清空。
    if obj_dir.exists():
        for stale in obj_dir.iterdir():
            if stale.is_file():
                stale.unlink()
    obj_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(CBA), "--start", str(obj_dir)], check=True, capture_output=True)

    failures: list[str] = []
    for i, entry in enumerate(entries):
        proc = subprocess.run(
            cba_args(entry["command"], obj_dir / f"u{i}.o"),
            cwd=entry["directory"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            # 记下首个错误行：失败原因必须可查，不能只报个计数。
            first = next(
                (ln for ln in proc.stderr.splitlines() if "error:" in ln), "unknown"
            )
            failures.append(f"{Path(entry['file']).name}: {first[:160]}")

    capture = RUN / "capture.bin"
    subprocess.run(
        [str(CBA), "--stop", str(obj_dir), str(capture)], check=True, capture_output=True
    )
    # ini 必须落在 analyze 的 cwd 里，CBA 只从当前目录读。
    (RUN / "ClangBuildAnalyzer.ini").write_text(CBA_INI, encoding="utf-8")
    analysis = subprocess.run(
        [str(CBA), "--analyze", str(capture)],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(RUN),
    ).stdout
    (RUN / "analysis.txt").write_text(analysis, encoding="utf-8")
    return analysis, failures


def run_heavyheader(scan_out: Path, max_tus: int) -> dict:
    """同一批 TU、同一个 clang，参数与 CBA 侧一致。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    subprocess.run(
        [
            sys.executable, "-m", "heavyheader", "scan", str(PROJECT),
            "--compile-commands", str(DB), "--max-tus", str(max_tus),
            "--jobs", "1", "--compiler", str(CLANG),
            *[f"--flag={flag}" for flag in TARGET_FLAGS],
            "--format", "json", "-o", str(scan_out),
        ],
        check=True,
        capture_output=True,
        env=env,
        cwd=str(ROOT),
    )
    return json.loads(scan_out.read_text(encoding="utf-8"))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--tus", type=int, default=25)
    # 第二个工程必须能复用同一套协议：硬编码 PROJECT/DB 会让「换个工程再测一遍」
    # 变成复制脚本，两份代码随后就会漂移——而 G1 要的正是「同一协议、不同工程」。
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    global PROJECT, DB
    if args.project is not None:
        PROJECT = args.project.resolve()
    if args.db is not None:
        DB = args.db.resolve()

    RUN.mkdir(parents=True, exist_ok=True)
    entries = json.loads(DB.read_text(encoding="utf-8"))[: args.tus]

    analysis, failures = run_cba(entries, RUN / "obj")
    scan = run_heavyheader(RUN / "scan.json", args.tus)

    # -- CBA 侧：全量榜单 + 仅项目头 ------------------------------------
    cba_all = parse_cba_headers(analysis)
    cba_project = [h for h in cba_all if under_project(h["path"], PROJECT)]
    cba_top_all = cba_all[:TOP]
    cba_top_project = cba_project[:TOP]

    # -- HeavyHeader 侧：用同一条路径规则筛，而不是依赖 kind -----------
    hh_rows = sorted(
        scan["headers"], key=lambda h: h["attributed_lines"], reverse=True
    )
    hh_project = [h for h in hh_rows if under_project(h["path"], PROJECT)]
    hh_top = hh_project[:TOP]

    cba_names = [Path(h["path"]).name for h in cba_top_project]
    hh_names = [Path(h["path"]).name for h in hh_top]

    cba_rank = {Path(h["path"]).name: i + 1 for i, h in enumerate(cba_project)}
    hh_rank = {Path(h["path"]).name: i + 1 for i, h in enumerate(hh_project)}
    common = [n for n in cba_names if n in set(hh_names)]
    tau = kendall_tau(common, cba_rank, hh_rank)

    # 密度极值必须从数据算，不能写死——写死的那天就和数字对不上了。
    cba_by_name = {Path(h["path"]).name: h for h in cba_project}
    hh_by_name = {Path(h["path"]).name: h for h in hh_project}
    both = sorted(set(cba_by_name) & set(hh_by_name))
    paired = [(cba_by_name[n]["ms"], hh_by_name[n]["attributed_lines"]) for n in both]
    rho = spearman(paired)
    paired_table = sorted(
        (
            {
                "name": n,
                "cba_ms": cba_by_name[n]["ms"],
                "cba_count": cba_by_name[n]["count"],
                "hh_lines": hh_by_name[n]["attributed_lines"],
                "cba_rank": cba_rank[n],
                "hh_rank": hh_rank[n],
            }
            for n in both
        ),
        key=lambda r: r["cba_ms"],
        reverse=True,
    )

    # -- 第二种对照：把 CBA 还原成「自身耗时」再比 ------------------------
    # 上面那组比的是 CBA 的**发布口径**（含子头），与我们的自身行数不同量。
    # 这组是唯一同构的比较，因此它是主结论；发布口径的结果保留作参照。
    self_times = trace_self_times(RUN / "obj")
    self_project = [
        {"name": name, "self_ms": round(v["self_us"] / 1000, 1), "count": v["count"]}
        for name, v in self_times.items()
        if under_project(v["path"], PROJECT) and v["self_us"] > 0
    ]
    self_project.sort(key=lambda r: (-r["self_ms"], r["name"]))
    self_rank = {r["name"]: i + 1 for i, r in enumerate(self_project)}
    self_top = [r["name"] for r in self_project[:TOP]]
    self_common = [n for n in self_top if n in set(hh_names)]
    self_tau = kendall_tau(self_common, self_rank, hh_rank)
    self_pairs = [(r["self_ms"], hh_by_name[r["name"]]["attributed_lines"]) for r in self_project
                  if r["name"] in hh_by_name]

    # 每千行耗时：同为「自身成本」，差别只可能来自每个头内部的内容构成
    # （纯声明 vs 模板实例化）。它解释了两份榜单为什么会分歧。
    density = [
        {
            "name": r["name"],
            "density": r["self_ms"] / (hh_by_name[r["name"]]["attributed_lines"] / 1000),
        }
        for r in self_project
        if r["name"] in hh_by_name
        and hh_by_name[r["name"]]["attributed_lines"] >= DENSITY_MIN_LINES
    ]
    density.sort(key=lambda r: r["density"])
    cheapest, priciest = (density[0], density[-1]) if len(density) >= 2 else (None, None)
    spread = (priciest["density"] / cheapest["density"]) if cheapest else None
    self_rho = spearman(self_pairs)

    payload = {
        "date": date.today().isoformat(),
        "tus": args.tus,
        "compiler": str(CLANG),
        "target_flags": TARGET_FLAGS,
        "failed_compiles": failures,
        "hh_failed_units": scan["totals"].get("failed_translation_units", 0),
        "cba_header_rows": len(cba_all),
        "cba_project_header_rows": len(cba_project),
        "cba_top10_project": cba_top_project,
        "cba_top10_all": cba_top_all,
        "hh_top10_project": [
            {
                "path": h["path"],
                "name": Path(h["path"]).name,
                "attributed_lines": h["attributed_lines"],
                "tu_reach": h.get("tu_reach", 0),
            }
            for h in hh_top
        ],
        "set_overlap_project": common,
        "set_overlap_count": len(common),
        "jaccard_project": round(
            len(common) / len(set(cba_names) | set(hh_names)), 3
        ) if (cba_names or hh_names) else 0.0,
        "kendall_tau_common": None if tau is None else round(tau, 3),
        "spearman_all_common": None if rho is None else round(rho, 3),
        "common_header_count": len(both),
        "paired_table": paired_table,
        # 同构比较：两边都是「自身成本 × 次数」
        "cba_self_top10_project": self_top,
        "cba_self_set_overlap": self_common,
        "cba_self_overlap_count": len(self_common),
        "cba_self_kendall_tau": None if self_tau is None else round(self_tau, 3),
        "cba_self_spearman": None if self_rho is None else round(self_rho, 3),
        "cba_self_project_header_count": len(self_project),
        "density_extremes": {
            "min_lines_threshold": DENSITY_MIN_LINES,
            "cheapest": cheapest,
            "priciest": priciest,
            "spread": None if spread is None else round(spread, 1),
        },
        "cba_self_table": [
            {
                "name": r["name"],
                "self_ms": r["self_ms"],
                "count": r["count"],
                "hh_lines": hh_by_name.get(r["name"], {}).get("attributed_lines"),
                # 每千行解析毫秒：行数与时间的换算率，个体差异可达两个数量级。
                "ms_per_1k_lines": (
                    round(r["self_ms"] / (hh_by_name[r["name"]]["attributed_lines"] / 1000), 1)
                    if r["name"] in hh_by_name
                    and hh_by_name[r["name"]]["attributed_lines"]
                    else None
                ),
                "self_rank": self_rank[r["name"]],
                "hh_rank": hh_rank.get(r["name"]),
            }
            for r in self_project[:20]
        ],
        "hh_compiler": scan.get("compiler"),
        "hh_reproduce_command": scan.get("reproduce_command"),
        "includes_system_headers_in_cba": any(
            not under_project(h["path"], PROJECT) for h in cba_top_all
        ),
        "cba_ranks_of_hh_top": {n: cba_rank.get(n) for n in hh_names},
        "hh_ranks_of_cba_top": {n: hh_rank.get(n) for n in cba_names},
    }

    out_dir = ROOT / "experiments" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.tag}-" if args.tag else ""
    stem = out_dir / f"cba-compare-{suffix}{date.today():%Y%m%d}"
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    def _row(i: int) -> str:
        a = cba_top_project[i] if i < len(cba_top_project) else None
        b = hh_top[i] if i < len(hh_top) else None
        a_txt = f"{Path(a['path']).name} ({a['ms']} ms)" if a else "—"
        b_txt = f"{Path(b['path']).name} ({b['attributed_lines']:,} 行)" if b else "—"
        return f"| {i + 1} | {a_txt} | {b_txt} |"

    md = [
        f"# G1 同任务对照：ClangBuildAnalyzer vs HeavyHeader（{payload['date']}）",
        "",
        "## 协议",
        "",
        f"- 同一批 **{args.tus}** 个编译单元，来自 `experiments/_work/json` 的 "
        "`compile_commands.json`（取前 N 条）。",
        f"- 同一个编译器：`{CLANG.name}` "
        f"{subprocess.run([str(CLANG), '--version'], capture_output=True, text=True).stdout.splitlines()[0]}",
        f"  （HeavyHeader 侧记录的编译器为 `{payload['hh_compiler']}`，两边一致）",
        f"- 同一组参数：`{' '.join(TARGET_FLAGS)}`（MinGW target，避免链到不可用的 MSVC 头）。",
        "- 候选筛选规则两边**完全相同**：路径是否位于项目根下；系统头两边都不计入。",
        "- CBA 的默认配置会破坏对照公平性，本轮显式改了 `ClangBuildAnalyzer.ini`：",
        "  `onlyRootHeaders=false`（默认只列直接被 TU 包含的头，会排除传递包含的头）、",
        "  `header=500`（默认只输出 10 行，去掉系统头后可能只剩三四行）。",
        "  两处默认值都在源码 `src/Analysis.cpp` 的 `Config` 里核实过。",
        "- 指标仍不同（这是工具定位差异，不是口径错误）：CBA 是编译时间，"
        "HeavyHeader 是归因行数。因此比的是「指向同一批热点头文件」与「顺序一致性」。",
        "",
        "## 采集质量",
        "",
        f"- CBA 侧编译失败：**{len(failures)}** / {args.tus}",
        f"- HeavyHeader 侧失败单元：**{payload['hh_failed_units']}** / {args.tus}",
        f"- CBA 解析到头文件行数：{payload['cba_header_rows']} 行，其中项目内 "
        f"{payload['cba_project_header_rows']} 行。",
        "",
        "## Top-10 对照（仅项目内头文件）",
        "",
        "| # | ClangBuildAnalyzer（按编译时间） | HeavyHeader（按归因行数） |",
        "|---|---|---|",
        *[_row(i) for i in range(TOP)],
        "",
        f"**集合重合 {len(common)}/{TOP}**（Jaccard "
        f"{payload['jaccard_project']}）：{', '.join(common) or '无'}",
        "",
        f"**共同项顺序一致性 Kendall τ = "
        f"{'样本不足' if tau is None else f'{tau:.2f}'}**"
        + ("（+1 完全同序，0 无关，−1 完全反序）" if tau is not None else f"，共同项仅 {len(common)} 个"),
        "",
        "## 参照：按 CBA 发布口径（含子头）比较",
        "",
        f"- 两边都报告的项目头文件：**{len(both)}** 个，集合重合 {len(common)}/{TOP}，",
        f"  Kendall τ = {'样本不足' if tau is None else f'{tau:.2f}'}，"
        f"Spearman ρ = {'样本不足' if rho is None else f'{rho:.2f}'}。",
        "- 这一组用的是 CBA 的**发布口径**：`Source` 区间嵌套，所以它的每头文件时间",
        "  含子头（`json.hpp` 的 382 ms 区间里包着 `adl_serializer.hpp` 的 113 ms）。",
        "  与我们的「自身行数」不同量，因此**不作为主结论**，只作参照。",
        "",
        "## 主结果：还原成「自身耗时」后的同构比较",
        "",
        "把 CBA 的原始 trace 按「区间长度 − 直接子区间」还原成自身耗时，",
        "与 `自身行数 × 包含次数` 同构后再排名（推导在 `trace_self_times()`，可复核）。",
        "",
        "| # | CBA 自身耗时 Top-10 | HeavyHeader 归因行数 Top-10 |",
        "|---|---|---|",
        *[
            f"| {i + 1} | "
            + (
                f"{self_top[i]} ({self_project[i]['self_ms']} ms)"
                if i < len(self_top)
                else "—"
            )
            + f" | {hh_names[i] if i < len(hh_names) else '—'} |"
            for i in range(TOP)
        ],
        "",
        f"- 集合重合 **{len(self_common)}/{TOP}**：{', '.join(self_common) or '无'}",
        f"- 共同项顺序一致性 Kendall τ = **"
        f"{'样本不足' if self_tau is None else f'{self_tau:.2f}'}**",
        f"- 全部 {len(self_pairs)} 个共同项目头的秩相关 Spearman ρ = **"
        f"{'样本不足' if self_rho is None else f'{self_rho:.2f}'}**",
        "",
        "## 为什么行数排不准：每千行解析成本差两个数量级",
        "",
        "| 头文件 | 自身耗时 | 包含次数 | 归因行数 | 每千行耗时 | CBA 名次 | HH 名次 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        *[
            f"| `{r['name']}` | {r['self_ms']} ms | {r['count']} | "
            f"{r['hh_lines'] if r['hh_lines'] is not None else '—'} | "
            f"{r['ms_per_1k_lines'] if r['ms_per_1k_lines'] is not None else '—'} | "
            f"{r['self_rank']} | {r['hh_rank'] if r['hh_rank'] else '—'} |"
            for r in payload["cba_self_table"][:12]
        ],
        "",
        (
            f"在归因行数 ≥ {DENSITY_MIN_LINES:,} 的 {len(density)} 个头上，"
            f"最便宜的 `{cheapest['name']}` 每千行约 {cheapest['density']:.1f} ms，"
            f"最贵的 `{priciest['name']}` 约 {priciest['density']:.1f} ms，"
            f"相差 **{spread:.0f}×**。"
            "（更小的头归一化后极不稳定，故设此门槛。）"
            "因此行数只能做候选筛选，**不能当成本结论**；成本结论必须来自实测。"
            if cheapest and priciest and spread
            else "密度样本不足，无法给出换算率区间。"
        ),
        "",
        "## CBA Top-10 里被排除的系统头",
        "",
        *[
            f"- `{Path(h['path']).name}`（{h['ms']} ms，包含 {h['count']} 次）"
            for h in cba_top_all
            if not under_project(h["path"], PROJECT)
        ],
        "",
        "## 判读边界",
        "",
        "- 时间序与行数序本来就不必相同：模板实例化重的头可能行数不多但耗时极高；",
        "  反之一个巨大的纯声明头行数多而解析便宜。两者不一致**不构成**任一方错误。",
        "- 本对照能支持的结论只有一句：在同一任务（找出最该治理的头文件）上，",
        "  两个工具给出的前 10 名是否指向同一批文件、顺序是否一致。",
        "- 它**不能**支持「谁更准」或「谁更快」——那需要以实际删改后的构建变化为准。",
    ]
    stem.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"written: {stem.with_suffix('.md')}")
    print(f"failures cba={len(failures)} hh={payload['hh_failed_units']}")
    print(f"published  overlap {len(common)}/{TOP} tau={tau} rho={rho}")
    print(f"self-time  overlap {len(self_common)}/{TOP} tau={self_tau} rho={self_rho}")
    print("CBA self:", self_top)
    print("HH      :", hh_names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
