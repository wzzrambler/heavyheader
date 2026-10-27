"""失败单元占比的措辞必须跟着占比走。

起因是 2026-10-27 的一次真实事故：72 个单元里 71 个因 ``-I`` 参照系错误而失败，
当时的提示是「数字可能偏低」。71/72 不是偏低——这份报告不能拿来做决策。
措辞若不分档，就是把一次「报告不可用」说成一次「报告略有遗漏」。
"""

from pathlib import Path

from heavyheader.models import CompilerFamily, ProjectScan, TranslationUnit
from heavyheader.report.markdown import compile_health_note, render_markdown

_FAMILY = CompilerFamily.GCC


def _scan(ok: int, failed: int) -> ProjectScan:
    tus: list[TranslationUnit] = []
    for i in range(ok):
        tus.append(TranslationUnit(source=Path(f"ok{i}.cpp"), dependencies=[]))
    for i in range(failed):
        tus.append(
            TranslationUnit(
                source=Path(f"bad{i}.cpp"),
                dependencies=[],
                error="fatal error: nlohmann/json.hpp: No such file or directory",
            )
        )
    return ProjectScan(
        root=Path("."),
        compiler="g++",
        compiler_family=_FAMILY,
        standard="c++17",
        tus=tus,
    )


def test_all_units_ok_produces_no_note():
    assert compile_health_note(_scan(ok=5, failed=0)) is None


def test_empty_scan_produces_no_note():
    """没有单元时不该产出「0/0 失败」这种话。"""
    assert compile_health_note(_scan(ok=0, failed=0)) is None


def test_few_failures_say_numbers_may_be_low():
    note = compile_health_note(_scan(ok=9, failed=1))
    assert note is not None
    assert "1/10" in note
    assert "偏低" in note
    # 少量失败时不该升级成「不可用」。
    assert "不足以支撑" not in note


def test_partial_failures_say_report_covers_only_the_ok_part():
    note = compile_health_note(_scan(ok=7, failed=3))
    assert note is not None
    assert "3/10" in note
    assert "只描述成功" in note
    assert "不足以支撑" not in note


def test_majority_failures_say_report_is_unusable():
    """71/72 是本项目真实踩到的形状，这句话必须够硬。"""
    note = compile_health_note(_scan(ok=1, failed=71))
    assert note is not None
    assert "71/72" in note
    assert "99%" in note  # 98.6% 四舍五入，不是 98%
    assert "不足以支撑任何结论" in note


def test_higher_tiers_name_the_likely_cause():
    """「71 个找不到头文件」到「我的 -I 参照系错了」还差一步推理，工具该替读者走。

    少量失败档不给提示：那时失败通常是项目自身的问题，塞一份通用清单是噪音。
    """
    partial = compile_health_note(_scan(ok=7, failed=3))
    unusable = compile_health_note(_scan(ok=1, failed=71))
    assert partial is not None and unusable is not None
    for note in (partial, unusable):
        assert "进程当前目录" in note
    assert "进程当前目录" not in (compile_health_note(_scan(ok=9, failed=1)) or "")


def test_note_appears_next_to_the_unit_count_in_the_report():
    """健康提示要紧挨着「编译单元」那一行，而不是丢进文末的扫描警告。

    它决定的是「下面这些数字能不能用」，读者不该先读完报告才发现。
    """
    body = render_markdown(_scan(ok=1, failed=71))
    lines = [line for line in body.splitlines() if line.strip()]
    count_at = next(i for i, line in enumerate(lines) if line.startswith("- 编译单元："))
    assert "不足以支撑任何结论" in lines[count_at + 1]
