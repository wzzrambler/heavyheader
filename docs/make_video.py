"""生成 HeavyHeader 演示视频（MP4，1920×1080，中文配音）。

用真实运行过的终端输出渲染，不是编造的演示。每一行命令和输出都来自
仓库归档的可复现运行（benchmarks/results/、experiments/results/、
docs/demo-walkthrough.md），观众可以自己跑一遍核对。

2026-10-29 第二稿（按用户反馈打磨）：
- **去掉打字机**：终端输出按步骤一次性给出（保留「命令 → 输出」的节奏），
  不再逐字生成代码；
- **音画逐步同步**：旁白按步骤拆成子句，每个子句单独 TTS，该步骤画面的
  停留时长 = 子句音频时长 + 0.4 s 缓冲——旁白讲到哪，画面正好停在哪一步；
- **底部进度条带环节标签**：八个环节（片头/问题/scan/verify/闸门/证据/门禁/
  片尾）各自一格，当前环节高亮，观众随时知道讲到哪了。

内容口径不变（与 10-29 首稿一致，出处见各段注释）：
- verify 段 = docs/demo-walkthrough.md 的 g++ 实测归档（720→612 ms，−15.1%）；
- 四项目表 = benchmarks/results/summary.md（审计修正后口径）；
- 15 命令 / 794 测试 = 2026-10-28 实测。

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
#: 要点栏字号自适应：宽度固定 604px，长句自动降字号，绝不截断尾部字符。
#: （第二稿渲染时发现 44px 大字放不下「−15.1%）」这类长尾，宁可字小也不丢数字。）
_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _cjk_font(size: int) -> ImageFont.FreeTypeFont:
    key = ("cjk", size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", size)
    return _FONT_CACHE[key]


def _cjk_dim_font(size: int) -> ImageFont.FreeTypeFont:
    key = ("dim", size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)
    return _FONT_CACHE[key]


def _fit_bullet_font(draw: ImageDraw.ImageDraw, text: str, color, big: bool):
    max_w = SIDE_X1 - SIDE_X0
    sizes = [44, 38, 32, 27] if big else [30, 26, 22]
    maker = _cjk_font if color != DIM else _cjk_dim_font
    for size in sizes:
        font = maker(size)
        if draw.textlength(text, font=font) <= max_w:
            return font, int(size * 1.35)
    return maker(sizes[-1]), int(sizes[-1] * 1.35)
FONT_HEAD = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 30)
FONT_TITLE = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 72)
FONT_CARD = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 30)
FONT_MONO_CARD = ImageFont.truetype("C:/Windows/Fonts/CascadiaCode.ttf", 26)
FONT_TAB = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 20)


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
# segments —— 每段 = 环节名 + 右侧要点 + 若干步骤（终端行 + 该步旁白子句）
# --------------------------------------------------------------------------


@dataclass
class Step:
    lines: list[tuple[str, tuple, bool]]    # 本步一次性出现的终端行
    voice: str                              # 本步旁白子句（单独 TTS，精确对齐）


@dataclass
class Segment:
    name: str                                   # 顶栏环节名
    tab: str                                    # 底部进度条上的标签
    bullets: list[tuple[str, str, bool]]        # (文字, 颜色, 是否大字)
    steps: list[Step] = field(default_factory=list)
    card: tuple[str, str, list[tuple[str, tuple]]] | None = None  # 全屏卡内容


def L(text: str, color=FG, bold: bool = False):
    return (text, color, bold)


def comment(text: str):
    return L(f"# {text}", DIM)


TERMINAL_TITLE = "heavyheader — 编译器视角的头文件成本"

# 所有数字的出处：
#   scan/verify/link-trap/gate 输出 = docs/demo-walkthrough.md（g++ 实测归档）
#   四项目表 = benchmarks/results/summary.md（审计修正后口径）
#   zstd −46.6% / json −7.1% / 断言数 / 竞品对照 = docs/benchmark-report.md
#   15 命令 / 794 测试 = 2026-10-28 实测（heavyheader --help / pytest）


def seg_intro() -> Segment:
    return Segment(
        name="",
        tab="片头",
        bullets=[],
        card=(
            "HeavyHeader",
            "C/C++ 构建效能治理平台 · 反事实构建实验",
            [
                ("删掉这行 include，是真能省，还是白改？", WHITE),
                ("每条结论都由真实编译器 A/B 实测给出", DIM),
                ("github.com/wzzrambler/heavyheader", CYAN),
            ],
        ),
        steps=[
            Step(
                [],
                "HeavyHeader，C/C++ 构建效能治理平台。"
                "用编译器自己的输出，回答一个老问题：这个头文件，让我多等了几秒？"
                "删掉它，是真能省，还是白改？",
            )
        ],
    )


def seg_problem() -> Segment:
    return Segment(
        name="① 问题",
        tab="问题",
        bullets=[
            ("4700 行的头 × 3000 个编译单元", WHITE, False),
            ("= 1410 万行解析量", YELLOW, True),
            ("已有工具能回答「哪个头贵」", DIM, False),
            ("没人回答「删了能省多少」", WHITE, False),
        ],
        steps=[
            Step(
                [
                    comment("构建慢，但看不到是哪几行 #include 造成的"),
                    L(""),
                    L("一个 4700 行的头文件被 3000 个编译单元引用时，", DIM),
                    L("项目要为它解析 1410 万行——这还只是它自身。", DIM),
                ],
                "一个四千七百行的头文件，被三千个编译单元引用，"
                "项目要为它解析一千四百一十万行——这还只是它自己。",
            ),
            Step(
                [
                    L("$ ls benchmarks/cases/demo", BLUE),
                    L("include/  app/", FG),
                    L("  core.h     成本热点，被两个 TU 引用", DIM),
                    L("  bloated.h  只经 lonely.h 到达的重量级闭包", DIM),
                    L("  lonely.h   被 main.cpp 包含但从未使用", YELLOW),
                    L("  unguarded.h 刻意不加 guard", DIM),
                ],
                "构建慢的病根在头文件。但已有的工具只能告诉你哪个头贵，"
                "没人敢回答后半个问题：删掉这一行，到底是真省，还是白改。",
            ),
        ],
    )


def seg_scan() -> Segment:
    return Segment(
        name="② 成本归因 scan",
        tab="scan",
        bullets=[
            ("2 TU · 425 条依赖边", WHITE, False),
            ("206,106 行头文件解析量", YELLOW, True),
            ("lonely.h：独占闭包 36 · 预计省 22,316 行（15.9%）", WHITE, False),
            ("使用状态：正在使用", RED, True),
            ("← 判据分不清「被使用」和「顺带被解析」", DIM, False),
        ],
        steps=[
            Step(
                [
                    L("$ heavyheader scan benchmarks/cases/demo \\", BLUE),
                    L("    --compiler g++ --std c++17 -I …/include", BLUE),
                    L(""),
                    L("2 TU · 425 条依赖边 · 206,106 行头文件解析量 · 5 个成本候选",
                      WHITE, True),
                ],
                "扫描不自己解析 C++，让真实编译器报告它做了什么。"
                "这个演示工程只有两个编译单元，却被迫解析二十万行头文件。",
            ),
            Step(
                [
                    L("## 成本热点 Top", WHITE, True),
                    L("| 头文件 | 行数 | 被引用 TU | 归因行数 |", DIM),
                    L("| include\\core.h    |   5 | 2 |      10 |", FG),
                    L("| include\\bloated.h |   8 | 1 |       8 |", FG),
                    L("| include\\lonely.h  |   3 | 1 |       3 |", YELLOW),
                ],
                "成本最高的这一条，独占闭包三十六行，预计省两万两千行。",
            ),
            Step(
                [
                    L("## 可精简的 include（按预计收益排序）", WHITE, True),
                    L("| 编译单元    | include          | 独占闭包 | 预计省行数 | 使用状态 |", DIM),
                    L("| app\\main.cpp | include\\lonely.h |      36 |  22,316 | 正在使用 |",
                      YELLOW, True),
                    L("| app\\util.cpp | include\\core.h   |     115 |  66,014 | 正在使用 |", FG),
                    L(""),
                    comment("「疑似未使用」只是文本启发式，不是安全保证。"),
                    comment("权威结论来自 verify：真的删掉、重新编译、实测。"),
                ],
                "但是注意——使用状态写着：正在使用。"
                "文本判据分不清「被使用」和「顺带被解析」。",
            ),
        ],
    )


def seg_verify() -> Segment:
    return Segment(
        name="③ 反事实验证 verify",
        tab="verify",
        bullets=[
            ("5 个候选：4 编译失败 · 1 improved", WHITE, False),
            ("lonely.h ← main.cpp", WHITE, False),
            ("720 ms → 612 ms（−15.1%）", GREEN, True),
            ("证据档「已验证」：汇编一致 · 复测成立", GREEN, False),
            ("判据说「在用」，编译器说：可删", YELLOW, True),
        ],
        steps=[
            Step(
                [
                    L("$ heavyheader verify benchmarks/cases/demo \\", BLUE),
                    L("    --compiler g++ --ungated-sites --top 5 --runs 3 --confirm 2", BLUE),
                ],
                "所以第二步，别猜——真的删掉，重新编译。"
                "两侧各编三遍取最小值，再复测一遍。",
            ),
            Step(
                [
                    L("## core.h ← util.cpp", WHITE, True),
                    L("   状态：compile-error — 移除后编译失败：", RED),
                    L("   error: 'Core' was not declared in this scope", RED),
                    L("## core.h ← main.cpp", WHITE, True),
                    L("   状态：compile-error — 'Core' was not declared in this scope", RED),
                    L("   （mid.h、unguarded.h 两条同样编译失败，共 4 条）", DIM),
                ],
                "五个候选里四个编译失败，逐个报出了原因。",
            ),
            Step(
                [
                    L("## lonely.h ← main.cpp", WHITE, True),
                    L("   状态：improved — 基线 720 ms → 移除后 612 ms", GREEN, True),
                    L("   实测节省：108 ms（−15.1%）", GREEN, True),
                    L("   证据档：已验证 — 汇编指纹一致 · 实测省时过噪声线 · 复测成立",
                      GREEN),
                    L(""),
                    comment("判据说「在用」的头，编译器说可以删——"),
                    comment("判据说不清的地方，编译器说得清。"),
                ],
                "而这一条，判据刚才说它「在用」——实测，"
                "七百二十毫秒降到六百一十二毫秒，省百分之十五点一，"
                "汇编指纹一致，复测仍然成立。"
                "判据说「在用」的头，编译器说：可以删。",
            ),
        ],
    )


def seg_linktrap() -> Segment:
    return Segment(
        name="④ 汇编闸门 codegen",
        tab="闸门",
        bullets=[
            ("编译通过 ≠ 程序没变", YELLOW, True),
            ("-fsyntax-only 对它完全沉默", WHITE, False),
            ("汇编指纹比对：当场拦下", WHITE, False),
            ("真实案例：−77.8% 的「提升」", RED, False),
            ("实为删掉公共 API 实现", RED, True),
        ],
        steps=[
            Step(
                [
                    L("$ printf '#pragma once\\nint helper();\\n' > decl.h", BLUE),
                    L("$ printf '#include \"decl.h\"\\nint helper(){return 42;}\\n' > defs.h", BLUE),
                    L("$ printf '#include \"decl.h\"\\n#include \"defs.h\"\\n"
                      "int main(){return helper()-42;}\\n' > main.cpp", BLUE),
                    L(""),
                    L("$ heavyheader verify /tmp/linktrap --compiler g++ --check-codegen", BLUE),
                ],
                "更关键的能力，是拦下看起来无害的破坏。"
                "这个十秒钟造出的最小复现：删掉头文件之后，编译照样通过，"
                "连语法检查都完全沉默——但一个函数定义悄悄没了，链接期才会炸。",
            ),
            Step(
                [
                    L("## defs.h ← main.cpp", WHITE, True),
                    L("   状态：codegen-changed — 移除后编译通过，但生成的代码与原来不同",
                      RED, True),
                    L("   （差异原因需人工判断：条件编译、重载解析选中的定义，", DIM),
                    L("   或链接期才暴露的缺失定义都有可能）", DIM),
                    L("代码等价闸门：跑了 1 条，拦下 1 条（汇编变了，已判 codegen-changed）。",
                      WHITE),
                ],
                "只看编译结果的工具会放行；我们比对汇编指纹，当场拦下。"
                "真实工程里，一次负百分之七十七点八的「大幅提升」，"
                "就是这样被抓住的——删掉的其实是公共 API 的实现。",
            ),
        ],
    )


def seg_evidence() -> Segment:
    return Segment(
        name="⑤ 真实工程硬证据",
        tab="证据",
        bullets=[
            ("4 个真实项目 · 54 TU · 1430 万行解析量", WHITE, False),
            ("87 个候选 · 0 空头承诺", WHITE, False),
            ("zstd 单条 −46.6%（384 → 205 ms）", GREEN, True),
            ("json 完整构建 −7.1% · 426 万断言逐个一致", GREEN, False),
            ("clang-include-cleaner 同任务：0 条删除建议", YELLOW, False),
            ("我们：1 条实测有效（−43.5%）", YELLOW, False),
        ],
        steps=[
            Step(
                [
                    comment("4 个真实开源项目，54 个成功编译单元，1430 万行解析量"),
                    L("| 项目          | TU    | 依赖边  |  解析量   | 候选 |", DIM),
                    L("| fmt           |  3/1  |    651 |   331,016 |   4 |", FG),
                    L("| nlohmann-json | 25/0  |  9,294 | 4,866,676 |  24 |", FG),
                    L("| spdlog        |  8/17 |  3,246 | 1,579,902 |  41 |", FG),
                    L("| cpp-httplib   | 18/7  | 14,724 | 7,523,316 |  18 |", FG),
                    L("| 合计          | 54    | 27,915 |14,300,910 |  87 |", WHITE, True),
                    L(""),
                    comment("87 个候选全是项目自己的头；成熟项目大多干净——"),
                    comment("没有为了凑数字而喊。"),
                ],
                "真实工程上的硬证据。四个开源项目，五十四个编译单元，"
                "一千四百三十万行解析量，八十七个候选，没有一个空头承诺。",
            ),
            Step(
                [
                    L("$ heavyheader edge-cost …/zstd-1.5.6/lib   # 单条最大收益", BLUE),
                    L("pool.c → allocations.h：384 ms → 205 ms（−46.6%）", GREEN, True),
                    L("解析文件数 184 → 32", DIM),
                    L(""),
                    L("同一行 include 在 -DZSTD_MULTITHREAD 下删了直接编译失败", DIM),
                    L("——该不该删是构建配置的属性。", DIM),
                ],
                "单条最大收益来自 zstd：删一行 include，"
                "三百八十四毫秒降到二百零五，省百分之四十六点六。",
            ),
            Step(
                [
                    comment("完整构建对照：nlohmann/json，63 个编译单元"),
                    L("墙上时间 −7.1% · 编译 CPU −11.4%", GREEN, True),
                    L("4,264,414 条断言逐个一致", GREEN, True),
                    L(""),
                    comment("与 clang-include-cleaner 同工程同任务对照："),
                    comment("它 0 条删除建议 / 101 条补充建议；"),
                    comment("我们 1 条实测有效（−43.5%，三个维度全部实测）。"),
                ],
                "完整构建对照：nlohmann json 墙上时间省百分之七点一，"
                "四百二十六万条断言逐个一致。"
                "和 clang-include-cleaner 同任务对照：它零条删除建议，"
                "我们一条实测有效，省百分之四十三点五。",
            ),
        ],
    )


def seg_gate() -> Segment:
    return Segment(
        name="⑥ CI 门禁 gate",
        tab="门禁",
        bullets=[
            ("通过：+0.0% 在 +5% 预算内", GREEN, False),
            ("拦截：+17.1% 超预算 → 非零退出", RED, True),
            ("只比归因行数 · 不比墙钟", WHITE, False),
            ("可用单元数变少 → 直接失败", WHITE, False),
            ("会误报的门禁会被关掉", DIM, False),
        ],
        steps=[
            Step(
                [
                    L("$ heavyheader gate …/fmt --baseline fmt-baseline.json", BLUE),
                    L("头文件解析量：331,016 → 331,016 (+0.0%)", WHITE),
                    L("门禁通过：成本回归在 +5.0% 预算内。", GREEN, True),
                ],
                "结论固化成基线，进 CI 当门禁。"
                "它只比确定性的归因行数，不比墙钟时间。"
                "代码没变，它必须通过。",
            ),
            Step(
                [
                    L("$ echo '#include <regex>' >> include/core.h   # 顺手加个重头", BLUE),
                    L(""),
                    L("$ heavyheader gate …/demo --baseline baseline.json", BLUE),
                    L("头文件解析量：206,100 → 241,300 (+17.1%)", WHITE),
                    L("门禁失败：", RED, True),
                    L("  - header cost regressed +17.1%, budget is +5.0%", RED),
                    L("    worst offender: include/bloated.h 0 → 22,312 lines", RED),
                    L(""),
                    comment("盲区也写进报告：行数不变但编译变慢的回归，它测不出。"),
                ],
                "成本回归超预算，它当场失败，并且指出是哪个头。"
                "一个会误报的门禁会被关掉——关掉的门禁，保护不了任何东西。",
            ),
        ],
    )


def seg_outro() -> Segment:
    return Segment(
        name="",
        tab="片尾",
        bullets=[],
        card=(
            "该报的报，不该报的不报",
            "HeavyHeader · Apache-2.0",
            [
                ("15 个子命令 · 794 个单元测试 · 零第三方运行时依赖", WHITE),
                ("pip install heavyheader", CYAN),
                ("github.com/wzzrambler/heavyheader", CYAN),
            ],
        ),
        steps=[
            Step(
                [],
                "HeavyHeader：十五个子命令，七百九十四个单元测试，"
                "零第三方运行时依赖，Apache 二点零协议，已发布至 PyPI。"
                "该报的报，不该报的不报。",
            )
        ],
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
TERM_X1, TERM_Y1 = 1232, 996        # 终端窗口右下（底部留出带标签的进度条）
TITLE_BAR_H = 44
SIDE_X0, SIDE_X1 = 1272, 1876       # 右侧要点栏


def draw_chrome(draw: ImageDraw.ImageDraw, seg_idx: int, segments: list[Segment]) -> None:
    """顶栏 + 右侧要点栏 + 底部带标签的环节进度条。"""
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
        y = 130
        draw.text((SIDE_X0, y), "这一步在做什么", font=_cjk_dim_font(24), fill=DIM)
        y += 58
        for text, color, big in seg.bullets:
            font, line_h = _fit_bullet_font(draw, text, color, big)
            draw.text((SIDE_X0, y), text, font=font, fill=color)
            y += line_h

    # 底部进度条：每环节一格，格内写环节标签，当前环节高亮
    x0, x1 = 48, W - 48
    tab_y0, tab_h = 1022, 40
    n = len(segments)
    seg_w = (x1 - x0) / n
    for i, s in enumerate(segments):
        sx = x0 + i * seg_w
        if i < seg_idx:
            fill, text_fill = (36, 52, 74), (150, 170, 200)
        elif i == seg_idx:
            fill, text_fill = CYAN, (10, 30, 36)
        else:
            fill, text_fill = BAR_DIM, DIM
        draw.rounded_rectangle(
            [(sx + 5, tab_y0), (sx + seg_w - 5, tab_y0 + tab_h)], radius=8, fill=fill
        )
        tw = draw.textlength(s.tab, font=FONT_TAB)
        draw.text(
            (sx + (seg_w - tw) / 2, tab_y0 + (tab_h - 26) / 2),
            s.tab, font=FONT_TAB, fill=text_fill,
        )


def render_terminal(
    lines: list[tuple[str, tuple, bool]], seg_idx: int, segments: list[Segment]
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
    draw.text((TERM_X0 + 140, TERM_Y0 + 10), TERMINAL_TITLE, font=FONT_CJK, fill=DIM)

    # 终端内容（滚动：超出行数时保留最后 N 行）
    text_y0 = TERM_Y0 + TITLE_BAR_H + 18
    max_lines = (TERM_Y1 - text_y0 - 16) // LINE_H
    visible = lines[-max_lines:]
    y = text_y0
    for text, color, bold in visible:
        draw_mixed(draw, (TERM_X0 + PAD, y), text, color, bold)
        y += LINE_H

    return img


def render_card(
    title: str, subtitle: str, tail_lines: list[tuple[str, tuple]],
    seg_idx: int, segments: list[Segment],
) -> Image.Image:
    img = Image.new("RGB", (W, H), PAGE_BG)
    draw = ImageDraw.Draw(img)
    draw_chrome(draw, seg_idx, segments)
    draw.rectangle([(0, 80), (W, 1000)], fill=TITLE_BG)
    draw.rectangle([(0, 80), (W, 86)], fill=BLUE)

    tw = draw.textlength(title, font=FONT_TITLE)
    draw.text(((W - tw) / 2, 280), title, font=FONT_TITLE, fill=WHITE)
    sw = draw.textlength(subtitle, font=FONT_CARD)
    draw.text(((W - sw) / 2, 420), subtitle, font=FONT_CARD, fill=BLUE)

    y = 550
    for text, color in tail_lines:
        font = FONT_MONO_CARD if text.startswith(("http", "pip", "$")) else FONT_CARD
        cw = draw.textlength(text, font=font)
        draw.text(((W - cw) / 2, y), text, font=font, fill=color)
        y += 56
    return img


# --------------------------------------------------------------------------
# voice (edge-tts) —— 每步一个子句，单独合成，画面停留时长 = 子句时长
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


def build_frames(
    segments: list[Segment], frames_dir: Path, voice_dir: Path
) -> tuple[int, list[Path]]:
    """渲染全部帧；返回 (帧数, 按顺序拼接的每步旁白 mp3 列表)。

    同步机制：每个步骤的画面停留帧数 = ceil(该步旁白时长 * FPS) + 缓冲，
    音频轨按相同顺序拼接——旁白讲到哪一步，画面正好停在哪一步。
    """
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
        for j, step in enumerate(seg.steps):
            voice_path = voice_dir / f"voice_{idx:02d}_{j:02d}.mp3"
            if not voice_path.exists():
                synth_voice(step.voice, voice_path)
            dur = audio_seconds(voice_path)
            audio_parts.append(voice_path)

            if seg.card is not None:
                title, subtitle, tail = seg.card
                img = render_card(title, subtitle, tail, idx, segments)
            else:
                term.extend(step.lines)
                img = render_terminal(term, idx, segments)

            # 停留时长 = 旁白 + 0.4s 缓冲；再加可读下限（至少 1.2s，每行 +1 帧），
            # 防止旁白短、行多时翻页太快看不清。
            frames = math.ceil((dur + 0.4) * FPS)
            frames = max(frames, int(1.2 * FPS) + len(step.lines))
            for _ in range(frames):
                emit(img)

    return count, audio_parts


def encode(frames_dir: Path, parts: list[Path], output: Path, frame_count: int) -> None:
    """帧序列 + 多段旁白。音频不先拼 mp3（-c copy 拼 mp3 不可靠），在编码时
    用 filter_concat 做采样级拼接，再 apad 补静音到视频长度。"""
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
    steps_total = sum(len(s.steps) for s in segments)
    print(f"{len(segments)} segments, {steps_total} steps")
    frames, audio_parts = build_frames(segments, out_dir, out_dir / "voice")
    print(f"rendered {frames} frames")
    encode(out_dir, audio_parts, output, frames)
    print("done")


if __name__ == "__main__":
    main()
