"""生成 HeavyHeader 演示视频（MP4，1920×1080，中文配音）。

用真实运行过的终端输出渲染，不是编造的演示。每一行命令和输出都来自
仓库归档的可复现运行（benchmarks/results/、experiments/results/、
docs/demo-walkthrough.md），观众可以自己跑一遍核对。

2026-10-29 重制版相对 10-22 版的改动：
- 画布 1080×810 → 1920×1080；底部不再空白；
- 布局改为「顶栏环节名 + 左侧终端窗口 + 右侧要点栏 + 底部进度条」，
  要点栏给大号关键数字——不盯终端的观众也能跟上；
- 删掉 10-22 版里两大段完全重复的内容（模板实例化/PCH/前向声明/四项目表）；
- 修正与归档不符的数字：verify 段按 docs/demo-walkthrough.md 的实测口径
  （720→612 ms，−15.1%，证据档「已验证」），不再是 520→320 (38.5%)；
- 加入 edge-tts 中文配音（zh-CN-YunxiNeural），每段旁白一个 mp3，
  段长取 max(打字动画, 旁白时长 + 0.5 s)，最后 ffmpeg apad + shortest 合流。

设计约束：
- 帧用 PIL 绘制，ffmpeg 编码（imageio_ffmpeg 自带，无独立 ffmpeg 依赖）；
- 视频里出现的每个数字都与归档一致——发现对不上就改视频，不是改归档。
"""

from __future__ import annotations

import asyncio
import math
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# canvas & palette
# --------------------------------------------------------------------------

W, H = 1920, 1080
FPS = 15

BG = (16, 20, 26)           # 终端区背景
PAGE_BG = (24, 28, 36)      # 页面背景（终端窗口外）
FG = (208, 214, 224)
DIM = (124, 134, 150)
GREEN = (92, 203, 138)
RED = (238, 116, 104)
YELLOW = (228, 180, 80)
BLUE = (110, 168, 254)
CYAN = (98, 204, 216)
WHITE = (236, 240, 246)
TITLE_BG = (26, 32, 42)
BAR_DIM = (52, 60, 74)

FONT_SIZE = 19              # 终端正文字号
LINE_H = 27
PAD = 24

FONT = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", FONT_SIZE)
FONT_BOLD = ImageFont.truetype("C:/Windows/Fonts/CascadiaCode.ttf", FONT_SIZE)
FONT_CJK = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", FONT_SIZE, index=0)
FONT_CJK_BOLD = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", FONT_SIZE, index=0)
#: 右侧要点栏
FONT_BULLET = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 30)
FONT_BULLET_BIG = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 44)
FONT_BULLET_DIM = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 24)
FONT_HEAD = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 30)
FONT_TITLE = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 72)
FONT_CARD = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
FONT_CARD_DIM = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 24)
FONT_MONO_CARD = ImageFont.truetype("C:/Windows/Fonts/CascadiaCode.ttf", 26)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x2E80 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF
        or 0x3000 <= code <= 0x303F
    )


def draw_mixed(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    fill,
    bold: bool = False,
) -> None:
    """ASCII 用 Cascadia、CJK 用微软雅黑逐字绘制，推进量按真实字宽。"""
    x, y = xy
    ascii_font = FONT_BOLD if bold else FONT
    cjk_font = FONT_CJK_BOLD if bold else FONT_CJK
    for char in text:
        font = cjk_font if _is_cjk(char) else ascii_font
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font)


# --------------------------------------------------------------------------
# segments —— 每段 = 环节名 + 右侧要点 + 旁白 + 终端行
# --------------------------------------------------------------------------


@dataclass
class Segment:
    name: str                                   # 顶栏环节名
    bullets: list[tuple[str, str, bool]]        # (文字, 颜色, 是否大字)
    voice: str                                  # 旁白（edge-tts）
    lines: list[tuple[str, tuple, bool, float]] = field(default_factory=list)


def L(text: str, color=FG, bold: bool = False, hold: float = 0.0):
    return (text, color, bold, hold)


def comment(text: str, hold: float = 0.3):
    return L(f"# {text}", DIM, False, hold)


def blank(hold: float = 0.2):
    return L("", FG, False, hold)


