"""历史趋势看板。

`--history` 让每次 `scan` 往一个 JSONL 文件追加一行，于是项目有了一条时间
序列。这个模块把它变成两样能用的东西：

1. **终端摘要**（`history` 子命令）——能直接读进 CI 日志；
2. **本地 HTTP 看板**（`history --serve`）——浏览器里看趋势。

## 判定口径

与 `gate` 完全一致：**归因行数是主指标**（确定性、可复现），墙钟时间只做
展示（它随机器负载波动，跨机器不可比）。这样「看板上的趋势」和「CI 上的门禁」
说的是同一件事——否则会出现看板显示成本涨了、门禁却说没问题的荒谬情况。

## 为什么不做成需要数据库的服务

看板要解决的是「我一个人/一个小组，能不能随时看到项目成本怎么变的」。
它需要能被一条命令启动、不装任何东西、不占端口到下次重启、关掉就彻底消失。
所以用标准库 `http.server` 起一个只服务单页的临时服务，HTML 自包含
（内联 SVG 画图，无 CDN、无图表库）——和 `report/html.py` 的做法一致。
多人共享确实是它做不到的，那属于 v0.2 的另一项「历史存储与趋势服务」。
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import sys
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .report.html import TrendPoint, trend_chart

__all__ = [
    "TrendSummary",
    "load_trend",
    "summarize",
    "render_dashboard",
    "make_server",
    "serve",
]


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------


def load_trend(path: Path) -> list[TrendPoint]:
    """Read a JSONL history file, skipping anything unparseable.

    历史文件是 append-only 的运行记录，会被手工查看、被拷贝、被中断写入。
    一行坏的（截断的、夹杂日志的）不该让整个趋势看不了，所以跳过而不是报错。
    """
    target = Path(path)
    points: list[TrendPoint] = []
    if not target.is_file():
        return points
    for raw in target.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        stamp = str(entry.get("timestamp") or "")
        # 只在我们自己产生的 ISO 时间戳（含 T）上做切片；其它情况（缺失、
        # 或形如 "bad-stamp" 的垃圾）一律回退到序号，避免切出 "tamp" 这种怪标签。
        label = stamp[5:16].replace("T", " ") if "T" in stamp else f"#{len(points) + 1}"
        points.append(
            TrendPoint(
                label=label,
                attributed_lines=int(entry.get("attributed_lines") or 0),
                seconds=float(entry.get("seconds") or 0.0),
            )
        )
    return points


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------


@dataclass
class TrendSummary:
    """What a scan history says about the project's cost trajectory."""

    count: int = 0
    first_lines: int = 0
    last_lines: int = 0
    delta_lines: int = 0
    delta_percent: float = 0.0
    peak_lines: int = 0
    peak_index: int = 0
    #: 相邻两次扫描之间归因行数的最大涨幅（百分比），与 gate 同口径。
    worst_step_percent: float = 0.0
    worst_step_label: str = ""
    best_step_percent: float = 0.0
    best_step_label: str = ""

    @property
    def trend_label(self) -> str:
        if self.count < 2:
            return "数据不足"
        if self.delta_percent > 1.0:
            return "上升"
        if self.delta_percent < -1.0:
            return "下降"
        return "持平"


def _percent(before: int, after: int) -> float:
    if not before:
        return 0.0
    return (after - before) / before * 100.0


def summarize(points: list[TrendPoint]) -> TrendSummary:
    """Turn a history into the handful of numbers a person will act on.

    刻意只报这几项：起点终点、净变化、峰值、最大单步回归与最大单步改善。
    再多的数字就变成没人看的仪表盘了。
    """
    if not points:
        return TrendSummary()

    values = [p.attributed_lines for p in points]
    summary = TrendSummary(
        count=len(points),
        first_lines=values[0],
        last_lines=values[-1],
        delta_lines=values[-1] - values[0],
        delta_percent=_percent(values[0], values[-1]),
        peak_lines=max(values),
        peak_index=values.index(max(values)),
    )

    if len(points) >= 2:
        steps = [
            (
                _percent(before, after),
                f"{before_point.label} → {after_point.label}",
            )
            for (before, before_point), (after, after_point) in zip(
                zip(values[:-1], points[:-1], strict=True),
                zip(values[1:], points[1:], strict=True),
                strict=True,
            )
        ]
        worst = max(steps, key=lambda item: item[0])
        best = min(steps, key=lambda item: item[0])
        summary.worst_step_percent, summary.worst_step_label = worst
        summary.best_step_percent, summary.best_step_label = best

    return summary


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _metric_cards(summary: TrendSummary) -> str:
    cards = [
        ("扫描次数", _fmt_int(summary.count), ""),
        (
            "当前归因行数",
            _fmt_int(summary.last_lines),
            f"起点 {_fmt_int(summary.first_lines)}",
        ),
        (
            "净变化",
            f"{summary.delta_lines:+,}",
            f"{summary.delta_percent:+.1f}%",
        ),
        ("历史峰值", _fmt_int(summary.peak_lines), f"第 {summary.peak_index + 1} 次扫描"),
        (
            "最大单步回归",
            f"{summary.worst_step_percent:+.1f}%",
            summary.worst_step_label,
        ),
        (
            "最大单步改善",
            f"{summary.best_step_percent:+.1f}%",
            summary.best_step_label,
        ),
    ]
    html_cards = "".join(
        f'<div class="card"><div class="card-label">{_esc(label)}</div>'
        f'<div class="card-value">{_esc(value)}</div>'
        f'<div class="card-foot">{_esc(foot)}</div></div>'
        for label, value, foot in cards
    )
    return f'<div class="cards">{html_cards}</div>'


