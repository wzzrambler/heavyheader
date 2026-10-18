"""Tests for the history trend dashboard.

历史文件是 append-only 的运行记录：会被手工查看、被拷贝、被中断写入。
看板要能在「坏行、空文件、缺字段」之下仍画出趋势，而不是整页报错。
这里钉住三件事：

1. ``load_trend`` 容错——坏行跳过、空行跳过、缺字段不崩；
2. ``summarize`` 的口径——趋势方向、净变化、峰值、最大单步回归/改善；
3. ``render_dashboard`` / ``make_server`` 能产出可渲染、可服务的自包含页面。
"""

from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

from heavyheader.dashboard import (
    load_trend,
    make_server,
    render_dashboard,
    summarize,
)
from heavyheader.report.html import TrendPoint


def _write_history(path: Path, entries: list[dict]) -> None:
    path.write_text(
        "\n".join(__import__("json").dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# load_trend 容错
# --------------------------------------------------------------------------


def test_missing_file_yields_empty(tmp_path: Path) -> None:
    assert load_trend(tmp_path / "does-not-exist.jsonl") == []


def test_empty_file_yields_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert load_trend(path) == []


def test_corrupt_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "messy.jsonl"
    _write_history(
        path,
        [
            {"timestamp": "2026-10-10T09:00:00", "attributed_lines": 1200, "seconds": 42.3},
            # 下面是各种坏行，都应被跳过而不是抛错
            "this is not json at all",
            "   ",  # 纯空白行
            {"timestamp": "2026-10-11T09:00:00", "attributed_lines": 1350},  # 缺 seconds
            [1, 2, 3],  # 不是 dict
        ],
    )
    points = load_trend(path)
    assert len(points) == 2  # 只有两个有效条目
    assert [p.attributed_lines for p in points] == [1200, 1350]


def test_bad_timestamp_falls_back_to_index_label(tmp_path: Path) -> None:
    path = tmp_path / "bad-stamp.jsonl"
    _write_history(path, [{"timestamp": "not-a-date", "attributed_lines": 100}])
    points = load_trend(path)
    assert points[0].label.startswith("#")


def test_seconds_defaults_to_zero(tmp_path: Path) -> None:
    path = tmp_path / "no-secs.jsonl"
    _write_history(path, [{"timestamp": "2026-10-10T09:00:00", "attributed_lines": 90}])
    assert load_trend(path)[0].seconds == 0.0


# --------------------------------------------------------------------------
# summarize 口径
# --------------------------------------------------------------------------


def _points() -> list[TrendPoint]:
    return [
        TrendPoint(label="10-10 09:00", attributed_lines=1000, seconds=40.0),
        TrendPoint(label="10-11 09:00", attributed_lines=1200, seconds=44.0),
        TrendPoint(label="10-12 09:00", attributed_lines=900, seconds=38.0),
    ]


def test_empty_summary() -> None:
    s = summarize([])
    assert s.count == 0
    assert s.trend_label == "数据不足"


def test_summary_aggregates_first_last_delta_peak() -> None:
    s = summarize(_points())
    assert s.count == 3
    assert s.first_lines == 1000
    assert s.last_lines == 900
    assert s.delta_lines == -100
    assert s.peak_lines == 1200
    assert s.peak_index == 1


def test_summary_reports_worst_and_best_step() -> None:
    s = summarize(_points())
    # 1000→1200 = +20%，1200→900 = -25%
    assert s.worst_step_percent == 20.0
    assert "10-10" in s.worst_step_label
    assert s.best_step_percent == -25.0


def test_trend_label_thresholds() -> None:
    assert summarize(
        [
            TrendPoint("a", 100, 1.0),
            TrendPoint("b", 102, 1.0),
        ]
    ).trend_label == "上升"
    assert summarize(
        [
            TrendPoint("a", 100, 1.0),
            TrendPoint("b", 98, 1.0),
        ]
    ).trend_label == "下降"
    assert summarize(
        [
            TrendPoint("a", 100, 1.0),
            TrendPoint("b", 100, 1.0),
        ]
    ).trend_label == "持平"


def test_single_point_has_no_steps() -> None:
    s = summarize([TrendPoint("a", 100, 1.0)])
    assert s.count == 1
    assert s.worst_step_percent == 0.0
    assert s.best_step_percent == 0.0


# --------------------------------------------------------------------------
# render_dashboard
# --------------------------------------------------------------------------


def test_render_contains_cards_and_chart() -> None:
    doc = render_dashboard(_points())
    assert "构建成本趋势" in doc
    assert 'class="cards"' in doc
    assert "<svg" in doc  # 两张以上数据点应出图


def test_render_degrades_without_enough_points() -> None:
    doc = render_dashboard([TrendPoint("a", 100, 1.0)])
    assert "历史点不足两点" in doc
    assert 'class="cards"' in doc


def test_render_handles_empty_history() -> None:
    doc = render_dashboard([])
    assert "暂无历史记录" in doc


def test_render_escapes_user_controlled_title() -> None:
    doc = render_dashboard([], title="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in doc
    assert "&lt;script&gt;" in doc


# --------------------------------------------------------------------------
# make_server（loopback HTTP）
# --------------------------------------------------------------------------


def test_server_serves_dashboard_on_loopback(tmp_path: Path) -> None:
    path = tmp_path / "cost.jsonl"
    _write_history(path, [{"timestamp": "2026-10-10T09:00:00", "attributed_lines": 1000}])
    httpd = make_server(path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "text/html" in resp.headers.get("Content-Type", "")
        assert "构建成本趋势" in body
        assert "扫描次数" in body
    finally:
        httpd.shutdown()
        httpd.server_close()
