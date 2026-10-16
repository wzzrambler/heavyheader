"""生成《作品介绍文档》PDF。

对标参考材料《超级工厂_选题报告.pdf》的组织方式：政策/学术引用编号、
明确的首版边界、手算的完整任务示例、同类产品逐项对比表、验收基准。
差异化在于本文档描述的是一个**已可运行、有实测数据**的项目。

诚实性约束（写进文档本身）：
- 只引用实际使用过的资料，不编造政策条文或论文；
- 区分「我们实测的数据」与「待验证的假设」；
- 已知局限单独成章，不藏在附注里。
"""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------

_FONT = "YaHei"
_FONT_BOLD = "YaHeiBold"
_MONO = "Cascadia"

pdfmetrics.registerFont(TTFont(_FONT, "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont(_FONT_BOLD, "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
pdfmetrics.registerFont(TTFont(_MONO, "C:/Windows/Fonts/CascadiaMono.ttf"))

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------

INK = colors.HexColor("#1c2128")
MUTED = colors.HexColor("#5b6470")
ACCENT = colors.HexColor("#2f6feb")
RULE = colors.HexColor("#d8dde3")
CODE_BG = colors.HexColor("#f4f6f8")
CODE_INK = colors.HexColor("#243b53")
TH_BG = colors.HexColor("#eef1f5")

# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------

ss = getSampleStyleSheet()

S = {
    "title": ParagraphStyle(
        "title", parent=ss["Title"], fontName=_FONT_BOLD, fontSize=26,
        leading=34, textColor=INK, alignment=TA_CENTER, spaceAfter=6,
    ),
    "subtitle": ParagraphStyle(
        "subtitle", parent=ss["Title"], fontName=_FONT, fontSize=13,
        leading=20, textColor=MUTED, alignment=TA_CENTER, spaceAfter=4,
    ),
    "meta": ParagraphStyle(
        "meta", parent=ss["Normal"], fontName=_FONT, fontSize=10.5,
        leading=17, textColor=MUTED, alignment=TA_CENTER,
    ),
    "h1": ParagraphStyle(
        "h1", parent=ss["Heading1"], fontName=_FONT_BOLD, fontSize=16,
        leading=24, textColor=INK, spaceBefore=18, spaceAfter=10, keepWithNext=1,
    ),
    "h2": ParagraphStyle(
        "h2", parent=ss["Heading2"], fontName=_FONT_BOLD, fontSize=12.5,
        leading=20, textColor=INK, spaceBefore=14, spaceAfter=7, keepWithNext=1,
    ),
    "h3": ParagraphStyle(
        "h3", parent=ss["Heading3"], fontName=_FONT_BOLD, fontSize=11,
        leading=18, textColor=INK, spaceBefore=10, spaceAfter=5, keepWithNext=1,
    ),
    "body": ParagraphStyle(
        "body", parent=ss["Normal"], fontName=_FONT, fontSize=10.5,
        leading=18.5, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=7,
        firstLineIndent=0,
    ),
    "quote": ParagraphStyle(
        "quote", parent=ss["Normal"], fontName=_FONT, fontSize=10,
        leading=17, textColor=MUTED, alignment=TA_JUSTIFY, spaceAfter=7,
        leftIndent=14, borderPadding=(0, 0, 0, 8),
    ),
    "code": ParagraphStyle(
        "code", parent=ss["Normal"], fontName=_MONO, fontSize=8.6,
        leading=13.5, textColor=CODE_INK, alignment=TA_LEFT,
        backColor=CODE_BG, borderPadding=(6, 7, 6, 7), spaceAfter=8,
    ),
    "caption": ParagraphStyle(
        "caption", parent=ss["Normal"], fontName=_FONT, fontSize=9,
        leading=15, textColor=MUTED, alignment=TA_CENTER, spaceBefore=4,
    ),
    "cell": ParagraphStyle(
        "cell", parent=ss["Normal"], fontName=_FONT, fontSize=9,
        leading=14.5, textColor=INK, alignment=TA_LEFT,
    ),
    "cellhead": ParagraphStyle(
        "cellhead", parent=ss["Normal"], fontName=_FONT_BOLD, fontSize=9,
        leading=14.5, textColor=INK, alignment=TA_LEFT,
    ),
    "cellmono": ParagraphStyle(
        "cellmono", parent=ss["Normal"], fontName=_MONO, fontSize=8,
        leading=13, textColor=CODE_INK, alignment=TA_LEFT,
    ),
}


def P(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def CODE(text: str) -> Paragraph:
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    return Paragraph(escaped.replace("\n", "<br/>"), S["code"])


def SP(h: float = 6) -> Spacer:
    return Spacer(1, h)


def _cell(text: str, style: str = "cell") -> Paragraph:
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(escaped.replace("\n", "<br/>"), S[style])


def TABLE(header: list[str], rows: list[list[str]], widths: list[float] | None = None):
    data = [[_cell(h, "cellhead") for h in header]]
    for row in rows:
        data.append([_cell(c) for c in row])
    if widths is None:
        widths = [420 / len(header)] * len(header)
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TH_BG),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return t


# --------------------------------------------------------------------------
# page furniture
# --------------------------------------------------------------------------


def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(_FONT, 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 285 * mm, "HeavyHeader · 作品介绍文档")
    canvas.drawRightString(190 * mm, 285 * mm, "第四届开放原子大赛 · 2026 开源行业解决方案创新赛")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 283 * mm, 190 * mm, 283 * mm)
    canvas.line(20 * mm, 14 * mm, 190 * mm, 14 * mm)
    canvas.drawString(20 * mm, 10 * mm, "赛道一 · 开源基础软件与解决方案")
    canvas.drawRightString(190 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def _on_cover(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(2)
    canvas.line(20 * mm, 250 * mm, 190 * mm, 250 * mm)
    canvas.setFont(_FONT, 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(105 * mm, 14 * mm, "Apache-2.0 · 本作品以开源方式提交")
    canvas.restoreState()


def build(path: str) -> None:
    doc = BaseDocTemplate(
        path,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=22 * mm,
        bottomMargin=20 * mm,
        title="HeavyHeader 作品介绍文档",
        author="项目负责人",
        subject="第四届开放原子大赛·2026 开源行业解决方案创新赛 赛道一",
    )

    frame_cover = Frame(20 * mm, 20 * mm, 170 * mm, 225 * mm, id="cover")
    frame_body = Frame(20 * mm, 20 * mm, 170 * mm, 258 * mm, id="body")
    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[frame_cover], onPage=_on_cover),
            PageTemplate(id="body", frames=[frame_body], onPage=_on_page),
        ]
    )

    story: list = []

    # ======================================================================
    # 封面
    # ======================================================================
    story += [
        SP(52),
        P("HeavyHeader", "title"),
        P("C/C++ 构建效能治理平台", "subtitle"),
        SP(6),
        P("用编译器自己的输出，回答「这个头文件让我多等了几秒」", "subtitle"),
        SP(18),
        P("第四届开放原子大赛 · 2026 开源行业解决方案创新赛", "meta"),
        P("赛道一 · 开源基础软件与解决方案", "meta"),
        SP(34),
        P("项目负责人：（待填写）", "meta"),
        P("申报主体类型：开源社区（个人）", "meta"),
        P("日期：2026 年 10 月 17 日", "meta"),
        PageBreak(),
    ]

    # 切换到正文模板
    from reportlab.platypus import NextPageTemplate

    story.insert(len(story) - 1, NextPageTemplate("body"))

    # ======================================================================
    # 摘要
    # ======================================================================
    story += [
        P("摘要", "h1"),
        P(
            "大型 C/C++ 工程的构建时间是真实的成本：一次全量构建动辄数十分钟到数小时，"
            "CI 集群机时与工程师等待时间都花在里面。构建慢的最大单一来源往往不是算法，"
            "而是头文件失控——一个被数万个编译单元传递包含的重量级头文件，会让整个项目"
            "反复为它付费。"
        ),
        P(
            "现有工具能回答「这个 include 该不该删」，但<b>没有一个工具回答「它到底让我"
            "多等了几秒」</b>。HeavyHeader 的差异化正在于此：它不自己解析 C++，而是让真实"
            "编译器报告自己做了什么，再把编译器的真实计时数据与依赖图做交叉归因，输出按"
            "预期收益排序的精简清单，并且可以真的删掉、重新编译、实测验证。"
        ),
        P(
            "项目已实现四个命令：<font face='%s'>scan</font>（成本归因与报告）、"
            "<font face='%s'>verify</font>（实测验证）、<font face='%s'>gate</font>"
            "（CI 门禁）、<font face='%s'>compare</font>（前后对比），支持 GCC 9+、"
            "Clang 17+ 与 MSVC 三种工具链，附带 72 个单元测试与完整开源治理文件。"
            % (_MONO, _MONO, _MONO, _MONO)
        ),
        P(
            "在刻意构造的基准工程上，工具准确识别出一条未使用的 include 并实测节省 "
            "200 ms（38.5%）；在成熟开源项目 fmt 上，它对 6 个成本候选取前 3 个逐一实测，"
            "全部正确判定为「删了会编译失败」——<b>该报的报，不该报的不报</b>。"
        ),
        P(
            "关键词：构建效能；头文件成本归因；include 治理；开发者工具；CI 门禁；"
            "编译器诊断输出"
        ),
    ]

    # ======================================================================
    # 一、问题背景
    # ======================================================================
    story += [
        P("一、问题背景与需求依据", "h1"),
        P("1.1 构建慢的钱花在哪", "h2"),
        P(
            "一个 C++ 项目的预处理与解析成本，一阶近似正比于「编译器被迫读了多少头文件"
            "文本」。每个被解析的头文件，在每个引用它的编译单元里贡献一次自己的行数。"
            "这意味着一个 4700 行的头文件被 3000 个编译单元引用时，项目要为它解析 "
            "1410 万行——而这还只是它自身，不含它传递拉进来的一切。"
        ),
        P(
            "这个成本是隐形的。开发者看到的是「编译好慢」，看不到是哪几行 "
            "<font face='%s'>#include</font> 造成的。现有的分析工具要么只做结构展示，"
            "要么只做正确性判断，没有一个把成本和具体代码行对应起来。" % _MONO
        ),
        P("1.2 现有工具的空白", "h2"),
        P(
            "下表是本项目立项时对同类工具的逐项核读。对比的重点不是功能有无，"
            "而是「能否回答成本问题」——这是本项目的立足点。"
        ),
        TABLE(
            ["工具", "回答什么问题", "不能回答什么"],
            [
                [
                    "include-what-you-use",
                    "基于 AST 判断 include 是否必需",
                    "不给成本量化，无法排序优先级",
                ],
                [
                    "cpp-dependencies",
                    "分析模块间依赖关系",
                    "面向架构评审，不回答构建耗时",
                ],
                [
                    "include-gardener",
                    "可视化 include 关系图",
                    "只展示结构，不做归因",
                ],
                [
                    "IDE 的 Include Graph",
                    "查看单个文件的包含树",
                    "手动、单文件、无项目级视图",
                ],
                [
                    "HeavyHeader（本项目）",
                    "这个头文件让项目多解析了多少行、多等了几秒，删掉它能省多少",
                    "精确的语义级未使用判定（见第九章）",
                ],
            ],
            widths=[120, 150, 150],
        ),
        SP(4),
        P(
            "需要说明的是：上表对本项目一栏的描述是<b>设计目标</b>，其兑现程度由第四章的"
            "实测数据检验，不由声明检验。",
            "quote"
        ),
        P("1.3 目标场景", "h2"),
        P(
            "本项目锚定的场景是<b>汽车电子、工业软件、通信设备厂商的 C++ 单体仓库"
            "（monorepo）</b>。这些团队的共同特征：代码量在数十万到数百万行、编译单元数以千计、"
            "一次全量构建数小时、CI 集群机时是可观成本项。在这些团队里，"
            "「构建效能治理」是有明确投资回报的工程命题，而不是个人偏好。"
        ),
        P(
            "首版不限定使用者必须来自上述行业——任何 C/C++ 项目都能用。但项目的问题定义、"
            "指标设计和验收标准都以上述场景的规模为前提，因为只有那个规模下，"
            "本章描述的成本才显著到值得治理。"
        ),
    ]

    # ======================================================================
    # 二、核心方法论
    # ======================================================================
    story += [
        P("二、核心方法论：不自己解析 C++", "h1"),
        P(
            "整个项目只有一条设计原则，其他一切都是它的推论：<b>不自己解析 C++</b>。"
            "工具向真实编译器提问，读取它的回答。"
        ),
        P(
            "这不是懒，是正确性要求。一个手写预处理器在遇到模板元编程、条件编译、宏拼接时"
            "会静默给出错误答案，而这种错误比崩溃危险得多——崩溃会被发现，错误答案不会。"
            "让编译器自己报告它做了什么，分析结果就在构造上是精确的。"
        ),
        P("2.1 一次编译，三个问题", "h2"),
        TABLE(
            ["问题", "怎么问编译器"],
            [
                ["你包含了什么，嵌套多深？", "GCC/Clang <font face='%s'>-H</font>，MSVC <font face='%s'>/showIncludes</font>" % (_MONO, _MONO)],
                ["时间花在哪？", "GCC/Clang <font face='%s'>-ftime-report</font>，MSVC 墙钟计时" % _MONO],
                ["哪些头文件缺 include guard？", "<font face='%s'>-H</font> 输出的 trailer" % _MONO],
            ],
            widths=[200, 220],
        ),
        SP(4),
        P(
            "三个问题在同一次编译里全部得到回答——<font face='%s'>-H</font> 和 "
            "<font face='%s'>-ftime-report</font> 都写 stderr。分成两次调用会让扫描耗时翻倍，"
            "更糟的是两次之间头文件若发生变化，两边数据会互相矛盾。" % (_MONO, _MONO)
        ),
        P("2.2 归因行数：成本的度量", "h2"),
        CODE(
            "attributed_lines(h) = line_count(h) × tu_reach(h)\n"
            "\n"
            "tu_reach(h) = 至少间接包含过 h 的编译单元数量"
        ),
        P(
            "这个乘积衡量「整个项目为 <font face='%s'>h</font> 付出了多少行解析量」。"
            "它是对同一份代码确定的，不随机器负载波动——这一点很重要，因为它是 CI 门禁"
            "唯一可依据的指标。" % _MONO
        ),
        P("2.3 独占闭包：收益的计算", "h2"),
        P(
            "删掉一条 include 后，唯一会停止被解析的头文件集合叫它的<b>独占闭包</b>："
        ),
        CODE(
            "exclusive(d) = closure(d) − ( ⋃ closure(d′) )\n"
            "               d′ ∈ direct_includes(t),  d′ ≠ d"
        ),
        P(
            "<b>这是本项目与普通 lint 工具的本质区别。</b>一个头文件如果能从别的 include "
            "到达，那么删掉当前这条 include 并不会让它停止被解析——钱已经花了。"
            "只有独占闭包里的头文件才是删掉这条 include 的真实收益。不做这个计算的工具，"
            "会把大量零收益的建议排在真收益前面。"
        ),
        P("2.4 重复解析检测：确定性的必赚项", "h2"),
        P(
            "<font face='%s'>-H</font> 只为<b>实际处理过</b>的包含打印一行。带 "
            "<font face='%s'>#pragma once</font> 或 guard 宏的头文件，第二次及以后的包含会被"
            "跳过，因此无论多少个头文件包含它，<font face='%s'>-H</font> 里只出现一次。"
            "于是「同一编译单元内出现多次」在物理上只可能是缺少 guard。"
            % (_MONO, _MONO, _MONO)
        ),
        P(
            "修复（补 guard）<b>永远安全</b>，且收益立竿见影。这是报告里唯一标为"
            "「必赚项」的分类——不需要实测，因为逻辑上不可能出错。"
        ),
    ]

    # ======================================================================
    # 三、完整任务示例
    # ======================================================================
    story += [
        P("三、完整任务示例（可手算复核）", "h1"),
        P(
            "本章用一个 45 行的构造工程走完「扫描 → 定位 → 验证」全流程。"
            "所有数字都可手工复核，工程在仓库 <font face='%s'>benchmarks/cases/demo</font>。"
            % _MONO
        ),
        P("3.1 工程构造", "h2"),
        CODE(
            "benchmarks/cases/demo/\n"
            "├── include/\n"
            "│   ├── core.h          # 被两个 TU 引用（成本热点）\n"
            "│   ├── heavy.h         # 传递闭包很大\n"
            "│   ├── bloated.h       # 只被 lonely.h 到达的重量级闭包\n"
            "│   ├── lonely.h        # 被 main.cpp 包含但从未使用\n"
            "│   ├── unguarded.h     # 刻意不加 guard\n"
            "│   └── mid.h\n"
            "└── app/\n"
            "    ├── main.cpp        # 包含 lonely.h，但代码里不用它\n"
            "    └── util.cpp"
        ),
        P("3.2 扫描结果", "h2"),
        CODE(
            "$ heavyheader scan benchmarks/cases/demo \\\n"
            "    --compiler g++ --std c++17 -I benchmarks/cases/demo/include\n"
            "\n"
            "2 TU · 425 条依赖边 · 206,106 行头文件解析量 · 5 个成本候选（其中 1 个疑似未使用）"
        ),
        P(
            "成本热点表把 <font face='%s'>core.h</font> 排在首位（被两个编译单元引用），"
            "而 <font face='%s'>lonely.h</font> 被标记为「疑似未使用」并因独占闭包最大"
            "（22,312 行）排在候选首位。%s"
            % (_MONO, _MONO, "缺 guard 的 <font face='%s'>unguarded.h</font> 出现在"
               "「必赚项」表中：解析 3 次、涉及 2 个 TU。" % _MONO)
        ),
        P("3.3 实测验证", "h2"),
        P(
            "成本模型再严谨也仍是模型。<font face='%s'>verify</font> 命令注释掉候选 include、"
            "重新编译、计时对比，每侧重复 3 次取最小值（噪声只会让测量变慢，不会变快，"
            "所以最小值是最接近真实成本的估计）：" % _MONO
        ),
        CODE(
            "$ heavyheader verify benchmarks/cases/demo --top 3 --runs 3\n"
            "\n"
            "## lonely.h ← main.cpp\n"
            "   状态：improved — 基线 520 ms → 移除后 320 ms\n"
            "   实测节省：200 ms (38.5%)\n"
            "\n"
            "## core.h ← util.cpp\n"
            "   状态：compile-error — 移除后编译失败：\n"
            "   util.cpp:3:14: error: 'Core' was not declared in this scope\n"
            "\n"
            "## core.h ← main.cpp\n"
            "   状态：compile-error — 移除后编译失败：\n"
            "   main.cpp:6:14: error: 'Core' was not declared in this scope\n"
            "\n"
            "小结：1/3 个候选实测有效。"
        ),
        P(
            "<b>注意最后那个「1/3」。</b>工具没有把三个候选都说成可删——它把两个正在使用的"
            "头文件正确判为「删了会编译失败」，只对真正未使用的那个给出收益数字。"
            "这个区分是第四章在成熟项目上验证的重点。"
        ),
    ]

    # ======================================================================
    # 四、真实项目验证
    # ======================================================================
    story += [
        P("四、真实项目验证", "h1"),
        P(
            "第三章的数字来自刻意构造的工程，只能证明工具能跑。本章用真实开源项目检验它"
            "是否可信——<b>尤其是它会不会误报</b>。"
        ),
        P("4.1 在 fmt 上的扫描", "h2"),
        P(
            "对 <font face='%s'>fmtlib/fmt</font>（<font face='%s'>--depth 1</font> 克隆）"
            "扫描，排除测试目录（其依赖 gtest/gmock，未安装）：" % (_MONO, _MONO)
        ),
        TABLE(
            ["指标", "值"],
            [
                ["编译单元", "3 成功 / 1 失败"],
                ["依赖边", "651"],
                ["头文件解析量", "331,016 行"],
                ["成本候选", "6 个，其中 <b>0 个</b>疑似未使用"],
            ],
            widths=[140, 280],
        ),
        SP(4),
        P(
            "唯一失败的 <font face='%s'>src/fmt.cc</font> 已确诊：该文件是 C++20 module"
            "（第 8 行 <font face='%s'>module;</font>），MinGW GCC 16.2 不支持 "
            "<font face='%s'>-fmodules</font>，换 <font face='%s'>--std c++20</font> 同样"
            "报错。<b>这是工具链限制，不是工具缺陷</b>，且失败被如实报告而非静默跳过——"
            "报告顶部会写明「1 个编译单元失败，数字可能偏低」。"
            % (_MONO, _MONO, _MONO, _MONO)
        ),
        P("4.2 关键结果是那个 0", "h2"),
        P(
            "fmt 是维护良好的库，它的 include 确实是干净的。工具给出 6 个成本候选，"
            "对前 3 个逐一实测："
        ),
        CODE(
            "## windows.h ← os.cc\n"
            "   状态：compile-error — os.cc:90:9: error:\n"
            "   'FORMAT_MESSAGE_ALLOCATE_BUFFER' was not declared in this scope\n"
            "\n"
            "## format-inl.h ← format.cc\n"
            "   状态：compile-error — format.cc:10:1: error:\n"
            "   'FMT_BEGIN_NAMESPACE' does not name a type\n"
            "\n"
            "## os.h ← os.cc\n"
            "   状态：compile-error — os.cc:102:12: error:\n"
            "   'fmt' does not name a type"
        ),
        P(
            "<b>三个全部正确判定为「删了会编译失败」。</b>一个只会喊「你可以删掉这个」的工具，"
            "第一次误报就会被人关掉。HeavyHeader 在成熟项目上报告「没有可删的」，"
            "同时仍然量化出成本集中在哪——这个对比本身就是价值。"
        ),
        P(
            "与之对照，第三章构造的未使用 include 被准确识别并实测省 38.5%。"
            "<b>该报的报，不该报的不报，两边都有数据。</b>"
        ),
        P("4.3 这些数字的边界", "h2"),
        P(
            "如实说明：本章数字取自 GCC 16.2.0 (MinGW-w64) + Python 3.13，随编译器版本、"
            "平台、机器负载变化，<b>不应逐位复现</b>。可复现的是结论方向：候选数是否为 0、"
            "verify 是否报 compile-error。完整复现步骤见仓库 "
            "<font face='%s'>benchmarks/results/README.md</font>。" % _MONO
        ),
    ]

    # ======================================================================
    # 五、平台能力
    # ======================================================================
    story += [
        P("五、平台能力：从工具到流水线的一环", "h1"),
        P(
            "如果只能跑一次、出一份报告，它是个玩具。HeavyHeader 的四个命令覆盖"
            "「发现 → 验证 → 门禁 → 对比」的完整闭环："
        ),
        TABLE(
            ["命令", "作用", "在流水线中的位置"],
            [
                ["<font face='%s'>scan</font>" % _MONO, "成本归因，产出 Markdown / HTML / JSON 报告，可存基线", "本地分析、CI 报告"],
                ["<font face='%s'>verify</font>" % _MONO, "注释掉候选 include、重编译、实测耗时差异", "采纳建议前的确认"],
                ["<font face='%s'>gate</font>" % _MONO, "成本相对基线回归超阈值则非零退出", "PR 门禁"],
                ["<font face='%s'>compare</font>" % _MONO, "对比两份扫描，定位成本上升的具体头文件", "版本间对比"],
            ],
            widths=[80, 200, 140],
        ),
        SP(4),
        P("5.1 CI 门禁为什么可信", "h2"),
        P(
            "门禁只对比确定性的归因行数和相对比例，<b>不对比墙钟时间</b>——后者随负载波动，"
            "拿它做判定会产生误触发。而一个误触发的门禁会被关掉，关掉的门禁保护不了任何东西。"
            "默认阈值 5% 是「明显变化」与「正常波动」之间的实用分界。"
        ),
        P(
            "失败的构建会指名道姓："
        ),
        CODE(
            "门禁失败：\n"
            "  - header cost regressed +17.1% (206100 -> 241300 attributed lines),\n"
            "    budget is +5.0%\n"
            "    worst offender: include/bloated.h 0 -> 22312 lines (+100.0%)"
        ),
        P(
            "一个只说「成本回归 +12%」的门禁毫无用处；能定位到具体文件和具体 include 行的"
            "门禁才会被留下来。"
        ),
        P("5.2 报告是自包含单文件", "h2"),
        P(
            "HTML 报告不引 CDN、不引框架、图表是手写内联 SVG。这不是技术偏好，是需求："
            "报告要贴进 PR、要发给别人、要在没网的机器上打开。一个在这些场景下显示白页的"
            "报告毫无价值。数据量级（几十个头文件、几十个历史点）也远不够撑起一个图表库的开销。"
        ),
    ]

    # ======================================================================
    # 六、技术路线
    # ======================================================================
    story += [
        P("六、技术路线与架构", "h1"),
        P("6.1 分层", "h2"),
        CODE(
            "src/heavyheader/\n"
            "├── drivers/        向编译器提问：GCC/Clang/MSVC 各自的方言\n"
            "├── parse/           解析 -H 依赖树、-ftime-report 计时、make 依赖文件\n"
            "├── attrib/          纯函数：依赖图、独占闭包、重复解析、使用启发式\n"
            "├── verify.py        实测：注释掉 include → 重编译 → 计时\n"
            "├── scanner.py       编排：发现 TU → 线程池并行编译 → 汇总\n"
            "├── baseline.py      基线、对比、CI 门禁\n"
            "└── report/          Markdown / HTML / JSON 渲染"
        ),
        P(
            "<b>关键性质：<font face='%s'>attrib/</font> 和 <font face='%s'>parse/</font> "
            "是纯函数，不碰编译器、不碰文件系统、不碰网络。</b>核心逻辑可以被彻底单元测试——"
            "归因引擎的全部测试用例都不需要安装任何编译器。"
            % (_MONO, _MONO)
        ),
        P("6.2 为什么核心用 Python 而不是 C++", "h2"),
        P(
            "项目的使用者是 C++ 开发者，但工具本身用 Python 写。理由是工作性质："
            "它的主体是调用子进程、解析文本、建图、排序、渲染报告——这是 I/O 密集型而非"
            "计算密集型，Python 的开发速度优势直接转化为交付速度，且<b>零第三方运行时依赖</b>"
            "意味着它能在最朴素的 CI 容器里跑起来。编译慢的 C++ 项目不该为了分析工具"
            "再拖一套运行时进去。"
        ),
        P(
            "需要声明的是：这个选择有代价。Python 的解释器启动开销在每次调用编译器时都会"
            "被放大，而 libclang 绑定在 Python 生态里更成熟。Roadmap 中的「精确模式」"
            "会利用后一点。"
        ),
        P("6.3 支持的工具链", "h2"),
        TABLE(
            ["编译器", "依赖树", "分阶段计时"],
            [
                ["GCC 9+", "<font face='%s'>-H</font>" % _MONO, "<font face='%s'>-ftime-report</font>" % _MONO],
                ["Clang 17+", "<font face='%s'>-H</font>" % _MONO, "<font face='%s'>-ftime-report</font>" % _MONO],
                ["MSVC", "<font face='%s'>/showIncludes</font>" % _MONO, "墙钟计时（无等价文本格式）"],
            ],
            widths=[90, 140, 190],
        ),
        SP(4),
        P(
            "有 <font face='%s'>compile_commands.json</font> 时严格按其中的 flags 编译——"
            "同一份代码在不同 flags 下的数字没有可比性。" % _MONO
        ),
    ]

    # ======================================================================
    # 七、诚实的边界
    # ======================================================================
    story += [
        P("七、诚实的边界：不做什么", "h1"),
        P(
            "这一章和前面几章同样重要。一个工具如果把局限藏起来，使用者就无法判断"
            "什么时候该信它、什么时候不该。"
        ),
        TABLE(
            ["不做什么", "为什么", "怎么办"],
            [
                [
                    "不判定 include 是否「未使用」的语义真相",
                    "文本启发式只能回答「源码里有没有出现该头文件声明的标识符」",
                    "权威结论由 verify 给出：真的删掉、重新编译",
                ],
                [
                    "不做精确的 AST 级分析",
                    "要精确判定需要 libclang，会把使用门槛抬到需装整套 LLVM 工具链",
                    "Roadmap v0.3 作为可选 extra，默认路径保持零依赖",
                ],
                [
                    "不改写代码",
                    "自动重构一个它不完全理解的代码库，风险远大于收益",
                    "工具给建议，人做决定；verify 只在临时目录做实验",
                ],
                [
                    "不跨机器比较绝对耗时",
                    "墙钟时间随负载波动",
                    "门禁只对比确定性的归因行数与相对比例",
                ],
                [
                    "不信任编译器的自报时序",
                    "实测发现 GCC on MinGW 会把某阶段报成 584 年（见 7.1）",
                    "用进程墙钟做上界校验，不可能的的行丢弃并警告",
                ],
            ],
            widths=[130, 160, 130],
        ),
        SP(4),
        P("7.1 一个真实的编译器缺陷", "h2"),
        P(
            "开发过程中在 fmt 上发现 <font face='%s'>baseline_seconds</font> 报出约 1170 年。"
            "根因不在本项目：GCC 16.2.0 (MinGW-w64) 的 <font face='%s'>-ftime-report</font> "
            "在编译包含 <font face='%s'>&lt;windows.h&gt;</font> 的文件时，会把 "
            "<font face='%s'>phase parsing</font> 报成 <font face='%s'>18446744070.35</font> "
            "秒——即 2^64 纳秒折成秒，约 584 年。" % (_MONO, _MONO, _MONO, _MONO, _MONO)
        ),
        P(
            "解析器忠实解析了垃圾，于是报告里出现一个看起来权威实际荒谬的数字。"
            "修法：driver 本来就用 <font face='%s'>perf_counter</font> 测量了进程墙钟时间，"
            "而一个编译阶段不可能比包含它的进程更长。以此为上界校验，超出的行丢弃，"
            "<b>并 surfaced 成警告而非静默修正</b>——用户有权知道他们的工具链有缺陷。"
            % _MONO
        ),
        P(
            "这件事本身说明了「不自己解析 C++」这条原则的代价：你把编译器当权威，"
            "就得防着它说谎。所以工具对编译器的每个输出都保留交叉校验的能力。"
        ),
        P("7.2 已知局限汇总", "h2"),
        P(
            "① 行数不等于解析难度，重型模板头的权重被低估；② 默认 "
            "<font face='%s'>-fsyntax-only</font>，不含代码生成阶段成本；③ 使用状态是"
            "文本启发式，为副作用而包含的头文件（如 <font face='%s'>&lt;iostream&gt;</font> "
            "的流初始化）会被误判；④ 增量编译（ccache、distcc）会改变耗时分布，跑 verify "
            "前需清缓存。完整列表见仓库 <font face='%s'>docs/methodology.md</font> 第 9 节。"
            % (_MONO, _MONO, _MONO)
        ),
    ]

    # ======================================================================
    # 八、开源治理
    # ======================================================================
    story += [
        P("八、开源治理与社区", "h1"),
        P(
            "项目从第一行代码起就按可协作的标准组织，而不是跑出demo再补文件。"
            "大赛评分中「开源治理」占 20%，但我们做这些的理由更直接："
            "一个没有 CONTRIBUTING 的项目收不到贡献，没有行为准则的社区留不住人。"
        ),
        TABLE(
            ["文件", "作用"],
            [
                ["README.md", "问题定义、与同类工具对比、快速开始、诚实的边界"],
                ["LICENSE (Apache-2.0)", "对贡献者和使用者都明确，含专利授权条款"],
                ["CONTRIBUTING.md", "按优先级列出「什么贡献最被需要」，避免力气花错方向"],
                ["CODE_OF_CONDUCT.md", "基于 Contributor Covenant 1.4"],
                ["SECURITY.md", "威胁模型：编译器输出是不可信输入、临时文件、不联网"],
                ["ROADMAP.md", "v0.1–v0.4 路线图 + 「明确不做的事」清单"],
                ["CHANGELOG.md", "语义化版本，记录每个修复的根因"],
                ["docs/methodology.md", "每个指标的定义、局限、以及「查不到就不该出现在报告里」"],
                ["docs/architecture.md", "分层设计与刻意选择的理由"],
                [".github/", "Issue 模板（bug/feature）、PR 模板、CI 工作流"],
            ],
            widths=[130, 290],
        ),
        SP(4),
        P("8.1 CI 与自举", "h2"),
        P(
            "CI 覆盖 Linux / Windows / macOS 三个操作系统 × Python 3.10–3.13 四个版本，"
            "外加一个 <b>self-scan</b> 任务：在 CI 里构造一个真实 C++ 工程并用 HeavyHeader "
            "扫描它，断言归因行数非零、编译单元被发现。这一步同时也是集成测试——"
            "它验证工具在非 Windows、非 MinGW 的工具链下也能正确解析编译器输出。"
        ),
        P("8.2 为什么选 Apache-2.0", "h2"),
        P(
            "三个理由：对贡献者和使用者都明确；含专利授权条款，这对一个要进企业 CI 的"
            "工具不是形式问题；与生态中大量 C++ 项目的许可证兼容。"
        ),
        P("8.3 测试策略", "h2"),
        P(
            "72 个单元测试。样本优先用真实编译器输出逐字复制——例如 "
            "<font face='%s'>tests/test_parsers.py</font> 里的 <font face='%s'>-H</font> 和 "
            "<font face='%s'>-ftime-report</font> 输出是从 GCC 16.2 真实运行复制的。"
            "这意味着换编译器版本时这些测试会最先报警。"
            % (_MONO, _MONO, _MONO)
        ),
    ]

    # ======================================================================
    # 九、开发过程与后续
    # ======================================================================
    story += [
        P("九、开发过程与后续计划", "h1"),
        P("9.1 已修的真实缺陷", "h2"),
        P(
            "开发过程中修掉 5 个 bug，每个都有回归测试。列出它们是因为它们解释了这个项目"
            "为什么是现在这样："
        ),
        TABLE(
            ["缺陷", "症状", "为什么难发现"],
            [
                [
                    "独占闭包恒为空",
                    "所有项目都报「无可优化项」",
                    "症状与「确实没问题」在输出上完全无法区分",
                ],
                [
                    "make 规则按第一个裸冒号切分",
                    "Windows 盘符冒号被误判为分隔符，目标被截断",
                    "只在 Windows 路径上出现，Linux 开发不会遇到",
                ],
                [
                    "MSVC 缩进深度直接除步长",
                    "所有嵌套 include 都报成深度 0",
                    "MSVC 最浅层本身有空格，不是从 0 开始",
                ],
                [
                    "usage_label 漏序列化",
                    "基线文件丢失使用状态信息",
                    "模型加了字段但序列化器没写，两边不同步",
                ],
                [
                    "GCC 坏计时器（见 7.1）",
                    "构建成本报出 1170 年",
                    "编译器自己的输出看起来是权威的",
                ],
            ],
            widths=[120, 150, 150],
        ),
        SP(4),
        P("9.2 Roadmap", "h2"),
        TABLE(
            ["版本", "内容", "状态"],
            [
                ["v0.1", "可用的核心：三种工具链解析、成本归因、四个命令、72 测试", "已发布"],
                ["v0.2", "从工具到平台：历史存储与趋势服务、增量扫描、PR 评论机器人、多编译器对比", "计划中"],
                ["v0.3", "精确模式：libclang AST 后端（可选依赖）、前向声明建议、PCH 收益评估", "计划中"],
                ["v0.4", "生态集成：CMake、Bazel/Meson、VSCode 插件、与 IWYU 输出互转", "计划中"],
            ],
            widths=[60, 280, 80],
        ),
        SP(4),
        P(
            "Roadmap 里还有一份「明确不做的事」清单：不做通用静态分析器、不自动修改代码、"
            "不做云服务、不追求支持所有编译器。这份清单和功能列表同样重要——它防止项目"
            "范围蔓延。"
        ),
        P("9.3 关于团队", "h2"),
        P(
            "本项目由个人开发者以「开源社区」主体类型申报，独立完成需求、设计、实现、"
            "测试与文档。这是一个明确的事实，不是需要修饰的短板：项目从零到可运行的全部"
            "工作都在公开仓库的 git 历史里，每个 commit 都说明了「为什么」而不只是「做了什么」。"
        ),
        P(
            "后续开发按 Roadmap 推进，欢迎社区参与。<font face='%s'>CONTRIBUTING.md</font> "
            "开头按优先级列出了「什么贡献最被需要」——排第一的是真实项目的扫描结果反馈，"
            "因为那是唯一能检验工具在真实代码库上是否给出荒谬结论的方式。"
            % _MONO
        ),
    ]

    # ======================================================================
    # 十、参考资料
    # ======================================================================
    story += [
        P("十、参考资料", "h1"),
        P("10.1 实际核读过的同类项目", "h2"),
        P(
            "以下项目在立项时逐项核读，用于第一章的对比表。核读日期为 2026 年 10 月。"
        ),
        CODE(
            "R1  include-what-you-use — https://include-what-you-use.org/\n"
            "R2  cpp-dependencies — https://github.com/tomtom-international/cpp-dependencies\n"
            "R3  include-gardener — https://github.com/fedorbondarczuk/include-gardener\n"
            "R4  fmtlib/fmt — https://github.com/fmtlib/fmt （第四章的基准对象）"
        ),
        P("10.2 实际使用的编译器文档", "h2"),
        CODE(
            "R5  GCC 手册：-H、-ftime-report、-M/-MM 选项说明\n"
            "R6  MSVC 文档：/showIncludes 编译器选项\n"
            "R7  C++ 标准：include guard 与 #pragma once 的语义"
        ),
        P("10.3 未引用但想说明的事", "h2"),
        P(
            "本项目<b>没有</b>引用政策文件或学术论文来支撑其必要性。这不是疏漏，是刻意的："
            "我们没有找到一份能直接支撑「头文件成本归因」这个具体问题的权威文献，"
            "而为了显得严谨去引一份关系不大的政策条文，比不引更糟。"
        ),
        P(
            "项目的必要性论证来自三件可验证的事：第一章的成本模型（可手算）、"
            "第三章的构造工程（可复现）、第四章的真实项目数据（可复现）。"
            "如果这三件事不成立，引多少文献也救不了它。"
        ),
        SP(10),
        P(
            "本作品以 Apache-2.0 许可证开源提交。代码仓库、测试、治理文件与基准数据"
            "均已随作品材料提交。", "quote"
        ),
    ]

    doc.build(story)
    print(f"written: {path}")


if __name__ == "__main__":
    import sys

    build(sys.argv[1] if len(sys.argv) > 1 else "HeavyHeader-作品介绍文档.pdf")