TERMINAL_TITLE = "heavyheader — 编译器视角的头文件成本"

# 所有数字的出处：
#   scan/verify/link-trap/gate 输出 = docs/demo-walkthrough.md（g++ 实测归档）
#   四项目表 = benchmarks/results/summary.md（审计修正后口径）
#   zstd −46.6% / json −7.1% / 断言数 / 竞品对照 = docs/benchmark-report.md
#   15 命令 / 794 测试 = 2026-10-28 实测（heavyheader --help / pytest）


def seg_intro() -> Segment:
    return Segment(
        name="",
        bullets=[],
        voice=(
            "HeavyHeader，C/C++ 构建效能治理平台。"
            "用编译器自己的输出，回答一个老问题：这个头文件，让我多等了几秒？"
            "删掉它，是真能省，还是白改？"
        ),
    )


def seg_problem() -> Segment:
    return Segment(
        name="① 问题",
        bullets=[
            ("4700 行的头 × 3000 个编译单元", WHITE, False),
            ("= 1410 万行解析量", YELLOW, True),
            ("已有工具能回答「哪个头贵」", DIM, False),
            ("没人回答「删了能省多少」", WHITE, False),
        ],
        voice=(
            "一个四千七百行的头文件，被三千个编译单元引用，"
            "项目要为它解析一千四百一十万行——这还只是它自己。"
            "构建慢的病根在头文件。但已有的工具只能告诉你哪个头贵，"
            "没人敢回答后半个问题：删掉这一行，到底是真省，还是白改。"
        ),
        lines=[
            comment("构建慢，但看不到是哪几行 #include 造成的", 0.5),
            blank(0.2),
            L("一个 4700 行的头文件被 3000 个编译单元引用时，", DIM, hold=0.35),
            L("项目要为它解析 1410 万行——这还只是它自身。", DIM, hold=0.9),
            blank(0.3),
            L("$ ls benchmarks/cases/demo", BLUE, hold=0.4),
            L("include/  app/", FG, hold=0.3),
            L("  core.h     成本热点，被两个 TU 引用", DIM, hold=0.25),
            L("  bloated.h  只经 lonely.h 到达的重量级闭包", DIM, hold=0.25),
            L("  lonely.h   被 main.cpp 包含但从未使用", YELLOW, hold=0.25),
            L("  unguarded.h 刻意不加 guard", DIM, hold=0.9),
        ],
    )


def seg_scan() -> Segment:
    return Segment(
        name="② 成本归因 scan",
        bullets=[
            ("2 TU · 425 条依赖边", WHITE, False),
            ("206,106 行头文件解析量", YELLOW, True),
            ("lonely.h：独占闭包 36 · 预计省 22,316 行（15.9%）", WHITE, False),
            ("使用状态：正在使用", RED, True),
            ("← 判据分不清「被使用」和「顺带被解析」", DIM, False),
        ],
        voice=(
            "扫描不自己解析 C++，让真实编译器报告它做了什么。"
            "这个演示工程只有两个编译单元，却被迫解析二十万行头文件。"
            "成本最高的这一条，独占闭包三十六行，预计省两万两千行。"
            "但是注意——使用状态写着：正在使用。"
            "文本判据分不清「被使用」和「顺带被解析」。"
        ),
        lines=[
            L("$ heavyheader scan benchmarks/cases/demo \\", BLUE, hold=0.3),
            L("    --compiler g++ --std c++17 -I …/include", BLUE, hold=0.8),
            blank(0.2),
            L("2 TU · 425 条依赖边 · 206,106 行头文件解析量 · 5 个成本候选", WHITE, True, hold=1.0),
            blank(0.3),
            L("## 成本热点 Top", WHITE, True, hold=0.4),
            L("| 头文件 | 行数 | 被引用 TU | 归因行数 |", DIM, hold=0.3),
            L("| include\\core.h    |   5 | 2 |      10 |", FG, hold=0.25),
            L("| include\\bloated.h |   8 | 1 |       8 |", FG, hold=0.25),
            L("| include\\lonely.h  |   3 | 1 |       3 |", YELLOW, hold=0.7),
            blank(0.3),
            L("## 可精简的 include（按预计收益排序）", WHITE, True, hold=0.4),
            L("| 编译单元    | include          | 独占闭包 | 预计省行数 | 使用状态 |", DIM, hold=0.3),
            L("| app\\main.cpp | include\\lonely.h |      36 |  22,316 | 正在使用 |", YELLOW, True, hold=1.0),
            L("| app\\util.cpp | include\\core.h   |     115 |  66,014 | 正在使用 |", FG, hold=0.9),
            blank(0.4),
            comment("「疑似未使用」只是文本启发式，不是安全保证。", 0.5),
            comment("权威结论来自 verify：真的删掉、重新编译、实测。", 0.9),
        ],
    )


