"""生成《HeavyHeader 选题报告》PDF。

视觉样式对标《超级工厂_选题报告.pdf》：
- 封面：特大加粗标题 + 副标题 + 灰色元信息 + 主图 + 图注；
- 页眉左上灰色「HeavyHeader ｜ 章节名」，页脚居中页码；
- 摘要（含关键词）、带页码与点线引导符的目录；
- 中文序号章节（一 二 三…），加粗小节标题；
- 深蓝表头三线感表格，隔行浅蓝；
- 行内引用：蓝色「R#：来源」与灰色「[D#]」；
- 图注按「图 N」编号。

内容约束（与 make_pdf.py 相同的诚实性约束）：
- 只写仓库里真实存在的数据与文档，不编造政策条文或论文；
- 区分「实测数据」与「启发式估计」，安全性结论只归 verify；
- 已知局限单独成章。

运行：python docs/make_topic_report.py "HeavyHeader-选题报告.pdf"
"""

from __future__ import annotations

import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

# --------------------------------------------------------------------------
# fonts / palette
# --------------------------------------------------------------------------

_FONT = "YaHei"
_FONT_BOLD = "YaHeiBold"
_MONO = "Cascadia"

pdfmetrics.registerFont(TTFont(_FONT, "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont(_FONT_BOLD, "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont(_MONO, "C:/Windows/Fonts/CascadiaMono.ttf"))

INK = colors.HexColor("#1c2128")        # 正文
MUTED = colors.HexColor("#6b7280")      # 页眉/图注/元信息
LINK = colors.HexColor("#1a66b0")       # 行内引用蓝
NAVY = colors.HexColor("#1f3a5c")       # 表头深蓝 / 图形主色
NAVY_SOFT = colors.HexColor("#edf2f8")  # 表格隔行浅蓝
RULE = colors.HexColor("#c9d2dc")       # 表格边线
CODE_BG = colors.HexColor("#f4f6f8")
CODE_INK = colors.HexColor("#243b53")

PAGE_W, PAGE_H = A4
M_L, M_R, M_T, M_B = 22 * mm, 22 * mm, 24 * mm, 20 * mm
CONTENT_W = PAGE_W - M_L - M_R

# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------

S = {}


def _style(name, **kw):
    S[name] = ParagraphStyle(name=name, **kw)
    return S[name]


_style("body", fontName=_FONT, fontSize=10.5, leading=17.5,
       alignment=TA_JUSTIFY, textColor=INK, spaceAfter=7)
_style("h1", fontName=_FONT_BOLD, fontSize=19, leading=26,
       textColor=INK, spaceBefore=16, spaceAfter=12)
_style("h2", fontName=_FONT_BOLD, fontSize=12, leading=18,
       textColor=INK, spaceBefore=12, spaceAfter=6)
_style("cover_title", fontName=_FONT_BOLD, fontSize=34, leading=44,
       textColor=INK, spaceAfter=18)
_style("cover_sub", fontName=_FONT_BOLD, fontSize=16, leading=24,
       textColor=INK, spaceAfter=16)
_style("cover_meta", fontName=_FONT, fontSize=11, leading=20, textColor=INK)
_style("caption", fontName=_FONT, fontSize=9, leading=14,
       alignment=TA_CENTER, textColor=MUTED, spaceBefore=4, spaceAfter=12)
_style("code", fontName=_MONO, fontSize=8.6, leading=13, textColor=CODE_INK,
       backColor=CODE_BG, borderPadding=(5, 7, 5, 7), spaceBefore=4, spaceAfter=8)
_style("cell", fontName=_FONT, fontSize=9.3, leading=14, textColor=INK)
_style("cell_head", fontName=_FONT_BOLD, fontSize=9.3, leading=14,
       textColor=colors.white)
_style("abstract_kw", fontName=_FONT, fontSize=10.5, leading=17.5,
       textColor=INK, spaceBefore=6)
_style("toc_h", fontName=_FONT_BOLD, fontSize=19, leading=26,
       textColor=INK, spaceBefore=4, spaceAfter=14)
_style("ref_body", fontName=_FONT, fontSize=9.8, leading=15.5,
       alignment=TA_LEFT, textColor=INK, spaceAfter=4, leftIndent=10,
       firstLineIndent=-10)
_style("abstract_title", fontName=_FONT_BOLD, fontSize=19, leading=26,
       textColor=INK, spaceAfter=12)


def P(text, style="body"):
    return Paragraph(text, S[style])


_CJK_RUN = re.compile(
    r"([\u2e80-\u9fff\u3000-\u303f\uff00-\uffef\u2018\u2019\u201c\u201d\u2014\u2026]+)"
)


def _cjk_font_span(text: str) -> str:
    """等宽西文字体没有 CJK 字形；把中文片段包进正文字体，避免渲染成方块。"""
    return _CJK_RUN.sub(r'<font face="%s">\1</font>' % _FONT, text)


def CODE(text):
    return Paragraph(_cjk_font_span(text).replace("\n", "<br/>"), S["code"])


def SP(h=6):
    return Spacer(1, h)


def TABLE(header, rows, widths=None, align_right_cols=()):
    head = [Paragraph(t, S["cell_head"]) for t in header]
    body = [[Paragraph(c, S["cell"]) for c in row] for row in rows]
    data = [head] + body
    if widths is None:
        widths = [CONTENT_W / len(header)] * len(header)
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID", (0, 0), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), NAVY_SOFT))
    t.setStyle(TableStyle(style))
    return t


def R(n, label):
    """行内蓝色引用：R#：来源"""
    return f'<font color="#1a66b0">R{n}：{label}</font>'


def D(n):
    """行内灰色项目内引用：[D#]"""
    return f'<font color="#6b7280">[D{n}]</font>'


# --------------------------------------------------------------------------
# figures（reportlab 原生矢量绘制，保证与正文风格一致）
# --------------------------------------------------------------------------


class _Arrow(Flowable):
    """水平箭头（从左指向右）。"""

    def __init__(self, width=9 * mm, color=NAVY):
        super().__init__()
        self.width, self.height = width, 3 * mm
        self.color = color

    def draw(self):
        c = self.canv
        w, h = self.width, self.height
        c.setStrokeColor(self.color)
        c.setFillColor(self.color)
        c.setLineWidth(1)
        c.line(0, h / 2, w - 3.2, h / 2)
        p = c.beginPath()
        p.moveTo(w - 3.2, h / 2 + 2.4)
        p.lineTo(w, h / 2)
        p.lineTo(w - 3.2, h / 2 - 2.4)
        p.close()
        c.drawPath(p, stroke=0, fill=1)


class WorkflowDiagram(Flowable):
    """图 1：HeavyHeader 工作链。一条主线：向真实编译器提问，交叉归因，四路输出。"""

    def __init__(self, width=CONTENT_W):
        super().__init__()
        self.width = width
        self.height = 52 * mm

    def wrap(self, aw, ah):
        return self.width, self.height

    def _box(self, x, y, w, h, title, lines, navy=False):
        c = self.canv
        c.saveState()
        if navy:
            c.setFillColor(NAVY)
            c.setStrokeColor(NAVY)
            t_col, b_col = colors.white, colors.HexColor("#c8d6e8")
        else:
            c.setFillColor(colors.white)
            c.setStrokeColor(NAVY)
            t_col, b_col = NAVY, INK
        c.setLineWidth(1)
        c.roundRect(x, y, w, h, 2.2 * mm, stroke=1, fill=1)
        c.setFillColor(t_col)
        c.setFont(_FONT_BOLD, 8.8)
        c.drawCentredString(x + w / 2, y + h - 6.2 * mm, title)
        c.setFillColor(b_col)
        c.setFont(_FONT, 7.2)
        step = 4.2 * mm
        yy = y + h - 11.2 * mm
        for ln in lines:
            c.drawCentredString(x + w / 2, yy, ln)
            yy -= step
        c.restoreState()

    def draw(self):
        c = self.canv
        W = self.width
        bw, bh = 44 * mm, 21 * mm
        gap = (W - 4 * bw) / 3
        y_top = self.height - bh - 6 * mm

        # 顶部输入（浅色框）
        self._box(0, y_top, bw, bh, "输入",
                  ["源码树或编译数据库", "真实编译器 GCC·Clang·MSVC"])
        self._box(bw + gap, y_top, bw, bh, "采集",
                  ["一次编译同时采集：", "-H 依赖树 · -ftime-report", "/showIncludes"])
        self._box(2 * (bw + gap), y_top, bw, bh, "归因引擎",
                  ["归因行数 = 行数 × TU 数", "独占闭包 · 重复解析", "启发式标注为估计"])
        self._box(3 * (bw + gap), y_top, bw, bh, "治理输出",
                  ["Markdown/HTML/JSON", "按预期收益排序", "基线快照"])

        # 箭头
        ay = y_top + bh / 2
        for i in range(3):
            x0 = bw + i * (bw + gap)
            c.saveState()
            c.translate(x0 + 1.5 * mm, ay)
            c.setFillColor(NAVY)
            c.setStrokeColor(NAVY)
            c.setLineWidth(1)
            ln = gap - 3 * mm
            c.line(0, 0, ln - 3.2, 0)
            p = c.beginPath()
            p.moveTo(ln - 3.2, 2.4)
            p.lineTo(ln, 0)
            p.lineTo(ln - 3.2, -2.4)
            p.close()
            c.drawPath(p, stroke=0, fill=1)
            c.restoreState()

        # 下行连接线（从「治理输出」底部中点向下，再横向分配到四个命令框）
        cmd_h = 13 * mm
        y_cmd = 4 * mm
        cmd_w = (W - 3 * 6 * mm) / 4
        cx_last = 3 * (bw + gap) + bw / 2
        y_bus = y_cmd + cmd_h + 5 * mm
        c.saveState()
        c.setStrokeColor(NAVY)
        c.setLineWidth(0.9)
        c.line(cx_last, y_top, cx_last, y_bus)
        centers = [i * (cmd_w + 6 * mm) + cmd_w / 2 for i in range(4)]
        c.line(centers[0], y_bus, cx_last, y_bus)
        for cx in centers:
            c.line(cx, y_bus, cx, y_cmd + cmd_h)
        c.restoreState()

        names = [
            ("scan", "扫描 · 报告 · 存基线"),
            ("verify", "删掉重编 · 实测耗时"),
            ("gate", "CI 门禁 · 回归即失败"),
            ("compare", "两次扫描 · 定位回归"),
        ]
        for i, (name, desc) in enumerate(names):
            x = i * (cmd_w + 6 * mm)
            self._box(x, y_cmd, cmd_w, cmd_h, name, [desc], navy=True)


class BenchBars(Flowable):
    """图 2：四个真实项目的头文件归因解析量（行）。数据来自 benchmarks/results/summary.md。"""

    DATA = [
        ("cpp-httplib", 7_523_316),
        ("nlohmann-json", 4_866_676),
        ("spdlog", 1_579_902),
        ("fmt", 331_016),
    ]

    def __init__(self, width=CONTENT_W):
        super().__init__()
        self.width = width
        self.height = 40 * mm

    def wrap(self, aw, ah):
        return self.width, self.height

    def draw(self):
        c = self.canv
        label_w, val_w = 30 * mm, 22 * mm
        bar_max = self.width - label_w - val_w
        top = self.height - 6 * mm
        row_h = 8.2 * mm
        vmax = max(v for _, v in self.DATA)
        for i, (name, v) in enumerate(self.DATA):
            y = top - (i + 1) * row_h
            c.setFillColor(INK)
            c.setFont(_FONT, 8.8)
            c.drawString(0, y + 1.6 * mm, name)
            w = bar_max * v / vmax
            c.setFillColor(NAVY)
            c.rect(label_w, y, w, row_h - 3.2 * mm, stroke=0, fill=1)
            c.setFillColor(INK)
            c.setFont(_MONO, 8)
            c.drawRightString(self.width, y + 1.6 * mm, f"{v:,}")


# --------------------------------------------------------------------------
# doc template：页眉章节名 + 目录登记
# --------------------------------------------------------------------------


class ChapterMarker(Flowable):
    """零尺寸标记：更新页眉当前章节名（用于摘要/目录等无 H1 的页面）。"""

    def __init__(self, name):
        super().__init__()
        self.name = name
        self.width = self.height = 0

    def wrap(self, aw, ah):
        return 0, 0

    def draw(self):
        pass


class ReportDoc(BaseDocTemplate):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._cur_chapter = ""
        self._page_chapter = {}

    def afterFlowable(self, fl):
        if isinstance(fl, ChapterMarker):
            self._cur_chapter = fl.name
        elif isinstance(fl, Paragraph):
            if fl.style.name == "h1":
                txt = fl.getPlainText()
                self._cur_chapter = txt
                self.notify("TOCEntry", (0, txt, self.page))

    def afterPage(self):
        self._page_chapter[self.page] = self._cur_chapter


def _on_body(canvas, doc):
    canvas.saveState()
    chapter = doc._page_chapter.get(doc.page, "")
    canvas.setFont(_FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(M_L, PAGE_H - 13 * mm, f"HeavyHeader  ｜  {chapter}")
    canvas.setFont(_FONT, 9)
    canvas.drawCentredString(PAGE_W / 2, 11 * mm, str(doc.page))
    canvas.restoreState()


def _on_cover(canvas, doc):
    pass


# --------------------------------------------------------------------------
# story
# --------------------------------------------------------------------------


def build(path: str) -> None:
    doc = ReportDoc(
        path,
        pagesize=A4,
        leftMargin=M_L,
        rightMargin=M_R,
        topMargin=M_T,
        bottomMargin=M_B,
        title="HeavyHeader 选题报告",
        author="王子周",
    )
    frame_cover = Frame(M_L, M_B, CONTENT_W, PAGE_H - M_T - M_B, id="cover")
    frame_body = Frame(M_L, M_B, CONTENT_W, PAGE_H - M_T - M_B, id="body")
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[frame_cover], onPage=_on_cover),
            PageTemplate(id="body", frames=[frame_body], onPage=_on_body),
        ]
    )

    story = []

    # ------------------------------------------------------------------ 封面
    story.append(SP(10))
    story.append(P("HeavyHeader", "cover_title"))
    story.append(P("面向 C/C++ 构建效能治理的<br/>编译器驱动成本归因平台", "cover_sub"))
    story.append(P("项目选题报告", "cover_meta"))
    story.append(SP(2))
    story.append(P("作者：王子周", "cover_meta"))
    story.append(P("日期：2026 年 10 月 17 日", "cover_meta"))
    story.append(
        P(
            "申报赛道：第四届开放原子大赛·2026 开源行业解决方案创新赛 · 赛道一（开源基础软件与解决方案）",
            "cover_meta",
        )
    )
    story.append(SP(14))
    story.append(WorkflowDiagram())
    story.append(
        P("图 1  HeavyHeader 工作链。工具不自己解析 C++，而是向真实编译器提问并读取它的回答，"
          "把编译器的真实计时数据与依赖图交叉归因，再交给四个命令消费。图中各环节均已实现并有测试覆盖。",
          "caption")
    )
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())

    # ------------------------------------------------------------------ 摘要
    story.append(ChapterMarker("摘要"))
    story.append(P("摘要", "abstract_title"))
    story.append(
        P(
            "大型 C/C++ 工程的构建时间是真实的成本：一次全量构建动辄数十分钟到数小时，CI 集群机时"
            "与工程师的等待时间都花在里面。而构建慢的最大单一来源，往往不是算法，而是头文件失控——"
            "一个被大量编译单元传递包含的重量级头文件，会让整个项目反复为它付费。现有工具分别解决了"
            "其中一段：Header Hero（Bitsquid，2011）用「行数 × 包含次数」给出成本排序，本文的归因"
            "公式即来自它；ClangBuildAnalyzer 用 -ftime-trace 给出逐头文件累计毫秒；IncludeGuardian "
            "给出结构建议；deheader 注释掉一条 include 再编译来验证可删性。但它们都不回答「删掉这一条，"
            "这次构建具体省多少时间、内存和体积」，因此治理工作缺少量化依据，也无法在 CI 里按实测"
            "净收益防止成本回归。"
        )
    )
    story.append(
        P(
            "HeavyHeader 是一个 Python 实现的命令行平台，核心设计只有一条主线：<b>不自己解析 C++</b>。"
            "它向真实编译器提问——GCC/Clang 的 <font face='%s'>-H</font> 依赖树与"
            " <font face='%s'>-ftime-report</font> 分阶段计时、MSVC 的"
            " <font face='%s'>/showIncludes</font>——并把编译器的真实输出与依赖图交叉归因。基础指标"
            "「归因行数」定义为一个头文件的行数乘以引用它的编译单元数，衡量整个项目为它付出了多少行"
            "解析量；在此之上给出独占闭包（删掉某条 include 后真正会停止被解析的集合）、缺少 include "
            "guard 的重复解析检测、模板实例化成本与可选的前向声明建议。"
            % (_MONO, _MONO, _MONO)
        )
    )
    story.append(
        P(
            "四个命令构成完整治理闭环：<font face='%s'>scan</font> 产出 Markdown / HTML / JSON 报告并可存为"
            "基线；<font face='%s'>verify</font> 把候选 include 真的注释掉、重新编译、实测耗时差异——成本模型再"
            "严谨也仍是模型，权威结论来自实测；<font face='%s'>gate</font> 在 CI 中对基线做回归判定，失败时"
            "指名道姓报出是哪些头文件、哪些 include 行引入的；<font face='%s'>compare</font> 对比两份扫描，"
            "定位成本上升的具体文件。门禁只使用确定性的归因行数判定，墙钟时间因随负载波动只做展示。"
            % (_MONO, _MONO, _MONO, _MONO)
        )
    )
    story.append(
        P(
            "项目当前已完成 v0.1 全部路线图条目、并提前交付 v0.2 的增量扫描、历史看板与 PR 评论机器人，通过 460 个单元测试，CI 覆盖三大操作系统 × 四个 "
            "Python 版本。四个真实开源项目（fmt、nlohmann-json、spdlog、cpp-httplib）的基准显示：54 个"
            "成功编译单元合计 1,430 万行头文件解析量、87 个成本候选；单体头 httplib.h 一项即被归因"
            "42 万行解析量，是最极端的成本热点样本。候选清单只含项目自己的头文件——系统头与第三方头"
            "照常计入统计，也进入建议清单——删除动作发生在项目自己的源文件里，「删掉 #include &lt;iostream&gt;」是完全正常的改动；早先版本把系统头整体排除在候选之外，结果恰好把全部有效改动挡在门外（对照实验里 5/5 个真实改动都是这一类）。头文件来源（HeaderKind）现在只是给读者看的标签，不是过滤器。所有基准数据可由一条命令复现，失败的编译单元被如实报告而不被跳过。"
            "同一份构造工程还已在 GCC / Clang / MSVC 三套真实编译器上跑通横评：三者对项目头的"
            "归因行数完全一致（44），差异只落在系统头解析量与耗时上——这正好印证了成本归因与"
            "工具链无关，也是「结构指标确定性」的直接证据。"
        )
    )
    story.append(
        P(
            "关键词：C/C++；构建效能；头文件依赖；成本归因；编译器计时；持续集成门禁",
            "abstract_kw",
        )
    )
    story.append(PageBreak())

    # ------------------------------------------------------------------ 目录
    story.append(ChapterMarker("目录"))
    story.append(P("目录", "toc_h"))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            name="TOCLevel0",
            fontName=_FONT,
            fontSize=10.5,
            leading=20,
            textColor=INK,
        )
    ]
    toc.dotsMinLevel = 0
    story.append(toc)
    story.append(PageBreak())

    # ------------------------------------------------------ 一 问题背景与需求依据
    story.append(P("一 问题背景与需求依据", "h1"))
    story.append(P("构建时间是真实的成本", "h2"))
    story.append(
        P(
            "C/C++ 项目的构建管线里，编译器在每个编译单元上都要重新解析一遍它 include 到的头文件。"
            "一个被几万个编译单元传递包含的重量级头文件，会让整个项目反复为同一份文本付费；而这样的"
            "头文件往往来自无意的聚合包含、复制粘贴的样板 include，或历史遗留的「保险起见都带上」。"
            "构建变慢之后，团队的典型反应是加机器、上缓存（ccache / distcc），这些手段缓解但不消除"
            "根因：头文件解析量还在那里，只是被分摊到了更多核与更多命中上。"
        )
    )
    story.append(P("现有工具回答的是另一个问题", "h2"))
    story.append(
        P(
            "围绕 include 治理已有不少工具，但它们共同缺席了一个问题：成本量化。下表按「能做什么 / "
            "不能做什么」逐项核对。"
        )
    )
    story.append(
        TABLE(
            ["工具", "能做什么", "不能做什么"],
            [
                ["include-what-you-use",
                 "基于 AST 判断 include 是否必需",
                 "不给成本量化，无法排序优先级"],
                ["cpp-dependencies",
                 "分析模块间依赖关系",
                 "面向架构评审，不回答构建耗时"],
                ["include-gardener",
                 "可视化 include 关系图",
                 "只展示结构，不给归因"],
                ["IDE 的「Include Graph」",
                 "查看单个文件的包含树",
                 "手动、单文件、无项目级视图"],
            ],
            widths=[42 * mm, 62 * mm, 66 * mm],
        )
    )
    story.append(SP(4))
    story.append(
        P(
            "缺少成本量化的直接后果是：治理工作没有优先级——几百条 include 里先动哪一条？删错一条"
            "省不了一秒，删对一条可能省掉百分之几十；同时治理成果无法沉淀为约束——今天清干净的头文件，"
            "明天一个 PR 又把它拖回原样，没有门禁守着。本课题据此把「成本归因 + 实测验证 + 门禁防回归」"
            "作为需要同时交付的三件事。R4：include-what-you-use 项目页"
        )
    )
    story.append(P("面向的用户与代表任务", "h2"))
    story.append(
        P(
            "平台首先面向维护中大型 C/C++ 工程的开发者与构建工程师（Build / Infrastructure 角色），"
            "代表任务是：在不破坏构建的前提下，找出让全量构建变慢的头文件，按预期收益排序精简，"
            "然后在 CI 中固化成果。输入是源码树（或编译数据库 compile_commands.json）与编译器；"
            "输出是按预期收益排序的精简清单、可复现的基准数据，以及一个能在流水线里执行的门禁命令。"
            "该任务是首版的需求假设，实际收益通过同任务工具对照与目标用户试用检验。"
            + D(1)
        )
    )

    # ------------------------------------------------------ 二 平台方案与首版边界
    story.append(P("二 平台方案与首版边界", "h1"))
    story.append(P("一条完整的工作链", "h2"))
    story.append(
        P(
            "工作链从源码树开始：工具发现编译单元（优先采用 compile_commands.json 中的编译命令，"
            "否则递归查找源文件并按命令行参数构造），并行调用真实编译器采集依赖树与分阶段计时，"
            "在归因引擎里计算每条 include 的成本，最后由四个命令消费同一份数据——扫描报告、实测验证、"
            "门禁判定与两次扫描对比。四路输出共享同一套模型与指标定义，不存在两套数字。"
            + D(2)
        )
    )
    story.append(P("首版包含的完整范围", "h2"))
    story.append(
        P(
            "首版（v0.1）交付：归因行数、独占闭包、重复解析检测三类确定性归因；模板实例化成本归因"
            "（每个编译单元精确、按头文件为启发式）；预编译头收益估算（--pch）与前向声明建议"
            "（--forward-decl）两个可选分析；Markdown / 自包含单文件 HTML / JSON 三种报告格式；"
            "基线存储与 CI 门禁；以及覆盖四个真实开源项目、一键可复现的基准套件。"
        )
    )
    story.append(
        TABLE(
            ["编译器", "依赖树来源", "分阶段计时来源"],
            [
                ["GCC 9+", "-H", "-ftime-report"],
                ["Clang 17+", "-H", "-ftime-report"],
                ["MSVC", "/showIncludes", "墙钟计时（MSVC 无等价的文本格式）"],
            ],
            widths=[36 * mm, 55 * mm, 79 * mm],
        )
    )
    story.append(SP(4))
    story.append(
        P(
            "有 compile_commands.json 时严格按其中的 flags 编译——否则同一份代码在不同 flags 下的数字"
            "没有可比性。R1：GCC 预处理输出文档　R2：GCC 调试选项文档　R3：MSVC /showIncludes 文档"
        )
    )
    story.append(P("刻意不做的事", "h2"))
    story.append(
        P(
            "精确判定 include 是否被语义上使用需要 libclang 级别的 AST 分析，那会把使用门槛抬到需要"
            "一整套 LLVM 工具链，并破坏「无第三方运行时依赖、Python 3.10+ 即可安装」的部署约束。"
            "本课题刻意把精确模式排在后续版本（v0.3）且作为可选依赖，默认路径保持零依赖、能在最朴素"
            "的 CI 容器里跑起来。这个取舍与它的理由写在路线图里，不藏在脚注中。" + D(4)
        )
    )

    # ------------------------------------------------------ 三 从扫描到门禁的完整任务
    story.append(P("三 从扫描到门禁的完整任务", "h1"))
    story.append(P("扫描与报告", "h2"))
    story.append(
        P(
            "一次典型扫描的终端输出与报告生成："
        )
    )
    story.append(
        CODE(
            "$ heavyheader scan ./src --format html -o report.html\n"
            "2 TU · 425 条依赖边 · 206,100 行头文件解析量 · 5 个成本候选（其中 1 个疑似未使用）"
        )
    )
    story.append(
        P(
            "报告按预期收益排序给出成本候选，每个候选标注发现类别与证据——「缺少 include guard」"
            "是确定性信号，「疑似未使用」是启发式估计，两者在报告里有明确区分，绝不混排。"
        )
    )
    story.append(P("实测验证：模型让位于实测", "h2"))
    story.append(
        CODE(
            "## lonely.h <- main.cpp\n"
            "   状态：improved — 基线 520 ms -> 移除后 420 ms\n"
            "   实测节省：100 ms (19.2%)\n"
            "\n"
            "## core.h <- util.cpp\n"
            "   状态：compile-error — 移除后编译失败：util.cpp:3:14: error: 'Core' was not declared"
        )
    )
    story.append(
        P(
            "verify 把候选 include 注释掉（不是删除——实验必须可逆）、在临时目录用完全相同的 flags "
            "重新编译、每侧重复 N 次取最小值。编译失败同样是被记录的结果，不会被静默丢弃：它说明这条 "
            "include 是必需的，这本身就是有价值的治理结论。"
        )
    )
    story.append(P("明确结论：每个候选一个「值不值得删」", "h2"))
    story.append(
        P(
            "启发式回答「这个头还用不用」，是个可能性问题；开发者问的是「这行值不值得删」，是个决策"
            "问题。决策层（attrib/advice.py）把探针证据收敛成三个明确结论之一，规则是确定性纯函数："
            "**建议删除**（编译通过且耗时下降超过 2% 噪声线）、**保留**（编译器已否决，不必再试）、"
            "**需人工判断**（编译可行但收益在噪声内，附倾向与理由）。未实测的候选显示「未实测」——"
            "没测过就不下判断。每个结论按功能影响、维护成本、构建体验三个维度列依据，实测数据覆盖"
            "编译时间、编译器内存分配（GGC 自报）与目标文件体积三个口径，报告附完整复现命令。"
        )
    )
    story.append(
        TABLE(
            ["结论", "数量", "含义"],
            [
                ["建议删除", "<b>1</b>", "编译通过且耗时下降超过噪声线"],
                ["保留", "187", "编译器已否决或定位不到该注释哪一行"],
                ["需人工判断", "34", "编译可行但收益在噪声内，全部附倾向"],
            ],
            widths=[34 * mm, 20 * mm, 106 * mm],
        )
    )
    story.append(SP(4))
    story.append(
        P("表：nlohmann/json 测试目标 222 个候选全部真实编译后的结论分布"
          "（experiments/decision_check.py，可一键复现）。唯一一条「建议删除」的三个维度全部实测："
          "unit-windows_h 去掉 windows.h，编译 1610 ms → 910 ms（−43.5%）、GGC 内存 "
          "301,052 kB → 168,960 kB、目标文件 63,004 B → 62,972 B。体积那一行是阴性结果——"
          "收益全部在编译时间侧——这类数据与正向收益一样必须实测，不能省略。" + D(6)
        )
    )
    story.append(P("四道门槛的实测结论（含两项部分通过）", "h2"))
    story.append(
        P(
            "项目按预注册判据跑了四道门槛，结果写在正文而不是附录里。"
            "<b>G1 公平对照：部分通过</b>——与 ClangBuildAnalyzer 在同一批 25 个编译单元、"
            "同一编译器、同一候选规则下对照，口径统一后 Top-10 重合 7/10、Spearman ρ ≈ 0.68，"
            "只能说明中度一致，<b>不能</b>说明我们更准。"
            "<b>G2 优化闭环：成立但有限</b>——第一版协议是 0/10，拆开看有三个来源：候选空间只覆盖"
            "编译单元自己的 include（83 条里仅 3 条疑似未使用）、测量协议把机器漂移整块算成收益"
            "（聚合 A/A 2.005%，改成交替后 0.644%）、以及「#include &lt;regex&gt;」这一行的文件名"
            "污染了用法判定使结论恒为「在用」。逐个修掉并重做：doctest 上带可行性闸门的 16 条候选"
            "全部测完——8 条能编过、1 条被证实可删且真省时（删公共头里一条 include，24 个单元"
            "全通过、聚合 −3.01%、复测通过），即 1/16；关闸门的 5 条里 0 条能编过。"
            "换到 json 与 zlib 都是 0/5 且闸门一条都不推荐。"
            "注入-召回 2/2 命中"
            "（头文件级排 1/17、单元级排 25/61，各两遍独立运行均过线）。"
            "据此撤回「自动找到优化」，但可以说<b>在真实工程上产出过一条经复测的优化</b>。"
            "<b>G3 门禁：成立但范围有限</b>——注入回归 3/3 检出、无回归 3/3 通过、构建损坏 3/3 拦截，"
            "单次 3.38 秒；盲区是「行数不变但编译变慢」的回归测不出。"
            "<b>G4 工程开销：成立</b>——扫描开销为纯编译的 1.15×（GCC）/ 1.20×（Clang），峰值 9.2 MB，"
            "三次运行结果一致。"
            "四条合起来，产品承诺收窄为：<b>不替你找到优化，而是把「看起来能省」变成"
            "「确认能省 / 确认不能省」，并固化成能拦住回归的基线</b>——在 doctest 上它确实"
            "产出过一条经复测的优化，全量命中率 1/16（放行的候选还有一半编不过），而 json 与 zlib"
            "都是 0/5，两者都如实写出。"
        )
    )
    story.append(P("门禁：失败时指名道姓", "h2"))
    story.append(
        CODE(
            "$ heavyheader gate ./src --baseline baseline.json --max-regression 5\n"
            "头文件解析量：206,100 -> 241,300 (+17.1%)\n"
            "门禁失败：\n"
            "  - header cost regressed +17.1% (206100 -> 241300 attributed lines), budget is +5.0%\n"
            "    worst offender: include/bloated.h 0 -> 22312 lines (+100.0%)"
        )
    )
    story.append(
        P(
            "一个只说「成本回归 +12%」的门禁毫无用处；能定位到具体头文件与 include 行的门禁才会被"
            "留下来。失败信息直接给出最严重的回归者及其前后行数，构建日志里的每一个人都能立刻知道"
            "该找谁、改哪里。"
        )
    )

    # ------------------------------------------------------ 四 成本归因如何保持准确
    story.append(P("四 成本归因如何保持准确", "h1"))
    story.append(P("归因行数：定义与局限", "h2"))
    story.append(
        P(
            "归因行数 = 头文件行数 × 引用它的编译单元数。C++ 项目的预处理与解析成本一阶近似正比于"
            "「编译器被迫读了多少头文件文本」，每个被解析的头文件在每个引用它的编译单元里贡献一次"
            "自己的行数。这个指标对同一份代码是确定性的，因此适合作为门禁判定依据。它的局限同样明确："
            "行数不等于解析难度，一个 100 行的重型模板头可能比 1000 行的纯声明头更慢——它是相对排序"
            "指标，不是绝对耗时的预测器。" + D(1)
        )
    )
    story.append(P("独占闭包：与 lint 工具的本质区别", "h2"))
    story.append(
        P(
            "删掉一条 include 之前必须回答：这个头文件还能不能从别的 include 到达？如果能，删掉当前"
            "这条并不会让它停止被解析——钱已经花了。独占闭包定义了删掉某条直接 include 后唯一会停止"
            "被解析的头文件集合，只有它才是真实收益。实现上有一个值得记录的真实 bug：早先版本把候选"
            "自身也放进了「已付费」并集，导致每个候选的独占集合恒为空，于是所有项目都报告「没有可优化"
            "项」——这个症状与「确实没问题」在输出上完全无法区分，因此靠单元测试而非代码审查防守。"
            + D(1) + D(5)
        )
    )
    story.append(P("重复解析检测：唯一的「必赚项」", "h2"))
    story.append(
        P(
            "-H 只为编译器实际处理过的包含打印一行，带 guard 的头文件在同一编译单元内物理上不可能"
            "出现第二次。因此「同一编译单元内出现多次」必然意味着缺少 include guard，修复（补"
            " #pragma once）永远安全且收益立竿见影——这是全部发现里唯一标注为必赚项的分类。" + D(1)
        )
    )
    story.append(P("模板实例化成本与实测方法学", "h2"))
    story.append(
        P(
            "-ftime-report 从 GCC 9 起报告各阶段耗时，对模板重型 C++，「模板实例化」常是最大单项——"
            "实测包含 nlohmann/json.hpp 的文件，39% 的构建时间花在这里。每个编译单元的秒数与占比是"
            "精确的；把它按头文件分摊是启发式，报告内明确标注。verify 侧的方法学：注释而非删除、临时"
            "目录重编并补上原文件所在目录的 -I（#include \"...\" 相对于包含它的文件所在目录解析）、"
            "每侧重复 N 次取最小值——噪声只会让测量变慢不会变快，最小值是最接近真实成本的估计；相对"
            "差异低于 2% 视为噪声地板，把 1% 的波动报告成优化成果是不诚实的。" + R(2, "GCC 调试选项文档")
            + "　" + D(1)
        )
    )
    story.append(P("门禁判定公式", "h2"))
    story.append(
        CODE(
            "regression = (attributed_lines_after - attributed_lines_before)\n"
            "             / attributed_lines_before * 100%\n"
            "fail if regression > max_regression_percent   (默认 5%)"
        )
    )
    story.append(
        P(
            "默认 5% 刻意保守：在噪声上误触发的门禁会被关掉，而关掉的门禁保护不了任何东西。5% 是"
            "「明显变化」与「正常波动」之间的实用分界。" + D(1)
        )
    )

    # ------------------------------------------------------ 五 真实项目基准
    story.append(P("五 真实项目基准", "h1"))
    story.append(
        P(
            "基准覆盖四个真实开源项目，由 benchmarks/run_all.py 一键生成，每个项目的编译 flags 都是"
            "实际跑通、能产出干净数据的那组并记录在案。数据取自 GCC 16.2.0 (MinGW-w64) + "
            "Python 3.13，54 个成功编译单元。" + D(3)
        )
    )
    story.append(
        TABLE(
            ["项目", "编译单元", "依赖边", "头文件解析量", "成本候选", "疑似未使用"],
            [
                ["fmt", "3 成功 / 1 失败", "651", "331,016", "4", "0"],
                ["nlohmann-json", "25 成功 / 0 失败", "9,294", "4,866,676", "24", "0"],
                ["spdlog", "8 成功 / 17 失败", "3,246", "1,579,902", "41", "0"],
                ["cpp-httplib", "18 成功 / 7 失败", "14,724", "7,523,316", "18", "0"],
                ["<b>合计</b>", "<b>54 成功</b>", "<b>27,915</b>", "<b>14,300,910</b>", "<b>87</b>", "<b>0</b>"],
            ],
            widths=[30 * mm, 32 * mm, 20 * mm, 36 * mm, 24 * mm, 28 * mm],
        )
    )
    story.append(SP(6))
    story.append(BenchBars())
    story.append(
        P("图 2  四个真实项目的头文件归因解析量（行）。cpp-httplib 的单体头 httplib.h 一项即被归因 "
          "42 万行解析量。数据来源：benchmarks/results/summary.md，可一键复现。",
          "caption")
    )
    story.append(P("代表性成本热点", "h2"))
    story.append(
        TABLE(
            ["项目", "头文件", "行数", "引用 TU", "归因行数"],
            [
                ["nlohmann-json", "json.hpp", "8,177", "24", "196,248"],
                ["cpp-httplib", "httplib.h", "23,343", "18", "420,174"],
                ["spdlog", "fmt/bundled/format.h", "4,395", "8", "35,160"],
                ["fmt", "format.h", "4,709", "2", "9,418"],
            ],
            widths=[32 * mm, 52 * mm, 22 * mm, 22 * mm, 42 * mm],
        )
    )
    story.append(SP(4))
    story.append(
        P(
            "读这些数字时要注意三件事。第一，失败的编译单元被如实报告，没有为了凑数字跳过——真实"
            "项目常需 CMake 生成的配置头才能编译，本机没有 CMake，spdlog 的 17 个失败单元与 cpp-httplib "
            "缺 OpenSSL 头的 7 个失败单元都写在报告顶部。第二，具体数字随编译器版本、平台、负载变化，"
            "不应逐位复现；可复现的是结论方向与数量级。第三，「疑似未使用」是启发式而不是安全性结论，"
            "权威判定来自 verify：真的删掉、重新编译，并给出明确结论（见第三章）。第四，候选清单"
            "包含系统头——删除动作发生在项目自己的源文件里；早先版本排除系统头，恰好挡掉了全部"
            "有效改动。本组数据里「疑似未使用」为 0：判定项目头是否被使用需要 AST 级分析，"
            "这正是路线图 v0.3（libclang 后端）要补的精度缺口。" + D(3)
        )
    )

    # ------------------------------------------------------ 六 CI 门禁与治理流程
    story.append(P("六 CI 门禁与治理流程", "h1"))
    story.append(P("从一次性分析到持续治理", "h2"))
    story.append(
        P(
            "单次扫描的价值随时间衰减：报告生成那一刻起，代码就在继续变化。因此平台把「基线快照 + "
            "回归门禁」作为与扫描同等的一等能力。治理流程是：在干净状态扫描并保存基线；此后每个 PR "
            "在 CI 里重新扫描并与基线对比；归因行数回归超过阈值（默认 5%）则流水线失败，失败信息"
            "指名道姓。compare 命令支持对任意两份扫描结果做同样的事，用于事后定位「构建什么时候变慢的」。"
        )
    )
    story.append(P("为什么门禁只用确定性指标", "h2"))
    story.append(
        P(
            "墙钟时间随机器、负载、时钟状态波动，是最吵的数字。gate 不用它做判定依据，只做展示；"
            "跨机器、跨时段不比较绝对值。判定依据只有归因行数——它对同一份代码是确定性的，同样的输入"
            "必然给出同样的数字，这是门禁可信的前提。趋势图里墙钟时间与归因行数一起画，但归因行数是"
            "主指标。" + D(1)
        )
    )

    # ------------------------------------------------------ 七 诚实的边界
    story.append(P("七 诚实的边界", "h1"))
    story.append(
        P(
            "这个工具不做什么，和它做什么同样重要。以下每一条都是设计决定而非能力缺口，均有对应的"
            "理由与替代方案。" + D(8)
        )
    )
    story.append(
        TABLE(
            ["已知局限", "含义与对策"],
            [
                ["不判定 include 是否「未使用」的语义真相",
                 "文本启发式会被副作用包含（如 iostream 的流初始化）误判；权威结论来自 verify 实测"],
                ["前向声明建议必须人工确认",
                 "「只以指针/引用形式使用」是语义属性，正则判断可能被宏、typedef、模板参数欺骗；"
                 "报告给出类型名、行号与阻断类型供人在数秒内确认或否决"],
                ["不做精确的 AST 级分析",
                 "精确模式需要 libclang 全套工具链，与零依赖部署约束冲突；排在 v0.3 作为可选依赖"],
                ["verify 不改写用户代码",
                 "实验只在临时目录进行，原始文件一个字节都不会动"],
                ["不跨机器比较绝对耗时",
                 "墙钟时间随负载波动；门禁只对比确定性的归因行数与相对比例"],
                ["-fsyntax-only 不含代码生成阶段",
                 "报告默认不含 opt 与 generate 的成本；--full-compile 可包含但扫描明显变慢"],
                ["ccache 环境下 verify 需先清缓存",
                 "否则测到的是缓存命中时间而不是编译时间"],
            ],
            widths=[62 * mm, 108 * mm],
        )
    )
    story.append(SP(4))
    story.append(
        P(
            "方法论文档为报告里的每一个指标给出定义：「如果一个指标没法在这里查到定义，那它不应该"
            "出现在报告里。」" + D(1)
        )
    )

    # ------------------------------------------------------ 八 报告与可复现
    story.append(P("八 报告与可复现", "h1"))
    story.append(P("三种格式，一份数据", "h2"))
    story.append(
        P(
            "scan 同时支持 Markdown（评审与 PR 评论）、HTML（自包含单文件，可直接分享或挂内网）与 "
            "JSON（供基线与后续工具消费）三种输出，全部渲染自同一份内部结果，不存在两套数字。HTML "
            "报告内嵌全部样式与数据，无外部资源引用，拷贝到任何机器都能打开。"
        )
    )
    story.append(P("基准可一键重跑", "h2"))
    story.append(
        P(
            "benchmarks/run_all.py 一键重跑全部四个项目的基准并生成汇总。每个项目的 flags 都记录在"
            "基准脚本与结果说明里——写下来是为了下次能复现。compile_commands.json 存在时严格采用其中"
            "的编译命令，这是「同一份代码的数字可比」的保障。" + D(3)
        )
    )

    # ------------------------------------------------------ 九 技术路线与模块分工
    story.append(P("九 技术路线与模块分工", "h1"))
    story.append(P("分层结构", "h2"))
    story.append(
        TABLE(
            ["模块", "职责"],
            [
                ["drivers/", "向编译器提问：GCC / Clang / MSVC 各自的方言"],
                ["parse/", "解析 -H 依赖树、-ftime-report 计时、make 风格依赖文件"],
                ["attrib/", "成本归因：依赖图、独占闭包、重复解析检测、使用启发式"],
                ["scanner.py", "编排：发现编译单元 → 并行编译 → 汇总统计"],
                ["verify.py", "实测验证：注释掉、重编译、计时"],
                ["baseline.py", "基线、对比、CI 门禁"],
                ["report/", "Markdown / HTML（自包含单文件）/ JSON 渲染"],
            ],
            widths=[36 * mm, 134 * mm],
        )
    )
    story.append(SP(4))
    story.append(P("几个刻意的设计选择", "h2"))
    story.append(
        P(
            "一次编译同时拿到依赖树、分阶段计时与 guard 缺失三项数据（-H 与 -ftime-report 都写 "
            "stderr，可并行收集）——第二次编译会让扫描耗时翻倍，更糟的是两次编译之间头文件若发生变化，"
            "两边数据会互相矛盾。扫描默认用 -fsyntax-only 而不是完整编译，换取数量级的速度提升；"
            "依赖图上的闭包计算迭代而非递归，喂给畸形输入时不会栈溢出。完整的取舍记录在架构文档里。"
            + D(2)
        )
    )
    story.append(P("测试策略", "h2"))
    story.append(
        P(
            "460 个单元测试覆盖解析、归因、门禁、verify、增量缓存、多编译器对比与并行编译健壮性各子系统。测试样本优先用真实编译器输出"
            "逐字复制而不是手写构造——解析器的输入是编译器方言的文本，手写样本会悄悄偏离真实格式。"
            "上一节提到的独占闭包 bug 正是因为「症状与正常输出无法区分」，才以回归测试的方式永久防守。"
            + D(5)
        )
    )

    story.append(P("真实工程上的完整构建实测", "h2"))
    story.append(
        P(
            "为避免「只有解析量、没有真实构建收益」的质疑，另做了一组可复现的对照实验："
            "对象是 nlohmann/json v3.11.3 的测试目标（63 个编译单元，源码以 SHA-256 固定），"
            "两臂各 5 次干净构建。按工具的预编译头建议实施后，完整构建墙上时间中位数 "
            "43.2 s → 40.1 s（−7.1%），编译 CPU 292 s → 259 s（−11.4%），"
            "63 个测试套件的 4,264,414 条断言逐个一致。" + D(4)
        )
    )
    story.append(
        P(
            "估算与实测分开陈述、从不合并：工具估算「消掉 91.2% 的头文件解析量」（按行计，"
            "是上限），实测是「编译 CPU −11.4%」（按秒计）。由二者反推，头文件解析约占编译 CPU "
            "的 17%——这正是「估算 91% 落地为实测 11%」的原因，也是不能把估算当秒数引用的理由。"
            + D(4)
        )
    )
    story.append(
        P(
            "同一实验还给出了一条对工具不利的结论，第一版报告里照实写了：在「逐个删 include」"
            "这件事上，按调用频次排序的经验法则 12 次试验命中 3 个有效改动，而工具按收益排序的"
            "5 个候选全部编译失败。" + D(4)
        )
    )
    story.append(
        P(
            "事后复核发现，这句话里有一半不是工具的错，是**测量的错**：那一版的基线用 8 路并行"
            "测量、每次试验却单独编译，基线被 CPU 争抢抬高约 25%。把那三个「有效改动」串行复测，"
            "结果是 +0.4% / −1.1% / −0.1%——即全是噪声。修正协议后（基线串行、每次试验 3 次取"
            "最小、并用 A/A 空对照现场量出噪声底来定阈值）重跑，两个策略都没能在这个工程里找到"
            "站得住的单点删除收益。" + D(4)
        )
    )
    story.append(
        P(
            "另一半确实是工具的错，共三处结构性缺陷，均已修复：① 用法判定只看候选头**自己**那段"
            "文本，而删掉一行真正消失的是它往下拉的**整批头**——`TEST_CASE` 在 `doctest.h` 里、"
            "不在 `doctest_compatibility.h` 里，于是 5 个候选全被判成「没人用」；② 按「全项目可省"
            "行数」排序，收益最大的头正是最不可能可删的，它排第一；③ 候选池直接排除了系统头，而"
            "编辑动作发生在自己的源文件里，系统头同样可提议。" + D(4)
        )
    )
    story.append(
        P(
            "修复后的排序改为「可采纳性优先」：先按结论分层（建议删除 / 需人工判断 / 保留与已证伪），"
            "层内再按**本单元**预计省下的秒数排。用一个零构建"
            "试验的回顾性检验背书——拿实验里已实测的 17 个结局当答案，只看新排序怎么排：13 个"
            "已知失败候选全部被压后，已知可删的 `unit-bjdata/iostream` 从「根本不在候选空间」"
            "升到第 3 名。" + D(4)
        )
    )
    story.append(
        P(
            "但这里必须把边界说清楚，否则就是把修好的排序又吹大了：**扫描排序的意义是少花构建"
            "试验，不是替代构建试验。**「这一行能不能删」的最终判定只能来自真的编译一次——`verify` "
            "做的正是这件事（注释掉那一行、重编、测时间）。所以报告里的口径是：估算归估算、"
            "实测归实测、证伪归证伪，三栏从不合并。" + D(4)
        )
    )

    # ------------------------------------------------------ 十 已有基础与开发起点
    story.append(P("十 已有基础与开发起点", "h1"))
    story.append(P("当前可以接着做什么", "h2"))
    story.append(
        P(
            "项目不是一个构想：v0.1 路线图全部条目已完成，代码以 Apache-2.0 许可证公开在 "
            "github.com/wzzrambler/heavyheader，50+ 个提交、148 个文件、工作区无未提交改动。下表按"
            "「已有基础 / 已核对的内容 / 接下来的开发工作」汇总。" + D(4)
        )
    )
    story.append(
        TABLE(
            ["已有基础", "已经核对的内容", "接下来的开发工作"],
            [
                ["七命令 CLI（scan / verify / gate / compare / history / pr-comment / toolchains）",
                 "终端输出样例与 460 个单元测试",
                 "PR 评论机器人（pr-comment 子命令，v0.2 已交付，走 GitHub 的 gh）",
                 "把两次扫描对比渲染成 GitHub 风格评论，幂等地更新而非堆积",
                 "GitLab 后端仍待做（需另一套 API）"],
                ["三编译器驱动（GCC / Clang / MSVC）",
                 "四真实项目基准，54 个编译单元的采集记录",
                 "多编译器对比（toolchains 子命令，v0.2 已交付）",
                 "同一份代码在多个编译器下的成本矩阵，支撑工具链迁移决策"],
                ["增量扫描（--cache，v0.2 已交付）",
                 "文件指纹两级判定；已验证增量结果与全量逐字一致",
                 "只重编受改动影响的编译单元 —— 已完成"],
                ["三种报告格式与自包含 HTML",
                 "HTML 单文件在无外部资源下可打开",
                 "历史趋势看板（history 子命令 + --serve，v0.2 已交付）",
                 "时间序列聚合 + 本地 HTTP 看板，判定口径与 gate 完全一致",
                 "多人共享的趋势服务仍待做（需持久化存储与多端访问）"],
                ["CI 门禁与基线",
                 "门禁判定公式与 5% 默认阈值的理由记录",
                 "按头文件分别设预算：单个头也不许悄悄长胖"],
                ["发布流水线",
                 "ci.yml 三任务（测试 / lint / self-scan）+ coverage + release",
                 "PyPI 正式发版（release.yml 已具备 tag 触发能力）"],
            ],
            widths=[52 * mm, 60 * mm, 58 * mm],
        )
    )
    story.append(SP(4))
    story.append(
        P(
            "持续集成覆盖三大操作系统（ubuntu / windows / macos）× 四个 Python 版本（3.10–3.13），"
            "fail-fast 关闭以保证每个组合都被完整验证。self-scan 任务用 HeavyHeader 扫描它自己的源码树"
            "——工具吃自己的狗粮。" + D(5)
        )
    )

    # ------------------------------------------------------ 十一 同类工具与项目价值
    story.append(P("十一 同类工具与项目价值", "h1"))
    story.append(P("定位差异", "h2"))
    story.append(
        P(
            "与同类工具相比，HeavyHeader 不试图替代它们，而是补上它们共同缺席的一层：<b>成本</b>。"
            "IWYU 仍然是最精确的「该不该删」判定者；HeavyHeader 回答的是「删了值不值、先删哪个、"
            "删完怎么守住」。"
        )
    )
    story.append(
        TABLE(
            ["能力", "IWYU 等结构分析工具", "HeavyHeader"],
            [
                ["include 必需性判定", "精确（AST 级）", "启发式 + verify 实测兜底"],
                ["成本量化与排序", "无", "归因行数 + 分阶段计时"],
                ["删掉的预期收益", "无", "独占闭包精确到头文件集合"],
                ["实测验证", "无", "注释-重编-计时，min-of-N"],
                ["CI 门禁", "无", "基线 + 阈值回归判定"],
                ["部署门槛", "需 LLVM 工具链", "pip 安装，零第三方运行时依赖"],
            ],
            widths=[44 * mm, 62 * mm, 64 * mm],
        )
    )
    story.append(SP(4))
    story.append(
        P(
            "项目希望验证的价值主张是：把「头文件成本」从开发者的直觉变成工程数据之后，构建效能"
            "治理可以像测试覆盖率一样被持续度量与门禁把守。实际收益通过同任务工具对照（有/无成本"
            "数据时的治理效率）与目标用户试用检验，相关方法见方法论文档。" + D(1)
        )
    )

    # ------------------------------------------------------ 十二 团队协作与后续开发
    story.append(P("十二 团队协作与后续开发", "h1"))
    story.append(P("申报与协作模式", "h2"))
    story.append(
        P(
            "本项目以开源社区主体类型（个人）申报第四届开放原子大赛·2026 开源行业解决方案创新赛"
            "赛道一。项目采用标准的开源协作模式：Issue 报告缺陷与需求、PR 贡献代码、CONTRIBUTING "
            "说明「什么类型的贡献最被需要」、行为规范与安全披露流程齐备。Apache-2.0 许可证对贡献者"
            "与使用者都明确，含专利授权条款，且与生态中大量 C++ 项目的许可证兼容。" + D(7)
        )
    )
    story.append(P("后续开发路线", "h2"))
    story.append(
        P(
            "v0.2「从工具到平台」聚焦团队持续使用：本地历史趋势看板、PR 评论机器人（GitHub）、"
            "多编译器对比（GCC / Clang / MSVC）均已交付；剩余多人共享的趋势服务（需持久化存储）、"
            "GitLab 后端。"
            "v0.3「精确模式」引入 libclang AST 后端作为可选依赖，把候选池的**精度**提高到 AST 级——"
            "当前 222 个候选里 187 个编译不过，这是文本启发式的真实上限；结论形态（建议删除/保留/"
            "需人工判断）与三维度实测框架已经就位，libclang 补的是命中率。默认路径保持零依赖不变。"
            "排序即优先级，完整路线图见仓库。" + D(4)
        )
    )
    story.append(P("比赛节点", "h2"))
    story.append(
        P(
            "作品材料（介绍文档 PDF、演示视频、代码仓库）于 2026 年 10 月 31 日 24:00 前提交；"
            "11 月 6 日公布入围名单；11 月 13 日总决赛线下路演。当前仓库即评审可见状态，评审期间"
            "仅修复缺陷、不引入新功能，保证评委看到的与提交时一致。" + D(7)
        )
    )

    # ------------------------------------------------------ 参考资料
    story.append(P("参考资料", "h1"))
    story.append(
        P("外部资料均为原始发布页面或官方文档，访问核对日期为 2026 年 10 月 16—17 日。", "body")
    )
    refs = [
        ("R1", "GCC, <i>Preprocessor Output</i>（-H 选项：打印被实际处理的头文件）。"
               "<font color='#1a66b0'>https://gcc.gnu.org/onlinedocs/gcc/Preprocessor-Output.html</font>"),
        ("R2", "GCC, <i>Debugging Options</i>（-ftime-report：编译各阶段计时）。"
               "<font color='#1a66b0'>https://gcc.gnu.org/onlinedocs/gcc/Debugging-Options.html</font>"),
        ("R3", "Microsoft, <i>/showIncludes (List Include Files)</i>。"
               "<font color='#1a66b0'>https://learn.microsoft.com/cpp/build/reference/showincludes-list-include-files</font>"),
        ("R4", "include-what-you-use 项目主页。"
               "<font color='#1a66b0'>https://include-what-you-use.org/</font>"),
        ("R5", "tomtom-international/cpp-dependencies 仓库。"
               "<font color='#1a66b0'>https://github.com/tomtom-international/cpp-dependencies</font>"),
        ("R6", "fedorbondarczyk/include-gardener 仓库。"
               "<font color='#1a66b0'>https://github.com/fedorbondarczyk/include-gardener</font>"),
        ("R7", "nlohmann/json 仓库（基准对象之一）。"
               "<font color='#1a66b0'>https://github.com/nlohmann/json</font>"),
        ("R8", "yhirose/cpp-httplib 仓库（基准对象之一）。"
               "<font color='#1a66b0'>https://github.com/yhirose/cpp-httplib</font>"),
        ("R9", "开放原子开源基金会，开放原子大赛信息页。"
               "<font color='#1a66b0'>https://www.openatom.org</font>"),
    ]
    for tag, txt in refs:
        story.append(P(f"· <b>{tag}</b>　{txt}", "ref_body"))
    story.append(SP(8))
    story.append(P("项目设计与实测依据", "h2"))
    story.append(
        P("项目内文档按主题列出，全部随公开仓库提交，详细方法、接口与试验记录可供评审查证。", "body")
    )
    drefs = [
        ("D1", "docs/methodology.md：《方法论：这些数字是怎么算出来的》——全部九个指标的定义与局限。"),
        ("D2", "docs/architecture.md：《架构》——分层结构、数据流与六个刻意的设计选择。"),
        ("D3", "benchmarks/：run_all.py 基准脚本与 results/summary.md 汇总——四个真实项目的实测数据。"),
        ("D4", "ROADMAP.md：《路线图》——v0.1 / v0.2 / v0.3 排序即优先级。"),
        ("D5", "tests/：418 个单元测试，样本优先采用真实编译器输出逐字复制。"),
        ("D6", "experiments/results/decision-20261018.md：《删除建议决策层实测》——222 个候选全部真实编译后的结论分布、三维度数据与竞品交叉核对。"),
        ("D7", "SUBMISSION.md：《提交清单》——比赛材料、节点与剩余步骤。"),
        ("D8", "README.md：项目主页——能力总览、诚实的边界与快速开始。"),
        ("D9", ".github/workflows/：ci.yml（三系统 × 四 Python 版本测试 / lint / self-scan）、"
               "coverage.yml（Codecov）、release.yml（tag 触发发布）。"),
    ]
    for tag, txt in drefs:
        story.append(P(f"· <b>{tag}</b>　{txt}", "ref_body"))

    doc.multiBuild(story)
    print("OK:", path)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "HeavyHeader-选题报告.pdf")
