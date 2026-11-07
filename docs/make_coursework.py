"""生成课程大作业文档（竞赛项目方向）。

课程：智能云原生系统设计与内核生态实践
方向：大作业方向 2「竞赛项目」——提交文档（项目背景、技术方案、架构设计、
      开源边界与协议等）与代码。

复用 docs/make_pdf.py 的样式与排版工具（中文字体、表格、代码块），
不重复实现一套。

诚实性约束同作品介绍文档：数字均来自仓库归档，区分实测与推算，
已知边界单独成节，不承诺未做到的事。
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Spacer,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_pdf import (  # noqa: E402
    ACCENT,
    CODE,
    INK,
    MUTED,
    RULE,
    S,
    SP,
    TABLE,
    P,
    _FONT,
    _MONO,
)

COURSE = "智能云原生系统设计与内核生态实践"
AUTHOR = "王子周"
STUDENT_ID = "202430552721"
TITLE = "HeavyHeader：C/C++ 构建效能治理平台"


def _on_cover(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(2)
    canvas.line(20 * mm, 250 * mm, 190 * mm, 250 * mm)
    canvas.setFont(_FONT, 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(105 * mm, 14 * mm, "Apache-2.0 · 代码与全部实验记录随附仓库")
    canvas.restoreState()


def _on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont(_FONT, 8.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 285 * mm, "HeavyHeader · 大作业（竞赛项目）")
    canvas.drawRightString(190 * mm, 285 * mm, COURSE)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 283 * mm, 190 * mm, 283 * mm)
    canvas.line(20 * mm, 14 * mm, 190 * mm, 14 * mm)
    canvas.drawString(20 * mm, 10 * mm, f"{AUTHOR} · {STUDENT_ID}")
    canvas.drawRightString(190 * mm, 10 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def build(path: str) -> None:
    doc = BaseDocTemplate(
        path,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=22 * mm,
        bottomMargin=20 * mm,
        title="HeavyHeader 大作业（竞赛项目）",
        author=AUTHOR,
        subject=f"{COURSE} 大作业",
    )
    frame_cover = Frame(20 * mm, 20 * mm, 170 * mm, 225 * mm, id="cover")
    frame_body = Frame(20 * mm, 20 * mm, 170 * mm, 258 * mm, id="body")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame_cover], onPage=_on_cover),
        PageTemplate(id="body", frames=[frame_body], onPage=_on_page),
    ])

    story: list = []

    # ------------------------------------------------------------------ 封面
    story += [
        SP(52),
        P("大作业 · 竞赛项目方向", "subtitle"),
        SP(10),
        P("HeavyHeader", "title"),
        SP(2),
        P("C/C++ 构建效能治理平台", "subtitle"),
        SP(4),
        P("用反事实构建实验回答「删掉这条 include 到底能省多少」", "meta"),
        SP(30),
        P(f"课程：{COURSE}", "meta"),
        P(f"姓名：{AUTHOR}　　学号：{STUDENT_ID}", "meta"),
        P("完成方式：个人独立完成（1 人）", "meta"),
        P("参赛情况：第四届开放原子大赛 · 2026 开源行业解决方案创新赛 赛道一", "meta"),
        P("2026 年 11 月", "meta"),
        SP(20),
        P("代码仓库：https://github.com/wzzrambler/heavyheader", "meta"),
        P("许可证：Apache-2.0　|　单元测试：794 条　|　CLI 子命令：15 个", "meta"),
        NextPageTemplate("body"),
        PageBreak(),
    ]

    # ------------------------------------------------------------ 一、背景
    story += [
        P("一、项目背景", "h1"),
        P(
            "云原生交付的瓶颈常常不在代码本身，而在构建。一个 C++ 服务的镜像流水线里，"
            "编译阶段往往占掉大半时间，而其中绝大部分 CPU 消耗在<b>头文件解析</b>："
            "一个头文件被多个编译单元包含，就要被重复解析多次。这件事在本地开发时是"
            "「等一会儿」，在 CI 里就是按分钟计费、按队列排队的真实成本。"
        ),
        P(
            "问题是，现有工具只回答了一半。<b>ClangBuildAnalyzer</b>、<b>Build Insights</b>"
            "（MSVC）、<b>CompileScore</b> 一类工具能告诉你哪个头文件最贵、"
            "被包含多少次；<b>include-what-you-use</b>、<b>clang-include-cleaner</b> 一类"
            "能告诉你某个 include 在语义上有没有必要。但没有工具回答第二句："
            "<b>把这一行 include 删掉之后，这次构建到底是变快了，还是白改</b>——"
            "以及更重要的，<b>删掉之后程序还是不是原来那个程序</b>。"
        ),
        P(
            "本项目把第二句话做成可执行的东西：对每一条候选 include 做一次"
            "<b>反事实构建实验</b>（Counterfactual Build Experiment，CBE）——"
            "真的删掉、真的重新编译、真的测时、真的比对汇编指纹，然后给出带证据的结论，"
            "并把这个结论固化成 CI 门禁。"
        ),
        SP(2),
        TABLE(
            ["问题层次", "谁在回答", "回答的内容"],
            [
                ["哪个头最贵", "ClangBuildAnalyzer / Build Insights",
                 "逐头文件的累计耗时、被包含次数"],
                ["这条 include 该不该存在", "IWYU / clang-include-cleaner",
                 "AST 层：符号是否被使用（正确性问题）"],
                ["<b>删掉它省多少、能不能删</b>", "<b>本项目</b>",
                 "<b>实测耗时差 + 汇编指纹 + 复测；给出三态结论</b>"],
            ],
            widths=[110, 150, 190],
        ),
        P("表 1　三类问题与对应工具。本项目只做第三行，不与前两类工具争同一件事。", "caption"),
    ]

    # ------------------------------------------------------------ 二、方案
    story += [
        P("二、技术方案", "h1"),
        P("2.1 一次编译，三个问题", "h2"),
        P(
            "工具不自己实现 C++ 解析器，而是让<b>真实编译器</b>报告自己做了什么："
            "GCC/Clang 用 <font face='{0}'>-H</font> 给出包含树，MSVC 用 "
            "<font face='{0}'>/showIncludes</font>，Clang 可选 "
            "<font face='{0}'>-ftime-trace</font> 给出逐文件耗时。一次扫描同时回答："
            "① 每个头文件被解析了多少行（成本）；② 每条 include 边的"
            "<b>独占闭包</b>（删掉它，只有哪些文件会停止被解析）；③ 这条 include 在"
            "文本与符号层是否看起来被使用。"
            .format(_MONO, _MONO, _MONO)
        ),
        P("2.2 收益怎么算：归因行数与独占闭包", "h2"),
        P(
            "<b>归因行数</b> = 头文件自身行数 × 引用它的编译单元数，衡量的是"
            "「整个项目为它多解析了多少行」。它是确定性的：同一棵树扫三遍，字节级一致，"
            "因此适合做 CI 门禁（<b>不比墙钟时间</b>——后者随负载波动，会误触发）。"
        ),
        P(
            "<b>独占闭包</b>更接近真实收益：一个头若还能从别的 include 到达，"
            "删掉当前这条并不省解析。本项目按「删掉后真正消失的文件集合」计算，"
            "并用实测真值验证过这套口径：25 条可删边的假阳性由 45 个文件降到 0。"
        ),
        P("2.3 三态决策：不给「疑似」，给结论", "h2"),
        P(
            "候选经探针实测后落进三个互斥结论之一：<b>建议删除</b>（编译通过且耗时"
            "下降超过 2% 噪声线）、<b>保留</b>（编译器已否决）、<b>需人工判断</b>"
            "（编译可行但收益在噪声内，附倾向与理由）。未实测的候选显示「未实测」——"
            "没测过就不下判断，这是本项目最在意的一条纪律。"
        ),
        P("2.4 证据分档：编译通过不等于程序没变", "h2"),
        P(
            "只测「编译通过 + 耗时下降」是不够的：删掉一条 include 后编译照过、"
            "时间还降了，但可能悄悄删掉了一个函数定义，问题要到链接期才暴露。"
            "因此判定分三档证据：<b>verified</b>（汇编指纹一致 + 省时过噪声线 + 复测成立）、"
            "<b>equivalent</b>（汇编一致但收益不显著）、<b>compiles_only</b>"
            "（只能证明编得过，等价性没有证据）。"
            "<b>汇编指纹是「代码生成一致」的强证据，不是完整语义等价的证明</b>——"
            "这条边界写在报告里，也写在代码注释里。"
        ),
        SP(2),
        CODE(
            "# 一条候选的完整判定链路（摘自 demo 语料实测）\n"
            "$ heavyheader scan benchmarks/cases/demo --compiler g++ --std c++17\n"
            "2 TU · 425 条依赖边 · 206,106 行头文件解析量 · 5 个成本候选\n"
            "\n"
            "$ heavyheader verify benchmarks/cases/demo --ungated-sites --top 5 --runs 3 --confirm 2\n"
            "## core.h ← util.cpp      状态：compile-error — 'Core' was not declared\n"
            "## lonely.h ← main.cpp    状态：improved — 基线 720 ms → 移除后 612 ms\n"
            "                          证据档：已验证（汇编指纹一致 · 过噪声线 · 复测成立）\n"
            "\n"
            "# 判据说「在用」的候选，编译器给出了相反结论：判据说不清的地方，编译器说得清。"
        ),
    ]

    # ------------------------------------------------------------ 三、架构
    story += [
        P("三、架构设计", "h1"),
        P("3.1 分层", "h2"),
        P(
            "工具按「采集层 → 归因层 → 判定层 → 消费层」四层组织，每层只依赖下一层，"
            "不做跨层调用。这样做的好处是判定逻辑只有一份实现：CLI、CI 门禁、编辑器"
            "扩展读的都是同一份 JSON 结论，不存在「命令行说能删、CI 说不能删」的情况。"
        ),
        SP(2),
        CODE(
            "cli.py                 15 个子命令的统一入口（argparse）\n"
            "  ├─ drivers/          编译器驱动：g++ / clang++ / cl（含重试与超时）\n"
            "  ├─ scan/             包含树采集：-H / showIncludes / -ftime-trace 解析\n"
            "  ├─ attrib/           归因层：归因行数、独占闭包、模板实例化、PCH 估算\n"
            "  │   ├─ advice.py     三态决策（纯函数，确定性）\n"
            "  │   ├─ evidence.py   三档证据（verified / equivalent / compiles_only）\n"
            "  │   ├─ crosscheck.py 与 IWYU / include-cleaner 的对位口径（共用一份）\n"
            "  │   └─ diagnosis.py  0 候选时逐条给出被拦原因\n"
            "  ├─ probe/            实测层：verify / pch-verify / forward-verify\n"
            "  └─ report/           Markdown / HTML（自包含单文件）/ JSON\n"
            "cmake/  meson/  editors/vscode/     三个接入层，不含第二份判定逻辑"
        ),
        P("3.2 六个刻意的设计选择", "h2"),
        TABLE(
            ["选择", "理由"],
            [
                ["零第三方运行时依赖",
                 "工具要进别人的 CI。装不上的工具等于不存在；零依赖由 "
                 "<font face='{0}'>tests/test_no_deps.py</font> 守护，新增依赖会让 CI 直接失败".format(_MONO)],
                ["只调编译器，不内嵌 C++ 前端",
                 "解析器复现不了真实编译命令，猜错时不报错、只让大量单元以「找不到头文件」"
                 "失败——报告每个数字都正常，描述的却不是你的项目"],
                ["门禁只比归因行数，不比墙钟",
                 "墙钟随负载波动，会误触发；误触发的门禁会被关掉，关掉的门禁保护不了任何东西"],
                ["报告是自包含单文件 HTML",
                 "评审/同事双击就能看，不需要装服务、不需要联网"],
                ["失败占比按档措辞",
                 "≤20% 说「可能偏低」，>50% 直接说「不足以支撑任何结论」——"
                 "不让措辞比证据更乐观"],
                ["实测不可逆",
                 "verify 用注释而非删除（.orig 备份 + 退出即恢复），实验必须可回退"],
            ],
            widths=[120, 330],
        ),
        P("3.3 一条命令流水线", "h2"),
        P(
            "15 个子命令不是 15 个卖点，是同一条流水线上的环节："
            "<font face='{0}'>scan</font>（发现）→ "
            "<font face='{0}'>verify / pch-verify / forward-verify</font>（验证）→ "
            "<font face='{0}'>gate / configs / compare</font>（治理）→ "
            "<font face='{0}'>blame / edge-cost / history / pr-comment</font>（追溯与呈现）→ "
            "<font face='{0}'>iwyu / include-cleaner / toolchains</font>（对接与横评）。"
            .format(_MONO, _MONO, _MONO, _MONO, _MONO)
        ),
    ]

    # -------------------------------------------------- 四、开源边界与协议
    story += [
        P("四、开源边界与协议", "h1"),
        P("4.1 许可证与理由", "h2"),
        P(
            "项目采用 <b>Apache-2.0</b>。选择理由三条：① 含专利授权条款，对企业 CI "
            "场景不是形式问题；② 与生态中大量 C++ 项目（LLVM 生态、多数基础库）兼容，"
            "便于被嵌入；③ 对贡献者与使用者都明确，不需要额外解释。"
        ),
        P("4.2 运行时边界：零第三方依赖", "h2"),
        P(
            "除 Python 标准库外不引入任何运行时依赖，也不内嵌 C++ 前端。"
            "所有「编译相关」的能力都通过调用开发者机器上已有的工具获得："
            "<font face='{0}'>g++</font>/<font face='{0}'>clang++</font>/"
            "<font face='{0}'>cl</font>（必需）、<font face='{0}'>gch</font>（仅 PCH 实测时）、"
            "<font face='{0}'>include-what-you-use</font> 与 "
            "<font face='{0}'>clang-include-cleaner</font>（可选对位，缺失时不降级结论、只标「未参与分析」）。"
            "零依赖不是口号：<font face='{0}'>tests/test_no_deps.py</font> 在 CI 里强制检查。"
            .format(_MONO, _MONO, _MONO, _MONO, _MONO, _MONO, _MONO)
        ),
        P("4.3 与外部工具的边界", "h2"),
        TABLE(
            ["外部工具", "本项目的用法", "边界（不做什么）"],
            [
                ["GCC / Clang / MSVC", "调用其编译与预处理输出，解析包含树与计时",
                 "不修改编译器、不注入插件"],
                ["include-what-you-use", "解析其输出，与自己的成本候选对位",
                 "<b>本项目构建不出 IWYU 二进制</b>，该对位未做端到端实测，报告正文写明"],
                ["clang-include-cleaner", "同上，且已用本机真实进程输出校准格式",
                 "不把「它没建议删」当成「可以删」——编译失败的文件单列且不参与对位"],
                ["CMake / Meson / VSCode", "以插件/构建目标方式接入，调用 CLI",
                 "接入层不含第二份判定逻辑"],
            ],
            widths=[110, 175, 165],
        ),
        P("4.4 数据与可复现边界", "h2"),
        P(
            "所有上屏数字都有归档：<font face='{0}'>benchmarks/results/</font>（四项目扫描）、"
            "<font face='{0}'>experiments/results/</font>（逐轮实验，含失败与否定结论）。"
            "报告里的每个大数字都附「如何复现」的入口。"
            "<b>明确不做的事</b>：不宣称「自动优化」（G2 优化闭环实测为 doctest 1/16、"
            "zlib 1/16、json 0/16，如实写在文档里）；不宣称「零假阳性」的绝对版本"
            "（该结论只在「删了还能编过」这一层成立）；不把推算当实测"
            "（行数与秒之间没有诚实的换算系数）。"
            .format(_MONO, _MONO)
        ),
        P("4.5 治理与上游协作", "h2"),
        P(
            "仓库含 GOVERNANCE / CONTRIBUTING / CODEOWNERS / SECURITY / "
            "Issue 与 PR 模板，以及三个 CI 工作流（3 系统 × 4 Python 版本矩阵、"
            "覆盖率、MSVC 真实运行证据）。向上游提交过两个真实优化 PR："
            "<b>doctest#1212</b>（−3.01%）、<b>madler/zlib#1344</b>（−2.125%），"
            "截至本文均为 open、未被合并——文中只写「已提交」，不写「已被采用」。"
        ),
    ]

    # ------------------------------------------------------------ 五、验证
    story += [
        P("五、验证与实测结果", "h1"),
        P("5.1 四个真实开源项目上的扫描结果", "h2"),
        TABLE(
            ["项目", "编译单元（成功/失败）", "依赖边", "头文件解析量", "成本候选"],
            [
                ["fmt", "3 / 1", "651", "331,016", "4"],
                ["nlohmann/json", "25 / 0", "9,294", "4,866,676", "24"],
                ["spdlog", "8 / 17", "3,246", "1,579,902", "41"],
                ["cpp-httplib", "18 / 7", "14,724", "7,523,316", "18"],
                ["<b>合计</b>", "<b>54</b>", "<b>27,915</b>", "<b>14,300,910</b>", "<b>87</b>"],
            ],
            widths=[100, 120, 80, 100, 80],
        ),
        P("表 2　失败单元如实计入（缺 CMake 配置头、缺 OpenSSL 头等），没有为凑数字跳过。", "caption"),
        P("5.2 三个关键收益数字", "h2"),
        P(
            "① <b>单条最大收益</b>：zstd 1.5.6 上删一条 include（"
            "<font face='{0}'>pool.c → allocations.h</font>），编译耗时 384 ms → 205 ms"
            "（<b>−46.6%</b>），解析文件数 184 → 32。"
            "② <b>完整构建对照</b>：nlohmann/json 63 个编译单元，墙上时间 −7.1%、"
            "编译 CPU −11.4%，4,264,414 条断言逐个一致。"
            "③ <b>与竞品同任务对照</b>：clang-include-cleaner 在同一批候选上给出 0 条"
            "删除建议、101 条补充建议；本工具给出的一条经三维度实测有效（−43.5%）。"
            .format(_MONO)
        ),
        P(
            "同一行 include 在两种构建配置下答案可能相反：zstd 的这条在默认配置下删了"
            "能编过且汇编一致，加上 <font face='{0}'>-DZSTD_MULTITHREAD</font> 之后删了"
            "直接编译失败。<b>「该不该删」不是源码的属性，是构建配置的属性</b>——"
            "这条结论现在是一条命令（<font face='{0}'>configs</font>）。"
            .format(_MONO, _MONO)
        ),
        P("5.3 如实记录的边界", "h2"),
        TABLE(
            ["边界", "实际情况"],
            [
                ["自动优化闭环", "成立但有限：doctest 1/16、zlib 1/16、json 0/16；不宣传「自动优化」"],
                ["候选判定精度", "222 个候选里 187 个编译不过（文本层的原理性局限，AST 层才能根治）"],
                ["前向声明建议", "三个真实语料上候选数为 0（合成语料 301.7→41.9 ms 只证明机制，不当效能证据）"],
                ["PCH 实测", "json 24 个单元里 9 个加 PCH 后编不过——采用 PCH 的真实代价，估算表达不了"],
                ["平台覆盖", "MSVC 只跑通 scan 与 gate；增量构建税仍在原型阶段，不作结论引用"],
            ],
            widths=[110, 340],
        ),
    ]

    # ------------------------------------------------------ 六、代码与复现
    story += [
        P("六、代码与复现入口", "h1"),
        P(
            "仓库：<font face='{0}'>https://github.com/wzzrambler/heavyheader</font>"
            "（Apache-2.0，含全部源码、测试、基准数据与实验归档）。"
            "已在 PyPI 发布 v0.1.0，可直接安装。"
            "<b>随本作业一并提交源码压缩包</b>"
            "（<font face='{0}'>HeavyHeader-source.zip</font>：src/ tests/ benchmarks/ "
            "experiments/ docs/ 与工程配置，不含构建产物与演示视频）。"
            .format(_MONO, _MONO)
        ),
        SP(2),
        CODE(
            "# 安装与最小验证\n"
            "pip install heavyheader\n"
            "heavyheader scan benchmarks/cases/demo --compiler g++ --std c++17\n"
            "heavyheader verify benchmarks/cases/demo --ungated-sites --top 5 --runs 3 --confirm 2\n"
            "heavyheader gate benchmarks/cases/demo --baseline baseline.json\n"
            "\n"
            "# 本地开发与回归\n"
            "python -m pytest --no-header        # 794 passed / 10 skipped\n"
            "ruff check src tests                # 0 违规"
        ),
        P(
            "测试共 794 条，覆盖解析器（用真实编译器输出逐字复制的样本）、归因算法、"
            "门禁、实测流程、并发与失败路径；10 条因环境缺失（meson/ninja 或特定编译器）"
            "跳过，而不是假装通过。"
        ),
    ]

    # ---------------------------------------------------- 七、分工与评分
    story += [
        P("七、个人分工与组内评分", "h1"),
        P(
            "本作业<b>个人独立完成</b>（1 人，未组队）。按课程要求说明分工："
            "从选题、资料检索、方案设计、代码实现、测试、实测实验、文档撰写到参赛材料制作，"
            "全部由本人完成；外部资源仅使用公开文档与开源项目源码，未使用他人未公开材料。"
        ),
        SP(2),
        TABLE(
            ["分工项", "内容", "占比"],
            [
                ["选题与调研", "构建成本工具调研（ClangBuildAnalyzer / IWYU / include-cleaner 等）、"
                 "确定「反事实」这一差异点", "10%"],
                ["方案与实现", "四层架构、15 个子命令、编译器驱动与解析器、"
                 "三态决策与三档证据、门禁与追溯", "45%"],
                ["实验与验证", "四个真实语料扫描、单条与完整构建对照、竞品同任务对照、"
                 "失败与否定结论归档", "25%"],
                ["测试与工程化", "794 条单元测试、零依赖守护、CMake/Meson/VSCode 接入、CI 三工作流", "12%"],
                ["文档与参赛材料", "作品介绍文档（35 页）、选题报告、演示视频、本大作业文档", "8%"],
            ],
            widths=[78, 288, 74],
        ),
        P("表 3　个人分工（1 人，全部工作由本人承担）。", "caption"),
        SP(2),
        TABLE(
            ["组长姓名", "主要职责", "组内评分"],
            [["王子周（组长，1 人组）",
              "全部：选题、方案、实现、测试、实验、文档、参赛材料", "—（个人作业）"]],
            widths=[118, 254, 68],
        ),
        P("表 4　按课程要求的分工表。个人作业无组内互评，故评分栏不填。", "caption"),
    ]

    # ------------------------------------------------------------ 八、心得
    story += [
        P("八、心得体会", "h1"),
        P(
            "<b>差分测试的思路可以迁移。</b>课程里用「同一程序在两个内核上跑、对比输出」"
            "定位内核语义差异；本项目做的是同一件事的内核外版本：同一条 include 在两份"
            "代码上跑（删与不删），对比编译器输出。区别是这里没有「标准答案」，"
            "所以必须自己造真值——汇编指纹、解析文件数、断言计数，都是为了让"
            "「有没有变」有确定性答案，而不是靠感觉。"
        ),
        P(
            "<b>证据链比数字好看更重要。</b>项目里最花时间的不是让工具算出收益，"
            "而是让每个数字都能被追到出处：为什么是 −46.6% 而不是别的、"
            "这条候选为什么判「保留」、这个门禁为什么可信。写进文档的每个数字都附复现入口，"
            "不利数据也留在正文而不是附注里。"
        ),
        P(
            "<b>否认自己是很容易的事。</b>项目最初的说法是「帮你找到可验证的优化」，"
            "实测结果把这个说法推翻了（16 条候选只有 1 条真有效）。后来改成"
            "「把『看起来能省』变成『确认能省 / 确认不能省』」——口径变窄了，"
            "但每一句都能被验证。这是这门课之外我最想记住的一条。"
        ),
    ]

    # -------------------------------------------------------- 九、参考资料
    story += [
        P("九、参考资料", "h1"),
        P(
            "1. ClangBuildAnalyzer：https://github.com/aras-p/ClangBuildAnalyzer<br/>"
            "2. include-what-you-use：https://github.com/include-what-you-use/include-what-you-use<br/>"
            "3. Clang include-cleaner：https://clang.llvm.org/extra/clang-include-cleaner/<br/>"
            "4. Aras Pranckevičius，<i>The Blowup Factor</i>（头文件成本度量口径来源）<br/>"
            "5. Bitsquid，<i>Header Hero</i>（首个体量化头文件成本的工具）<br/>"
            "6. GCC 手册：-H / -ftime-report；Clang 手册：-ftime-trace；MSVC：/showIncludes<br/>"
            "7. 本项目仓库内文档：docs/benchmark-report.md（全部实测）、"
            "docs/methodology.md（指标定义与局限）、docs/innovation.md（创新点与不声称清单）、"
            "docs/competitor-matrix.md（竞品逐项对照）"
        ),
        SP(10),
        P(
            "本文所有数字均可在仓库中复现：<font face='{0}'>benchmarks/results/</font> 与 "
            "<font face='{0}'>experiments/results/</font> 保存了每一轮的原始记录。".format(_MONO, _MONO),
            "quote",
        ),
    ]

    doc.build(story)
    print(f"written: {path}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "大作业-竞赛项目-HeavyHeader.pdf")