def seg_verify() -> Segment:
    return Segment(
        name="③ 反事实验证 verify",
        bullets=[
            ("5 个候选：4 编译失败 · 1 improved", WHITE, False),
            ("lonely.h ← main.cpp", WHITE, False),
            ("720 ms → 612 ms（−15.1%）", GREEN, True),
            ("证据档「已验证」：汇编一致 · 复测成立", GREEN, False),
            ("判据说「在用」，编译器说：可删", YELLOW, True),
        ],
        voice=(
            "所以第二步，别猜——真的删掉，重新编译。"
            "两侧各编三遍取最小值，再复测一遍。"
            "五个候选里四个编译失败，逐个报出了原因。"
            "而这一条，判据刚才说它「在用」——实测，"
            "七百二十毫秒降到六百一十二毫秒，省百分之十五点一，"
            "汇编指纹一致，复测仍然成立。"
            "判据说「在用」的头，编译器说：可以删。"
        ),
        lines=[
            L("$ heavyheader verify benchmarks/cases/demo \\", BLUE, hold=0.3),
            L("    --compiler g++ --ungated-sites --top 5 --runs 3 --confirm 2", BLUE, hold=0.9),
            blank(0.3),
            L("## core.h ← util.cpp", WHITE, True, hold=0.4),
            L("   状态：compile-error — 移除后编译失败：", RED, hold=0.4),
            L("   error: 'Core' was not declared in this scope", RED, hold=0.7),
            L("## core.h ← main.cpp", WHITE, True, hold=0.4),
            L("   状态：compile-error — 'Core' was not declared in this scope", RED, hold=0.9),
            L("   （mid.h、unguarded.h 两条同样编译失败，共 4 条）", DIM, hold=0.9),
            blank(0.3),
            L("## lonely.h ← main.cpp", WHITE, True, hold=0.5),
            L("   状态：improved — 基线 720 ms → 移除后 612 ms", GREEN, True, hold=0.7),
            L("   实测节省：108 ms（−15.1%）", GREEN, True, hold=1.0),
            L("   证据档：已验证 — 汇编指纹一致 · 实测省时过噪声线 · 复测成立", GREEN, hold=1.3),
            blank(0.3),
            comment("判据说「在用」的头，编译器说可以删——", 0.5),
            comment("判据说不清的地方，编译器说得清。", 1.0),
        ],
    )


def seg_linktrap() -> Segment:
    return Segment(
        name="④ 汇编闸门 codegen",
        bullets=[
            ("编译通过 ≠ 程序没变", YELLOW, True),
            ("-fsyntax-only 对它完全沉默", WHITE, False),
            ("汇编指纹比对：当场拦下", WHITE, False),
            ("真实案例：−77.8% 的「提升」", RED, False),
            ("实为删掉公共 API 实现", RED, True),
        ],
        voice=(
            "更关键的能力，是拦下看起来无害的破坏。"
            "这个十秒钟造出的最小复现：删掉头文件之后，编译照样通过，"
            "连语法检查都完全沉默——但一个函数定义悄悄没了，链接期才会炸。"
            "只看编译结果的工具会放行；我们比对汇编指纹，当场拦下。"
            "真实工程里，一次负百分之七十七点八的「大幅提升」，"
            "就是这样被抓住的——删掉的其实是公共 API 的实现。"
        ),
        lines=[
            L("$ printf '#pragma once\\nint helper();\\n' > decl.h", BLUE, hold=0.5),
            L("$ printf '#include \"decl.h\"\\nint helper(){return 42;}\\n' > defs.h", BLUE, hold=0.5),
            L("$ printf '#include \"decl.h\"\\n#include \"defs.h\"\\n"
              "int main(){return helper()-42;}\\n' > main.cpp", BLUE, hold=0.7),
            blank(0.3),
            L("$ heavyheader verify /tmp/linktrap --compiler g++ --check-codegen", BLUE, hold=0.9),
            blank(0.3),
            L("## defs.h ← main.cpp", WHITE, True, hold=0.5),
            L("   状态：codegen-changed — 移除后编译通过，但生成的代码与原来不同", RED, True, hold=0.9),
            L("   （差异原因需人工判断：条件编译、重载解析选中的定义，", DIM, hold=0.4),
            L("   或链接期才暴露的缺失定义都有可能）", DIM, hold=0.7),
            L("代码等价闸门：跑了 1 条，拦下 1 条（汇编变了，已判 codegen-changed）。", WHITE, hold=1.2),
        ],
    )


