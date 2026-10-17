"""Self-contained HTML report.

One file, no CDN, no JavaScript framework, no network access at view time.
That is a hard requirement rather than a preference: the report gets attached
to pull requests, mailed to people, and opened on machines with no internet,
and a report that shows a blank page in those situations is worthless.

Charts are hand-rolled inline SVG.  The data volumes involved (tens of
headers, tens of history points) are far too small to justify a charting
library, and drawing them ourselves keeps the whole thing under one file.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from ..models import HeaderKind, ProjectScan

__all__ = ["render_html", "TrendPoint", "trend_chart"]


@dataclass(slots=True)
class TrendPoint:
    """One entry in a scan history, used to draw the trend chart."""

    label: str
    attributed_lines: int
    seconds: float


_CSS = """
:root {
  --bg: #f6f7f9;
  --surface: #ffffff;
  --border: #e3e6ea;
  --text: #1c2128;
  --muted: #5b6470;
  --accent: #2f6feb;
  --warn: #b25e09;
  --bad: #c9372c;
  --good: #1a7f52;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px 64px;
  background: var(--bg); color: var(--text);
  font: 15px/1.6 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.wrap { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 36px 0 12px; }
.sub { color: var(--muted); margin: 0 0 24px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.card .k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.card .v { font-size: 24px; font-weight: 650; margin-top: 4px; font-variant-numeric: tabular-nums; }
.card .n { color: var(--muted); font-size: 12px; margin-top: 2px; }
.panel { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; margin-top: 14px; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); font-size: 13.5px; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
td.num, th.num { text-align: right; }
tr:last-child td { border-bottom: none; }
code, .mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 12.5px; }
.path { word-break: break-all; }
.tag { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11.5px; border: 1px solid var(--border); color: var(--muted); }
.tag.high { color: var(--bad); border-color: #f0c6c2; background: #fdf1f0; }
.tag.medium { color: var(--warn); border-color: #f0dcc0; background: #fdf7ee; }
.tag.low { color: var(--muted); }
.note { color: var(--muted); font-size: 13px; margin: 0 0 14px; }
.bar-row { display: grid; grid-template-columns: minmax(160px, 300px) 1fr 90px; gap: 10px; align-items: center; padding: 3px 0; }
.bar-track { background: #eef0f3; border-radius: 4px; height: 16px; overflow: hidden; }
.bar-fill { height: 100%; background: var(--accent); border-radius: 4px; }
.bar-fill.system { background: #b9c2cf; }
.bar-val { text-align: right; color: var(--muted); font-size: 12.5px; font-variant-numeric: tabular-nums; }
footer { margin-top: 48px; color: var(--muted); font-size: 12.5px; text-align: center; }
"""


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _fmt_seconds(value: float) -> str:
    if value >= 60:
        return f"{value / 60:.1f} min"
    if value >= 1:
        return f"{value:.2f} s"
    return f"{value * 1000:.0f} ms"


def _rel(path: Path, root: Path) -> str:
    """Project-relative path, or a disambiguating tail for foreign headers."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        parts = path.parts
        return str(Path(*parts[-2:])) if len(parts) > 1 else path.name


def _bar_chart(rows: list[tuple[str, int, bool]], unit: str) -> str:
    if not rows:
        return '<p class="note">没有可显示的数据。</p>'
    peak = max(value for _, value, _ in rows) or 1
    out = ['<div class="panel">']
    for label, value, is_system in rows:
        width = max(1.5, value / peak * 100)
        cls = "bar-fill system" if is_system else "bar-fill"
        out.append(
            f'<div class="bar-row">'
            f'<div class="mono path" title="{_esc(label)}">{_esc(_shorten(label))}</div>'
            f'<div class="bar-track"><div class="{cls}" style="width:{width:.1f}%"></div></div>'
            f'<div class="bar-val">{_fmt_int(value)} {unit}</div>'
            f"</div>"
        )
    out.append("</div>")
    return "".join(out)


def _shorten(label: str, keep: int = 46) -> str:
    return label if len(label) <= keep else "…" + label[-keep:]


def trend_chart(points: list[TrendPoint]) -> str:
    """Inline SVG line chart of attributed lines over time."""
    if len(points) < 2:
        return '<p class="note">历史点不足两点，无法绘制趋势。连续运行 `scan --history` 多次即可生成。</p>'

    width, height = 1000, 220
    pad_l, pad_r, pad_t, pad_b = 56, 16, 16, 34
    values = [p.attributed_lines for p in points]
    low, high = min(values), max(values)
    span = (high - low) or 1

    def x(index: int) -> float:
        return pad_l + (width - pad_l - pad_r) * index / (len(points) - 1)

    def y(value: int) -> float:
        return pad_t + (height - pad_t - pad_b) * (1 - (value - low) / span)

    path = " ".join(
        ("M" if i == 0 else "L") + f"{x(i):.1f},{y(p.attributed_lines):.1f}"
        for i, p in enumerate(points)
    )
    area = (
        f"{x(0):.1f},{height - pad_b} "
        + " ".join(f"{x(i):.1f},{y(p.attributed_lines):.1f}" for i, p in enumerate(points))
        + f" {x(len(points) - 1):.1f},{height - pad_b}"
    )

    grid = []
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        value = low + span * fraction
        gy = y(value)
        grid.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
            f'stroke="#e3e6ea" stroke-width="1"/>'
            f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#5b6470">{_fmt_int(int(value))}</text>'
        )

    labels = []
    for index, point in enumerate(points):
        if len(points) > 12 and index % max(1, len(points) // 8) != 0 and index != len(points) - 1:
            continue
        labels.append(
            f'<text x="{x(index):.1f}" y="{height - pad_b + 18}" text-anchor="middle" '
            f'font-size="10.5" fill="#5b6470">{_esc(point.label)}</text>'
        )

    return (
        f'<div class="panel"><svg viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" aria-label="构建成本趋势">'
        f'<polygon points="{area}" fill="#2f6feb" opacity="0.10"/>'
        + "".join(grid)
        + f'<path d="{path}" fill="none" stroke="#2f6f" stroke-width="2"/>'
        + "".join(
            f'<circle cx="{x(i):.1f}" cy="{y(p.attributed_lines):.1f}" r="3" fill="#2f6feb"/>'
            for i, p in enumerate(points)
        )
        + "".join(labels)
        + "</svg></div>"
    ).replace('stroke="#2f6f"', 'stroke="#2f6feb"')


def render_html(
    scan: ProjectScan,
    *,
    title: str = "HeavyHeader 构建成本报告",
    trend: list[TrendPoint] | None = None,
) -> str:
    """Render the full standalone HTML report."""
    root = scan.root
    ok = scan.ok_tus
    project_headers = [s for s in scan.headers_by_cost() if s.kind is HeaderKind.PROJECT]

    kpis = [
        ("头文件解析量", _fmt_int(scan.total_included_lines()), "行 · 全项目累计"),
        ("编译单元", str(len(ok)), f"{len(scan.failed_tus)} 个失败"),
        ("依赖边", _fmt_int(scan.total_dependency_edges()), "条 include 关系"),
        ("累计编译耗时", _fmt_seconds(scan.baseline_seconds), "wall clock 合计"),
        ("可精简 include", str(len(scan.candidates)), "已按收益排序"),
    ]
    cards = "".join(
        f'<div class="card"><div class="k">{_esc(k)}</div>'
        f'<div class="v">{_esc(v)}</div><div class="n">{_esc(n)}</div></div>'
        for k, v, n in kpis
    )

    bars = _bar_chart(
        [
            (_rel(s.path, root), s.attributed_lines, s.kind is not HeaderKind.PROJECT)
            for s in project_headers[:15]
        ],
        "行",
    )

    # candidates table
    if scan.candidates:
        rows = []
        for candidate in scan.candidates_by_estimate()[:25]:
            rows.append(
                "<tr>"
                f'<td class="mono path">{_esc(_rel(candidate.tu, root))}</td>'
                f'<td class="mono path">{_esc(_rel(candidate.header, root))}</td>'
                f'<td class="num">{len(candidate.exclusive_headers)}</td>'
                f'<td class="num">{_fmt_int(candidate.estimated_lines_saved)}</td>'
                f'<td class="num">{candidate.estimated_percent:.1f}%</td>'
                f'<td><span class="tag {_esc(candidate.confidence)}">'
                f"{_esc(candidate.confidence)}</span></td>"
                "</tr>"
            )
        candidates_table = (
            '<div class="panel"><table><thead><tr>'
            "<th>编译单元</th><th>include</th><th class='num'>独占闭包</th>"
            "<th class='num'>预计省行数</th><th class='num'>预计占比</th><th>置信度</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        )
    else:
        candidates_table = '<p class="note">没有发现值得精简的 include。</p>'

    redundant = sorted(
        (
            (h, s)
            for h, s in scan.header_stats.items()
            if s.inclusion_events > s.tu_reach
        ),
        key=lambda item: item[1].inclusion_events - item[1].tu_reach,
        reverse=True,
    )
    if redundant:
        rows = "".join(
            "<tr>"
            f'<td class="mono path">{_esc(_rel(h, root))}</td>'
            f'<td class="num">{s.inclusion_events}</td>'
            f'<td class="num">{s.tu_reach}</td>'
            f'<td class="num">{s.inclusion_events - s.tu_reach}</td>'
            "</tr>"
            for h, s in redundant[:20]
        )
        guards_table = (
            '<div class="panel"><table><thead><tr><th>头文件</th>'
            "<th class='num'>解析次数</th><th class='num'>涉及 TU</th>"
            "<th class='num'>多解析次数</th></tr></thead><tbody>"
            + rows
            + "</tbody></table></div>"
        )
    else:
        guards_table = '<p class="note">未发现缺少 include guard 的头文件。</p>'

    failures = ""
    if scan.failed_tus:
        items = "".join(
            f'<li><code>{_esc(_rel(tu.source, root))}</code> — {_esc(tu.error)}</li>'
            for tu in scan.failed_tus[:30]
        )
        failures = f"<h2>编译失败的单元</h2><div class='panel'><ul>{items}</ul></div>"

    trend_block = ""
    if trend:
        trend_block = (
            "<h2>构建成本趋势</h2>"
            + trend_chart(trend)
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>{_esc(title)}</h1>
  <p class="sub">项目 <code>{_esc(root)}</code> · 编译器 <code>{_esc(scan.compiler)}</code>
  {f"· 标准 <code>{_esc(scan.standard)}</code>" if scan.standard else ""}</p>

  <div class="cards">{cards}</div>

  {trend_block}

  <h2>成本热点 Top 15</h2>
  <p class="note">归因行数 = 头文件行数 × 引用它的编译单元数，衡量该头文件让整个项目多解析了多少行代码。</p>
  {bars}

  <h2>可精简的 include</h2>
  <p class="note">独占闭包 = 删掉这条 include 后才会停止解析的头文件。若某头文件仍可从其他 include 到达，删除当前这条并不省钱。</p>
  {candidates_table}

  <h2>缺少 include guard 的头文件</h2>
  <p class="note">同一编译单元内被重复解析，对带 guard 的头文件不可能发生——这是确定性信号，补 <code>#pragma once</code> 即可，永远安全。</p>
  {guards_table}

  {failures}

  <footer>由 HeavyHeader 生成 · 指标定义见 docs/methodology.md</footer>
</div>
</body>
</html>
"""
