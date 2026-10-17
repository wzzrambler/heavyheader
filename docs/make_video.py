"""生成 HeavyHeader 演示视频（MP4）。

用真实运行过的终端输出渲染，不是编造的演示。每一行命令和输出都来自
仓库 benchmarks/ 下可复现的运行，观众可以自己跑一遍核对。

设计约束：
- 帧用 PIL 绘制，ffmpeg 编码，不依赖录屏软件；
- 打字机效果按「可读速度」推进，不为了炫技加速到看不清；
- 视频里出现的每个数字都与 benchmarks/results/ 中固化的数据一致。
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------
# terminal geometry & palette
# --------------------------------------------------------------------------

COLS, ROWS = 104, 32
FONT_SIZE = 17
PAD_X, PAD_Y = 22, 20
LINE_H = int(FONT_SIZE * 1.42)

BG = (18, 22, 28)
FG = (206, 212, 222)
DIM = (122, 132, 148)
GREEN = (86, 199, 130)
RED = (235, 111, 100)
YELLOW = (224, 175, 74)
BLUE = (110, 168, 254)
CYAN = (94, 202, 214)
WHITE = (232, 237, 244)
TITLE_BG = (24, 30, 38)

FONT = ImageFont.truetype("C:/Windows/Fonts/CascadiaMono.ttf", FONT_SIZE)
FONT_BOLD = ImageFont.truetype("C:/Windows/Fonts/CascadiaCode.ttf", FONT_SIZE)
# Cascadia Mono has *zero* CJK coverage (verified: 26/26 test glyphs missing),
# so every CJK character would render as a box.  Render ASCII with Cascadia and
# CJK with a font that has the glyphs, advancing by the real width of each.
# This is also what a real terminal does: a CJK character occupies two columns.
FONT_CJK = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", FONT_SIZE, index=0)
FONT_TITLE = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 40)
FONT_SUB = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)

FPS = 15

# 全局时间缩放。视频必须在 3-5 分钟内（大赛要求），内容扩充后曾到 6:56，
# 因此把停顿和打字速度统一压缩，而不是砍内容——每一段都是 PDF 里有的证据。
TIME_SCALE = 0.48


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x2E80 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF
        or 0x3000 <= code <= 0x303F
    )


def draw_mixed(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill, bold: bool = False) -> None:
    """Draw text, switching to a CJK-capable font for characters that need it."""
    x, y = xy
    ascii_font = FONT_BOLD if bold else FONT
    for char in text:
        font = FONT_CJK if _is_cjk(char) else ascii_font
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font)


# --------------------------------------------------------------------------
# terminal model
# --------------------------------------------------------------------------


@dataclass
class Line:
    text: str
    color: tuple = FG
    bold: bool = False


@dataclass
class Script:
    """A sequence of typing/pause actions."""

    lines: list[tuple[str, tuple, bool]] = field(default_factory=list)
    holds: list[float] = field(default_factory=list)

    def print(self, text: str, color: tuple = FG, bold: bool = False, hold: float = 0.0):
        self.lines.append((text, color, bold))
        self.holds.append(hold)

    def blank(self, hold: float = 0.2):
        self.print("", FG, False, hold)

    def comment(self, text: str, hold: float = 0.3):
        self.print(f"# {text}", DIM, False, hold)


class Terminal:
    """A scrollback of lines plus a partially-typed current line."""

    def __init__(self) -> None:
        self.lines: list[Line] = []

    def push(self, line: Line) -> None:
        self.lines.append(line)
        if len(self.lines) > ROWS - 1:
            self.lines.pop(0)

    def clone(self) -> "Terminal":
        copy = Terminal()
        copy.lines = list(self.lines)
        return copy


def render(term: Terminal, cursor: str = "") -> Image.Image:
    """Draw the terminal state to an RGB image."""
    width = int(COLS * FONT_SIZE * 0.62) + PAD_X * 2
    height = ROWS * LINE_H + PAD_Y * 2
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    for index, line in enumerate(term.lines):
        y = PAD_Y + index * LINE_H
        draw_mixed(draw, (PAD_X, y), line.text, line.color, line.bold)

    if cursor:
        y = PAD_Y + len(term.lines) * LINE_H
        draw_mixed(draw, (PAD_X, y), cursor, WHITE)

    return img


def render_title(title: str, subtitle: str, tagline: str) -> Image.Image:
    """Full-screen title card."""
    width = int(COLS * FONT_SIZE * 0.62) + PAD_X * 2
    height = ROWS * LINE_H + PAD_Y * 2
    img = Image.new("RGB", (width, height), TITLE_BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (width, 6)], fill=BLUE)

    tw = draw.textlength(title, font=FONT_TITLE)
    draw.text(((width - tw) / 2, height * 0.30), title, font=FONT_TITLE, fill=WHITE)

    sw = draw.textlength(subtitle, font=FONT_SUB)
    draw.text(((width - sw) / 2, height * 0.48), subtitle, font=FONT_SUB, fill=BLUE)

    gw = draw.textlength(tagline, font=FONT_SUB)
    draw.text(((width - gw) / 2, height * 0.62), tagline, font=FONT_SUB, fill=DIM)

    return img


# --------------------------------------------------------------------------
# frame generation
# --------------------------------------------------------------------------


def build_frames(script: Script, frames_dir: Path) -> int:
    """Replay the script, writing one PNG per frame. Returns frame count."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    term = Terminal()
    count = 0

    def emit(img: Image.Image) -> None:
        nonlocal count
        img.save(frames_dir / f"f{count:05d}.png")
        count += 1

    # Opening title card, held for 2.5s.
    for _ in range(int(2.5 * TIME_SCALE * FPS)):
        emit(render_title(
            "HeavyHeader",
            "C/C++ 构建效能治理平台",
            "用编译器自己的输出，回答「这个头文件让我多等了几秒」",
        ))

    for (text, color, bold), hold in zip(script.lines, script.holds):
        # Type the line out at a bounded pace: however long the line, typing
        # takes at most ~25 frames (~1.7s).  Without the bound, a long table
        # row would crawl and blow the runtime budget.
        step = max(1, len(text) // 25)
        for end in range(0, len(text) + 1, step):
            emit(render(term, cursor=text[:end] + "▌"))
            if len(text) > 200:
                break
        term.push(Line(text, color, bold))
        emit(render(term))
        if hold > 0:
            for _ in range(int(hold * TIME_SCALE * FPS)):
                emit(render(term))

    # Closing hold.
    for _ in range(int(2.0 * TIME_SCALE * FPS)):
        emit(render(term))

    return count


def encode(frames_dir: Path, output: Path, frame_count: int) -> None:
    exe = _ffmpeg()
    cmd = [
        exe,
        "-y",
        "-framerate", str(FPS),
        "-i", str(frames_dir / "f%05d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "23",
        "-movflags", "+faststart",
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"encoded {frame_count} frames -> {output}")


def _ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


# --------------------------------------------------------------------------
# the script itself — every line below is a real run's output
# --------------------------------------------------------------------------


def build_script() -> Script:
    s = Script()

    s.comment("问题：构建慢，但看不到是哪几行 #include 造成的", 0.6)
    s.blank(0.2)
    s.print("一个 4700 行的头文件被 3000 个编译单元引用时，", DIM, hold=0.35)
    s.print("项目要为它解析 1410 万行——这还只是它自身。", DIM, hold=0.8)
    s.blank(0.3)

    s.print("$ ls benchmarks/cases/demo", BLUE, hold=0.5)
    s.print("include/  app/", FG, hold=0.4)
    s.print("  core.h      被两个 TU 引用（成本热点）", DIM, hold=0.25)
    s.print("  bloated.h   只被 lonely.h 到达的重量级闭包", DIM, hold=0.25)
    s.print("  lonely.h    被 main.cpp 包含但从未使用", YELLOW, hold=0.25)
    s.print("  unguarded.h 刻意不加 guard", DIM, hold=0.8)
    s.blank(0.3)

    s.print("$ heavyheader scan benchmarks/cases/demo \\", BLUE, hold=0.3)
    s.print("    --compiler g++ --std c++17 -I benchmarks/cases/demo/include", BLUE, hold=0.9)
    s.blank(0.2)
    s.print("2 TU · 425 条依赖边 · 206,106 行头文件解析量 · 5 个成本候选（其中 1 个疑似未使用）", WHITE, True, hold=1.2)
    s.blank(0.3)

    s.print("## 成本热点 Top 15", WHITE, True, hold=0.5)
    s.print("| 头文件 | 行数 | 被引用 TU | 归因行数 |", DIM, hold=0.3)
    s.print("| include\\core.h    |   5 | 2 |      10 |", FG, hold=0.25)
    s.print("| include\\bloated.h |   8 | 1 |       8 |", FG, hold=0.25)
    s.print("| include\\lonely.h  |   3 | 1 |       3 |", YELLOW, hold=0.8)
    s.blank(0.3)

    s.print("## 可精简的 include（按预计收益排序）", WHITE, True, hold=0.5)
    s.print("| 编译单元 | include | 独占闭包 | 预计省行数 | 使用状态 |", DIM, hold=0.3)
    s.print("| app\\main.cpp | include\\lonely.h  | 35 |  22,312 | 疑似未使用 |", YELLOW, True, hold=1.0)
    s.print("| app\\main.cpp | include\\core.h    | 95 |  58,633 | 正在使用   |", FG, hold=0.4)
    s.print("| app\\util.cpp | include\\core.h    | 115|  66,014 | 正在使用   |", FG, hold=1.0)
    s.blank(0.4)

    s.print("# 关键：报告区分「成本估计」与「安全性结论」", DIM, hold=0.6)
    s.print("# 「疑似未使用」是文本启发式，不是安全保证。", DIM, hold=0.5)
    s.print("# 权威结论来自 verify：真的删掉、重新编译、实测。", DIM, hold=1.0)
    s.blank(0.4)

    s.print("$ heavyheader verify benchmarks/cases/demo --top 3 --runs 3", BLUE, hold=1.0)
    s.blank(0.2)
    s.print("## lonely.h ← main.cpp", WHITE, True, hold=0.5)
    s.print("   状态：improved — 基线 520 ms → 移除后 320 ms", GREEN, True, hold=0.6)
    s.print("   实测节省：200 ms (38.5%)", GREEN, True, hold=1.5)
    s.blank(0.3)
    s.print("## core.h ← util.cpp", WHITE, True, hold=0.5)
    s.print("   状态：compile-error — 移除后编译失败：", RED, hold=0.4)
    s.print("   util.cpp:3:14: error: 'Core' was not declared in this scope", RED, hold=0.8)
    s.blank(0.3)
    s.print("## core.h ← main.cpp", WHITE, True, hold=0.5)
    s.print("   状态：compile-error — 移除后编译失败：", RED, hold=0.4)
    s.print("   main.cpp:6:14: error: 'Core' was not declared in this scope", RED, hold=1.2)
    s.blank(0.3)
    s.print("小结：1/3 个候选实测有效。", WHITE, True, hold=1.5)
    s.blank(0.4)

    s.print("# 工具没有把三个候选都说成能删。", DIM, hold=0.6)
    s.print("# 两个正在使用的被正确判为「删了会编译失败」。", DIM, hold=1.2)
    s.blank(0.4)

    s.print("$ heavyheader scan /tmp/fmt --exclude test \\", BLUE, hold=0.3)
    s.print("    --save fmt-baseline.json   # 成熟开源项目 fmt", BLUE, hold=1.0)
    s.print("3 TU · 651 条依赖边 · 331,016 行解析量 · 4 个成本候选（0 个疑似未使用）", WHITE, True, hold=1.2)
    s.blank(0.3)
    s.print("$ heavyheader verify /tmp/fmt --top 3 --runs 2", BLUE, hold=1.0)
    s.blank(0.2)
    s.print("## windows.h ← os.cc", WHITE, True, hold=0.4)
    s.print("   状态：compile-error — 'FORMAT_MESSAGE_ALLOCATE_BUFFER' was not declared", RED, hold=0.6)
    s.print("## format-inl.h ← format.cc", WHITE, True, hold=0.4)
    s.print("   状态：compile-error — 'FMT_BEGIN_NAMESPACE' does not name a type", RED, hold=0.6)
    s.print("## os.h ← os.cc", WHITE, True, hold=0.4)
    s.print("   状态：compile-error — 'fmt' does not name a type", RED, hold=1.5)
    s.blank(0.4)
    s.print("# 三个全部正确判定。一个只会喊「你可以删掉这个」的工具，", DIM, hold=0.6)
    s.print("# 第一次误报就会被人关掉。", DIM, hold=1.2)
    s.blank(0.4)

    s.blank(0.4)

    # ---- 模板实例化成本：没人展示过的数字 ----
    s.print("# -ftime-report 一直都报告 template instantiation 阶段，", DIM, hold=0.5)
    s.print("# 但对模板重型 C++ 它常是最大单项。没人把这个数字摆到人面前。", DIM, hold=0.9)
    s.blank(0.2)
    s.print("$ heavyheader scan /tmp/bench/nlohmann-json --std c++17 \\", BLUE, hold=0.3)
    s.print("    -I include --exclude test", BLUE, hold=0.9)
    s.blank(0.2)
    s.print("## 模板实例化成本", WHITE, True, hold=0.4)
    s.print("全项目模板实例化合计 28.16 s，占总构建时间 42.8%。", YELLOW, True, hold=1.0)
    s.print("模板实例化占比超过 25% 的编译单元：22 / 25", YELLOW, hold=0.7)
    s.blank(0.2)
    s.print("| 头文件 | template< 声明数 | 被引用 TU | 模板表面 |", DIM, hold=0.3)
    s.print("| include\\nlohmann\\json.hpp | 173 | 24 | 4152 |", FG, True, hold=1.0)
    s.blank(0.3)
    s.print("# 每个 TU 的数字来自 -ftime-report，精确；", DIM, hold=0.5)
    s.print("# 按头文件的「模板表面」是启发式，报告里明确标注。", DIM, hold=1.0)
    s.blank(0.4)

    # ---- PCH 收益估算 ----
    s.print("$ heavyheader scan /tmp/bench/nlohmann-json --pch --pch-top 5", BLUE, hold=0.9)
    s.blank(0.2)
    s.print("## 预编译头（PCH）收益估算", WHITE, True, hold=0.4)
    s.print("建议纳入 5 个头文件，传递闭包 139,180 行，", FG, hold=0.5)
    s.print("24/25 个编译单元已全部包含它们（覆盖率 96%）。", FG, hold=0.7)
    s.print("估算净收益：3,201,140 行（占全项目 65.8%）——值得做。", GREEN, True, hold=1.2)
    s.blank(0.3)
    s.print("# 四条限制写进报告：行数是代理不是秒数、上限估计、", DIM, hold=0.5)
    s.print("# 未计 PCH 失效、本工具不生成 PCH。", DIM, hold=1.0)
    s.blank(0.4)

    # ---- 前向声明建议 ----
    s.print("$ heavyheader scan /tmp/bench/cpp-httplib --forward-decl", BLUE, hold=0.9)
    s.blank(0.2)
    s.print("## 可改为前向声明的 include", WHITE, True, hold=0.4)
    s.print("| example\\hello.cc | httplib.h | Request | 14 | Response、Server |", FG, hold=0.8)
    s.print("「阻断类型」非空 → 不能整个 include 换掉，只能拆出去。", YELLOW, hold=1.0)
    s.blank(0.4)

    # ---- 四项目横向对比 ----
    s.print("# 4 个真实开源项目，54 个成功编译单元，1430 万行解析量", DIM, hold=0.6)
    s.print("| 项目 | TU | 依赖边 | 解析量 | 候选 | 疑似未使用 |", DIM, hold=0.3)
    s.print("| fmt            |  3/1  |    651 |   331,016 |  4 | 0 |", FG, hold=0.2)
    s.print("| nlohmann-json  | 25/0  |  9,294 | 4,866,676 | 24 | 0 |", FG, hold=0.2)
    s.print("| spdlog         |  8/17 |  3,246 | 1,579,902 | 41 | 0 |", FG, hold=0.2)
    s.print("| cpp-httplib    | 18/7  | 14,724 | 7,523,316 | 18 | 0 |", FG, hold=0.2)
    s.print("| 合计           | 54    | 27,915 |14,300,910 | 87 | 0 |", WHITE, True, hold=1.2)
    s.blank(0.3)
    s.print("# 87 个候选全是项目自己的头——系统头不计入建议，", DIM, hold=0.5)
    s.print("# 因为「建议删掉 <vector>」这种条目会把可操作项埋掉。", DIM, hold=0.5)
    s.print("# 成熟项目的 include 大多是干净的——没有为了凑数字而喊。", DIM, hold=1.2)
    s.blank(0.4)

    s.blank(0.4)

    # ---- 模板实例化成本：没人展示过的数字 ----
    s.print("# -ftime-report 一直都报告 template instantiation 阶段，", DIM, hold=0.5)
    s.print("# 但对模板重型 C++ 它常是最大单项。没人把这个数字摆到人面前。", DIM, hold=0.9)
    s.blank(0.2)
    s.print("$ heavyheader scan /tmp/bench/nlohmann-json --std c++17 \\", BLUE, hold=0.3)
    s.print("    -I include --exclude test", BLUE, hold=0.9)
    s.blank(0.2)
    s.print("## 模板实例化成本", WHITE, True, hold=0.4)
    s.print("全项目模板实例化合计 28.16 s，占总构建时间 42.8%。", YELLOW, True, hold=1.0)
    s.print("模板实例化占比超过 25% 的编译单元：22 / 25", YELLOW, hold=0.7)
    s.blank(0.2)
    s.print("| 头文件 | template< 声明数 | 被引用 TU | 模板表面 |", DIM, hold=0.3)
    s.print("| include\\nlohmann\\json.hpp | 173 | 24 | 4152 |", FG, True, hold=1.0)
    s.blank(0.3)
    s.print("# 每个 TU 的数字来自 -ftime-report，精确；", DIM, hold=0.5)
    s.print("# 按头文件的「模板表面」是启发式，报告里明确标注。", DIM, hold=1.0)
    s.blank(0.4)

    # ---- PCH 收益估算 ----
    s.print("$ heavyheader scan /tmp/bench/nlohmann-json --pch --pch-top 5", BLUE, hold=0.9)
    s.blank(0.2)
    s.print("## 预编译头（PCH）收益估算", WHITE, True, hold=0.4)
    s.print("建议纳入 5 个头文件，传递闭包 139,180 行，", FG, hold=0.5)
    s.print("24/25 个编译单元已全部包含它们（覆盖率 96%）。", FG, hold=0.7)
    s.print("估算净收益：3,201,140 行（占全项目 65.8%）——值得做。", GREEN, True, hold=1.2)
    s.blank(0.3)
    s.print("# 四条限制写进报告：行数是代理不是秒数、上限估计、", DIM, hold=0.5)
    s.print("# 未计 PCH 失效、本工具不生成 PCH。", DIM, hold=1.0)
    s.blank(0.4)

    # ---- 前向声明建议 ----
    s.print("$ heavyheader scan /tmp/bench/cpp-httplib --forward-decl", BLUE, hold=0.9)
    s.blank(0.2)
    s.print("## 可改为前向声明的 include", WHITE, True, hold=0.4)
    s.print("| example\\hello.cc | httplib.h | Request | 14 | Response、Server |", FG, hold=0.8)
    s.print("「阻断类型」非空 → 不能整个 include 换掉，只能拆出去。", YELLOW, hold=1.0)
    s.blank(0.4)

    # ---- 四项目横向对比 ----
    s.print("# 4 个真实开源项目，54 个成功编译单元，1430 万行解析量", DIM, hold=0.6)
    s.print("| 项目 | TU | 依赖边 | 解析量 | 候选 | 疑似未使用 |", DIM, hold=0.3)
    s.print("| fmt            |  3/1  |    651 |   331,016 |  4 | 0 |", FG, hold=0.2)
    s.print("| nlohmann-json  | 25/0  |  9,294 | 4,866,676 | 24 | 0 |", FG, hold=0.2)
    s.print("| spdlog         |  8/17 |  3,246 | 1,579,902 | 41 | 0 |", FG, hold=0.2)
    s.print("| cpp-httplib    | 18/7  | 14,724 | 7,523,316 | 18 | 0 |", FG, hold=0.2)
    s.print("| 合计           | 54    | 27,915 |14,300,910 | 87 | 0 |", WHITE, True, hold=1.2)
    s.blank(0.3)
    s.print("# 87 个候选全是项目自己的头——系统头不计入建议，", DIM, hold=0.5)
    s.print("# 因为「建议删掉 <vector>」这种条目会把可操作项埋掉。", DIM, hold=0.5)
    s.print("# 成熟项目的 include 大多是干净的——没有为了凑数字而喊。", DIM, hold=1.2)
    s.blank(0.4)

    s.print("$ heavyheader gate /tmp/fmt --baseline fmt-baseline.json", BLUE, hold=1.0)
    s.print("头文件解析量：331,016 → 331,016 (+0.0%)", WHITE, hold=0.5)
    s.print("门禁通过：成本回归在 +5.0% 预算内。", GREEN, True, hold=1.2)
    s.blank(0.4)

    s.print("$ heavyheader scan /tmp/fmt --format html -o report.html", BLUE, hold=0.8)
    s.print("已写入 report.html", DIM, hold=1.0)
    s.blank(0.4)

    s.print("─────────────────────────────────────────────", DIM, hold=0.4)
    s.print("HeavyHeader · Apache-2.0 · 72 个单元测试", WHITE, True, hold=0.5)
    s.print("GCC 9+ / Clang 17+ / MSVC · 零第三方运行时依赖", DIM, hold=0.5)
    s.print("该报的报，不该报的不报。", CYAN, True, hold=1.5)

    return s


def main() -> None:
    # 每次用带序号的目录，避免残留帧被 ffmpeg 的 f%05d.png 模式拾取
    # （上一次帧数更多时会静默污染输出）。不删除旧目录——构建产物归用户管。
    out_dir = Path(f"video_build_{len(list(Path('.').glob('video_build_*')))}")
    output = Path("HeavyHeader-演示视频.mp4")
    script = build_script()
    print(f"script has {len(script.lines)} lines")
    frames = build_frames(script, out_dir)
    print(f"rendered {frames} frames")
    encode(out_dir, output, frames)
    print("done")


if __name__ == "__main__":
    main()