def _trend_table(points: list[TrendPoint]) -> str:
    if not points:
        return '<p class="note">暂无历史记录。</p>'
    rows = []
    previous: int | None = None
    for point in points:
        if previous is None:
            change = "—"
        else:
            change = f"{_percent(previous, point.attributed_lines):+.1f}%"
        rows.append(
            "<tr>"
            f"<td>{_esc(point.label)}</td>"
            f'<td class="num">{_fmt_int(point.attributed_lines)}</td>'
            f'<td class="num">{change}</td>'
            f'<td class="num">{point.seconds:.2f}s</td>'
            "</tr>"
        )
        previous = point.attributed_lines
    return (
        '<table><thead><tr><th>时间</th><th class="num">归因行数</th>'
        '<th class="num">较上次</th><th class="num">编译耗时</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_dashboard(
    points: list[TrendPoint], *, title: str = "HeavyHeader 构建成本趋势看板"
) -> str:
    """Self-contained HTML dashboard: one file, no network at view time."""
    summary = summarize(points)
    cards = _metric_cards(summary)
    chart = trend_chart(points)
    table = _trend_table(points)
    generated = _dt.datetime.now().isoformat(timespec="seconds")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_DASHBOARD_CSS}</style>
</head>
<body>
<main>
<header>
  <h1>{_esc(title)}</h1>
  <p class="sub">扫描次数 {summary.count} · 当前趋势：<b>{summary.trend_label}</b></p>
</header>
{cards}
<h2>归因行数随时间变化</h2>
{chart}
<h2>每次扫描明细</h2>
{table}
<footer>由 HeavyHeader 生成 · {_esc(generated)} · 指标定义见 docs/methodology.md</footer>
</main>
</body>
</html>
"""


# --------------------------------------------------------------------------
# local server (loopback only)
# --------------------------------------------------------------------------


def make_server(
    history_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    title: str | None = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) a temporary dashboard server.

    Bound to the loopback interface on purpose: the dashboard is for one
    person on one machine, not for sharing over a network.  The history file
    is re-read on every request, so a fresh ``scan --history`` shows up on
    refresh without restarting anything.
    """
    data_path = Path(history_path)
    page_title = title or f"HeavyHeader 构建成本趋势 · {data_path.name}"

    class _DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required name
            try:
                points = load_trend(data_path)
            except OSError:
                points = []
            body = render_dashboard(points, title=page_title).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # silence default stderr logging
            return

    return ThreadingHTTPServer((host, port), _DashboardHandler)


def serve(
    history_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    open_browser: bool = True,
) -> None:
    """Start the dashboard server and block until interrupted.

    Closing the terminal kills it too — there is no daemon left running in the
    background, and nothing is installed.  ``Ctrl-C`` stops it cleanly.
    """
    httpd = make_server(history_path, host=host, port=port)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"HeavyHeader 趋势看板已启动：{url}", file=sys.stderr)
    print("按 Ctrl+C 停止。关闭终端也会一并结束，不会在后台残留。", file=sys.stderr)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        pass
    finally:
        httpd.server_close()


_DASHBOARD_CSS = """
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
  margin: 0; padding: 32px 24px 56px;
  background: var(--bg); color: var(--text);
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
  line-height: 1.6;
}
main { max-width: 1080px; margin: 0 auto; }
header { margin-bottom: 26px; border-bottom: 1px solid var(--border); padding-bottom: 18px; }
h1 { font-size: 26px; margin: 0 0 6px; font-weight: 700; letter-spacing: .3px; }
.sub { margin: 0; color: var(--muted); font-size: 13.5px; }
h2 { font-size: 17px; margin: 30px 0 12px; font-weight: 700; }
.cards {
  display: grid; gap: 12px; margin: 4px 0 8px;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 13px 15px;
}
.card-label { font-size: 12px; color: var(--muted); }
.card-value { font-size: 21px; font-weight: 700; margin: 3px 0 2px; }
.card-foot { font-size: 11.5px; color: var(--muted); }
table { border-collapse: collapse; width: 100%; background: var(--surface); }
th, td { border: 1px solid var(--border); padding: 7px 10px; text-align: left; font-size: 13px; }
th { background: #1f3a5c; color: #fff; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.panel {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px;
}
.note { color: var(--muted); font-size: 13px; }
footer {
    margin-top: 34px; color: var(--muted); font-size: 12px;
    border-top: 1px solid var(--border); padding-top: 12px;
}
"""