def seg_evidence() -> Segment:
    return Segment(
        name="⑤ 真实工程硬证据",
        bullets=[
            ("4 个真实项目 · 54 TU · 1430 万行解析量", WHITE, False),
            ("87 个候选 · 0 空头承诺", WHITE, False),
            ("zstd 单条 −46.6%（384 → 205 ms）", GREEN, True),
            ("json 完整构建 −7.1% · 426 万断言逐个一致", GREEN, False),
            ("clang-include-cleaner 同任务：0 条删除建议", YELLOW, False),
            ("我们：1 条实测有效（−43.5%）", YELLOW, False),
        ],
        voice=(
            "真实工程上的硬证据。四个开源项目，五十四个编译单元，"
            "一千四百三十万行解析量，八十七个候选，没有一个空头承诺。"
            "单条最大收益来自 zstd：删一行 include，"
            "三百八十四毫秒降到二百零五，省百分之四十六点六。"
            "完整构建对照：nlohmann json 墙上时间省百分之七点一，"
            "四百二十六万条断言逐个一致。"
            "和 clang-include-cleaner 同任务对照：它零条删除建议，"
            "我们一条实测有效，省百分之四十三点五。"
        ),
        lines=[
            comment("4 个真实开源项目，54 个成功编译单元，1430 万行解析量", 0.5),
            L("| 项目          | TU    | 依赖边  |  解析量   | 候选 |", DIM, hold=0.3),
            L("| fmt           |  3/1  |    651 |   331,016 |   4 |", FG, hold=0.25),
            L("| nlohmann-json | 25/0  |  9,294 | 4,866,676 |  24 |", FG, hold=0.25),
            L("| spdlog        |  8/17 |  3,246 | 1,579,902 |  41 |", FG, hold=0.25),
            L("| cpp-httplib   | 18/7  | 14,724 | 7,523,316 |  18 |", FG, hold=0.25),
            L("| 合计          | 54    | 27,915 |14,300,910 |  87 |", WHITE, True, hold=1.0),
            blank(0.3),
            comment("87 个候选全是项目自己的头，系统头不计入建议；", 0.4),
            comment("成熟项目的 include 大多干净——没有为了凑数字而喊。", 0.8),
            blank(0.3),
            L("$ heavyheader edge-cost …/zstd-1.5.6/lib   # 单条最大收益", BLUE, hold=0.6),
            L("pool.c → allocations.h：384 ms → 205 ms（−46.6%）", GREEN, True, hold=1.2),
            L("解析文件数 184 → 32；同一行 include 在 -DZSTD_MULTITHREAD", DIM, hold=0.4),
            L("下删了直接编译失败——该不该删是构建配置的属性。", DIM, hold=1.0),
        ],
    )


