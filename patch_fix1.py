# -*- coding: utf-8 -*-
"""修审计确认的三处硬错误（其余待重测后处理）。"""
import io

BS = "\\"

def patch(path, subs):
    s = io.open(path, encoding="utf-8").read()
    for old, new in subs:
        assert old in s, "%s NOT FOUND:\n%s" % (path, old[:90])
        n = s.count(old)
        assert n >= 1, "%s count 0" % path
        s = s.replace(old, new)
        print("  %-46s x%d" % (old[:44].replace("\n", " "), n))
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print("%s: %d rules applied" % (path, len(subs)))


print("== make_pdf.py ==")
patch("docs/make_pdf.py", [
    # 1) 四语料合计被误挂到 json 单语料上；边数也对不上归档（9,294）
    ('"nlohmann/json 25 TU：1430 万行解析量归因到 9302 条 include 边",',
     '"nlohmann/json 25 TU：487 万行解析量归因到 9,294 条 include 边",'),
    # 2) spdlog 复现次数：归档写的是三次独立运行
    ('566 → 125 ms（<b>−77.8%，四次独立运行复现</b>）',
     '566 → 125 ms（<b>−77.8%，三次独立运行复现</b>）'),
    # 3) zstd 单条收益：归档记录写 46.5%（179 ms）
    ("（<b>−46.6%%</b>）", "（<b>−46.5%%</b>）"),
    ('zstd 单条 −46.6%', 'zstd 单条 −46.5%'),
    ('zstd 单条 <b>−46.6%</b>', 'zstd 单条 <b>−46.5%</b>'),
    ('zstd <b>−46.6%</b>', 'zstd <b>−46.5%</b>'),
    ('「删了省 46.6%%」', '「删了省 46.5%%」'),
])

print("== make_pdf_review.py ==")
patch("docs/make_pdf_review.py", [
    ('"nlohmann/json 25 TU：1430 万行解析量归因到 9302 条 include 边",',
     '"nlohmann/json 25 TU：487 万行解析量归因到 9,294 条 include 边",'),
    ('566 → 125 ms（<b>−77.8%，四次独立运行复现</b>）',
     '566 → 125 ms（<b>−77.8%，三次独立运行复现</b>）'),
    ('zstd 单条 <b>−46.6%</b>', 'zstd 单条 <b>−46.5%</b>'),
    ('384 → 205 ms（<b>−46.6%</b>）', '384 → 205 ms（<b>−46.5%</b>）'),
    ('「删了省 46.6%%」', '「删了省 46.5%%」'),
    ('zstd −46.6% / CPU −11.4%', 'zstd −46.5% / CPU −11.4%'),
])

print("== make_topic_report.py ==")
patch("docs/make_topic_report.py", [
    ('−77.8% 的大额「提升」', '−77.8% 的大额「提升」'),
])

print("== demo-walkthrough.md ==")
patch("docs/demo-walkthrough.md", [
    ('zstd 单条 −46.6%', 'zstd 单条 −46.5%'),
    ('−46.6%（384→205 ms）', '−46.5%（384→205 ms）'),
])

print("== restructure-map.md ==")
patch("docs/restructure-map.md", [
    ('zstd −46.6%', 'zstd −46.5%'),
])

print("== CHANGELOG.md ==")
patch("CHANGELOG.md", [
    ('zstd 单条 −46.6%', 'zstd 单条 −46.5%'),
    ('（384 → 205 ms）', '（384 → 205 ms）'),
])