def seg_gate() -> Segment:
    return Segment(
        name="⑥ CI 门禁 gate",
        bullets=[
            ("通过：+0.0% 在 +5% 预算内", GREEN, False),
            ("拦截：+17.1% 超预算 → 非零退出", RED, True),
            ("只比归因行数 · 不比墙钟", WHITE, False),
            ("可用单元数变少 → 直接失败", WHITE, False),
            ("会误报的门禁会被关掉", DIM, False),
        ],
        voice=(
            "结论固化成基线，进 CI 当门禁。"
            "它只比确定性的归因行数，不比墙钟时间。"
            "代码没变，它必须通过；成本回归超预算，它当场失败，"
            "并且指出是哪个头。"
            "一个会误报的门禁会被关掉——关掉的门禁，保护不了任何东西。"
        ),
        lines=[
            L("$ heavyheader gate …/fmt --baseline fmt-baseline.json", BLUE, hold=0.8),
            L("头文件解析量：331,016 → 331,016 (+0.0%)", WHITE, hold=0.5),
            L("门禁通过：成本回归在 +5.0% 预算内。", GREEN, True, hold=1.1),
            blank(0.4),
            L("$ echo '#include <regex>' >> include/core.h   # 顺手加个重头", BLUE, hold=0.7),
            blank(0.2),
            L("$ heavyheader gate …/demo --baseline baseline.json", BLUE, hold=0.8),
            L("头文件解析量：206,100 → 241,300 (+17.1%)", WHITE, hold=0.5),
            L("门禁失败：", RED, True, hold=0.4),
            L("  - header cost regressed +17.1%, budget is +5.0%", RED, hold=0.5),
            L("    worst offender: include/bloated.h 0 → 22,312 lines", RED, hold=1.2),
            blank(0.3),
            comment("盲区也写进报告：行数不变但编译变慢的回归，它测不出。", 1.0),
        ],
    )


def seg_outro() -> Segment:
    return Segment(
        name="",
        bullets=[],
        voice=(
            "HeavyHeader：十五个子命令，七百九十四个单元测试，"
            "零第三方运行时依赖，Apache 二点零协议，已发布至 PyPI。"
            "该报的报，不该报的不报。"
        ),
        lines=[],
    )


def build_segments() -> list[Segment]:
    return [
        seg_intro(),
        seg_problem(),
        seg_scan(),
        seg_verify(),
        seg_linktrap(),
        seg_evidence(),
        seg_gate(),
        seg_outro(),
    ]


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

TERM_X0, TERM_Y0 = 48, 108          # 终端窗口左上
TERM_X1, TERM_Y1 = 1232, 1030       # 终端窗口右下
TITLE_BAR_H = 44
SIDE_X0, SIDE_X1 = 1272, 1876       # 右侧要点栏


def draw_chrome(draw: ImageDraw.ImageDraw, seg_idx: int, segments: list[Segment]) -> None:
    """顶栏 + 右侧要点栏 + 底部进度条。每帧都画（无状态，最简单也最稳）。"""
    seg = segments[seg_idx]

    # 顶栏
    draw.rectangle([(0, 0), (W, 76)], fill=TITLE_BG)
    draw.rectangle([(0, 76), (W, 80)], fill=(60, 70, 88))
    if seg.name:
        draw.text((48, 20), seg.name, font=FONT_HEAD, fill=WHITE)
    brand = "HeavyHeader"
    bw = draw.textlength(brand, font=FONT_HEAD)
    draw.text((W - 48 - bw, 20), brand, font=FONT_HEAD, fill=CYAN)

    # 右侧要点栏
    if seg.bullets:
        y = 140
        draw.text((SIDE_X0, y), "这一步在做什么", font=FONT_BULLET_DIM, fill=DIM)
        y += 62
        for text, color, big in seg.bullets:
            if big:
                font, line_h = FONT_BULLET_BIG, 62
            elif color == DIM:
                font, line_h = FONT_BULLET_DIM, 38
            else:
                font, line_h = FONT_BULLET, 50
            x = SIDE_X0
            for char in text:
                cw = draw.textlength(char, font=font)
                if x + cw > SIDE_X1:
                    break  # 不折行——要点长度在写脚本时控制
                draw.text((x, y), char, font=font, fill=color)
                x += cw
            y += line_h

    # 底部进度条
    bar_y0, bar_y1 = 1052, 1064
    x0, x1 = 48, W - 48
    n = len(segments)
    seg_w = (x1 - x0) / n
    for i in range(n):
        sx = x0 + i * seg_w
        fill = BLUE if i < seg_idx else (CYAN if i == seg_idx else BAR_DIM)
        draw.rectangle([(sx + 4, bar_y0), (sx + seg_w - 4, bar_y1)], fill=fill)


def render_terminal(
    lines: list[tuple[str, tuple, bool]], cursor: str, seg_idx: int,
    segments: list[Segment],
) -> Image.Image:
    img = Image.new("RGB", (W, H), PAGE_BG)
    draw = ImageDraw.Draw(img)
    draw_chrome(draw, seg_idx, segments)

    # 终端窗口 + 标题栏
    draw.rounded_rectangle([(TERM_X0, TERM_Y0), (TERM_X1, TERM_Y1)], radius=10, fill=BG)
    draw.rounded_rectangle(
        [(TERM_X0, TERM_Y0), (TERM_X1, TERM_Y0 + TITLE_BAR_H + 12)],
        radius=10, fill=TITLE_BG,
    )
    draw.rectangle(
        [(TERM_X0, TERM_Y0 + TITLE_BAR_H), (TERM_X1, TERM_Y0 + TITLE_BAR_H + 12)],
        fill=TITLE_BG,
    )
    for i, color in enumerate((RED, YELLOW, GREEN)):
        cx = TERM_X0 + 34 + i * 30
        cy = TERM_Y0 + TITLE_BAR_H / 2
        draw.ellipse([(cx - 7, cy - 7), (cx + 7, cy + 7)], fill=color)
    draw.text(
        (TERM_X0 + 140, TERM_Y0 + 10), TERMINAL_TITLE, font=FONT_CJK, fill=DIM
    )

    # 终端内容（滚动：超出行数时保留最后 N 行）
    text_y0 = TERM_Y0 + TITLE_BAR_H + 12 + 6
    max_lines = (TERM_Y1 - text_y0 - 16) // LINE_H
    visible = lines[-(max_lines - 1):] if cursor else lines[-max_lines:]
    y = text_y0
    for text, color, bold in visible:
        draw_mixed(draw, (TERM_X0 + PAD, y), text, color, bold)
        y += LINE_H
    if cursor:
        draw_mixed(draw, (TERM_X0 + PAD, y), cursor, WHITE)

    return img


def render_card(
    title: str, subtitle: str, tail_lines: list[tuple[str, tuple]],
    seg_idx: int, segments: list[Segment],
) -> Image.Image:
    img = Image.new("RGB", (W, H), PAGE_BG)
    draw = ImageDraw.Draw(img)
    draw_chrome(draw, seg_idx, segments)
    draw.rectangle([(0, 80), (W, H)], fill=TITLE_BG)
    draw.rectangle([(0, 80), (W, 86)], fill=BLUE)

    tw = draw.textlength(title, font=FONT_TITLE)
    draw.text(((W - tw) / 2, 290), title, font=FONT_TITLE, fill=WHITE)
    sw = draw.textlength(subtitle, font=FONT_CARD)
    draw.text(((W - sw) / 2, 430), subtitle, font=FONT_CARD, fill=BLUE)

    y = 560
    for text, color in tail_lines:
        font = FONT_MONO_CARD if text.startswith(("http", "pip", "$")) else FONT_CARD
        cw = draw.textlength(text, font=font)
        draw.text(((W - cw) / 2, y), text, font=font, fill=color)
        y += 56
    return img


# --------------------------------------------------------------------------
# voice (edge-tts)
# --------------------------------------------------------------------------


def synth_voice(text: str, out_path: Path) -> None:
    import edge_tts

    async def _run() -> None:
        com = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+4%")
        await com.save(str(out_path))

    asyncio.run(_run())


def audio_seconds(path: Path) -> float:
    exe = _ffmpeg()
    done = subprocess.run(
        [str(exe), "-i", str(path)], capture_output=True, text=True, errors="replace"
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", done.stderr)
    if not m:
        raise RuntimeError(f"读不出音频时长：{path}\n{done.stderr[-400:]}")
    return int(m.group(1)) * 60 + int(m.group(2)) + float(m.group(3))


# --------------------------------------------------------------------------
# frame generation
# --------------------------------------------------------------------------


def _anim_frame_count(seg: Segment) -> int:
    """数一遍打字动画会产生多少帧（不真正绘制）。"""
    count = 0
    for text, _color, _bold, hold in seg.lines:
        step = max(1, len(text) // 25)
        count += len(range(0, len(text) + 1, step))
        count += 1  # push 后的整行帧
        if hold:
            count += int(hold * FPS)
    return count


def build_frames(
    segments: list[Segment], frames_dir: Path, voice_dir: Path
) -> tuple[int, list[Path]]:
    """渲染全部帧；返回 (帧数, 按顺序拼接的每段旁白 mp3 列表)。"""
    frames_dir.mkdir(parents=True, exist_ok=True)
    voice_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    audio_parts: list[Path] = []
    term: list[tuple[str, tuple, bool]] = []

    def emit(img: Image.Image) -> None:
        nonlocal count
        img.save(frames_dir / f"f{count:05d}.png")
        count += 1

    for idx, seg in enumerate(segments):
        voice_path = voice_dir / f"voice_{idx:02d}.mp3"
        if not voice_path.exists():
            synth_voice(seg.voice, voice_path)
        dur = audio_seconds(voice_path)
        audio_parts.append(voice_path)
        min_frames = math.ceil((dur + 0.5) * FPS)

        if not seg.lines:
            # 全屏卡（片头 / 片尾）
            if idx == 0:
                img = render_card(
                    "HeavyHeader",
                    "C/C++ 构建效能治理平台 · 反事实构建实验",
                    [
                        ("删掉这行 include，是真能省，还是白改？", WHITE),
                        ("每条结论都由真实编译器 A/B 实测给出", DIM),
                        ("github.com/wzzrambler/heavyheader", CYAN),
                    ],
                    idx, segments,
                )
            else:
                img = render_card(
                    "该报的报，不该报的不报",
                    "HeavyHeader · Apache-2.0",
                    [
                        ("15 个子命令 · 794 个单元测试 · 零第三方运行时依赖", WHITE),
                        ("pip install heavyheader", CYAN),
                        ("github.com/wzzrambler/heavyheader", CYAN),
                    ],
                    idx, segments,
                )
            for _ in range(max(int(3.0 * FPS), min_frames)):
                emit(img)
            continue

        # 终端段：动画帧不足 min_frames 时在尾部补静置帧
        extra_tail = max(0, min_frames - _anim_frame_count(seg))

        for text, color, bold, hold in seg.lines:
            step = max(1, len(text) // 25)
            for end in range(0, len(text) + 1, step):
                emit(render_terminal(term, text[:end] + "▌", idx, segments))
            term.append((text, color, bold))
            emit(render_terminal(term, "", idx, segments))
            for _ in range(int(hold * FPS)):
                emit(render_terminal(term, "", idx, segments))
        for _ in range(extra_tail):
            emit(render_terminal(term, "", idx, segments))

    return count, audio_parts


def encode(frames_dir: Path, parts: list[Path], output: Path, frame_count: int) -> None:
    """帧序列 + 多段旁白。音频不先拼 mp3（-c copy 拼 mp3 不可靠，首渲只出 58s/应为 176s），
    而是在编码时用 filter_concat 做采样级拼接，再 apad 补静音到视频长度。"""
    inputs: list[str] = []
    for p in parts:
        inputs += ["-i", str(p)]
    n = len(parts)
    # 输入 0 是帧序列（无音频），旁白 mp3 是输入 1..n——concat 索引别数错。
    concat_in = "".join(f"[{i + 1}:a]" for i in range(n))
    cmd = [
        str(_ffmpeg()), "-y",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "f%05d.png"),
        *inputs,
        "-filter_complex",
        f"{concat_in}concat=n={n}:v=0:a=1[voice];[voice]apad[a]",
        "-map", "0:v",
        "-map", "[a]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "21",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-movflags", "+faststart",
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"encoded {frame_count} frames + {n} voice parts -> {output}")


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def main() -> None:
    # 帧目录带序号，防止上一次的残留帧被 ffmpeg 的 f%05d.png 模式拾取
    # （上次帧数更多时会静默污染输出）。
    out_dir = Path(f"video_build_{len(list(Path('.').glob('video_build_*')))}")
    output = Path("HeavyHeader-演示视频.mp4")
    segments = build_segments()
    print(f"{len(segments)} segments")
    frames, audio_parts = build_frames(segments, out_dir, out_dir / "voice")
    print(f"rendered {frames} frames")
    encode(out_dir, audio_parts, output, frames)
    print("done")


if __name__ == "__main__":
    main()
