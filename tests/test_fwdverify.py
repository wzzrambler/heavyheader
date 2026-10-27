"""Tests for the forward-declaration *measurement* path (``attrib/fwdverify.py``).

Most of these use a fake driver: the property under test is the测量协议 ——
which status a given pair of compiles produces, whether the assembly gate is
allowed to upgrade a result, and whether a candidate that cannot be safely
spelled is reported instead of guessed.  Whether ``g++`` accepts a particular
forward declaration is the compiler's business, and there is one real-compiler
test at the bottom for that.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from heavyheader.attrib import fwdverify
from heavyheader.attrib.advice import TIME_NOISE_FLOOR
from heavyheader.attrib.forward import (
    ForwardCandidate,
    find_forward_declaration_candidates,
    type_declarations,
)
from heavyheader.attrib.fwdverify import (
    STATUS_AMBIGUOUS,
    STATUS_CODEGEN_DIFFERS,
    STATUS_COMPILE_ERROR,
    STATUS_COMPILES_ONLY,
    STATUS_EQUIVALENT,
    STATUS_NEEDS_SPLIT,
    STATUS_VERIFIED,
    plan_patch,
    render_declaration,
    run_forward_trial,
)
from heavyheader.codegen import Fingerprint
from heavyheader.drivers.base import CompilerDriver, DriverOptions
from heavyheader.models import CompilerFamily, ProjectScan, TranslationUnit

HEADER_TEXT = """\
#pragma once
namespace demo {
class Handle {
 public:
  int value() const { return 1; }
};
}
"""

TU_TEXT = """\
#include "handle.h"
int peek(demo::Handle* h) { return h ? 1 : 0; }
"""


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class FakeDriver(CompilerDriver):
    """Reports fixed times; whether a compile is the *patched* one is inferred
    from the temp directory name, because that is the only observable
    difference between the two sides.
    """

    family = CompilerFamily.GCC

    def __init__(
        self,
        baseline: float = 1.0,
        patched: float = 0.6,
        patched_error: str = "",
        baseline_error: str = "",
    ) -> None:
        self.executable = "fake-g++"
        self.baseline = baseline
        self.patched = patched
        self.patched_error = patched_error
        self.baseline_error = baseline_error
        self.calls: list[str] = []

    def build_command(self, source, options):  # pragma: no cover - unused
        return ["fake-g++", str(source)]

    def interpret(self, source, returncode, stdout, stderr, wall_seconds):
        # 本文件不走 spawn/interpret 那条路（run_verbose 直接给结果），
        # 但这个方法是抽象方法，必须实现。
        return TranslationUnit(source=source, wall_seconds=wall_seconds)  # pragma: no cover

    def run_verbose(self, source, options, cwd=None, timeout=300.0):
        patched = source.parent.name.startswith("heavyheader-fwd-")
        self.calls.append("patched" if patched else "baseline")
        if patched and self.patched_error:
            return TranslationUnit(source=source, error=self.patched_error), ""
        if not patched and self.baseline_error:
            return TranslationUnit(source=source, error=self.baseline_error), ""
        return (
            TranslationUnit(
                source=source,
                wall_seconds=self.patched if patched else self.baseline,
            ),
            "",
        )


class FakeCodegen:
    """Stands in for ``heavyheader.codegen`` so the gate can be scripted."""

    def __init__(self, supported: bool = True, same: bool = True, ok: bool = True) -> None:
        self._supported = supported
        self._same = same
        self._ok = ok

    def supported(self, driver) -> bool:
        return self._supported

    def compare(self, driver, original, patched, options, cwd, workdir, timeout=300.0):
        before = Fingerprint(ok=self._ok, digest="a" if self._ok else None)
        after = Fingerprint(
            ok=self._ok, digest=("a" if self._same else "b") if self._ok else None
        )
        return before, after


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "include").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "include" / "handle.h").write_text(HEADER_TEXT, encoding="utf-8")
    (root / "src" / "main.cpp").write_text(TU_TEXT, encoding="utf-8")
    return root


def _scan(root: Path) -> ProjectScan:
    tu = TranslationUnit(
        source=root / "src" / "main.cpp",
        include_events=[],
    )
    # 空选项：本模块只用它来复现编译命令，假驱动不看内容。
    tu.compile_options = DriverOptions(standard="c++17", include_dirs=[root / "include"])
    tu.compile_cwd = None
    return ProjectScan(
        root=root,
        compiler="fake-g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
    )


def _candidate(root: Path, **kwargs) -> ForwardCandidate:
    values = {
        "tu": root / "src" / "main.cpp",
        "header": root / "include" / "handle.h",
        "forwardable_types": ["Handle"],
        "blocking_types": [],
        "evidence_lines": [2],
        "header_decl_lines": [3],
    }
    values.update(kwargs)
    return ForwardCandidate(**values)


def _patch_codegen(monkeypatch, fake: FakeCodegen) -> None:
    monkeypatch.setattr(fwdverify, "codegen", fake)


# --------------------------------------------------------------------------
# 声明解析与补丁生成（不需要编译器）
# --------------------------------------------------------------------------


def test_type_declarations_namespace_and_template(tmp_path: Path) -> None:
    """命名空间必须被记下来——漏了它，前向声明指向的就不是同一个类型。"""
    header = tmp_path / "mix.h"
    header.write_text(
        "#pragma once\n"
        "namespace a { namespace b { class Deep; } }\n"
        "namespace c::d { struct Modern { int x; }; }\n"
        "template <typename T> class Tmpl { };\n"
        "class Top;\n",
        encoding="utf-8",
    )
    found = {d.name: d for d in type_declarations(header)}
    assert found["Deep"].namespaces == ("a", "b")
    # ``namespace c::d`` 是 C++17 语法，前向声明里必须展开成嵌套形式才通用。
    assert found["Modern"].namespaces == ("c", "d")
    assert found["Tmpl"].template_params == "typename T"
    assert found["Top"].namespaces == ()


def test_type_declarations_ignores_function_local_class(tmp_path: Path) -> None:
    """函数体内的局部类不能从外部前向声明，提了也没人能采纳。"""
    header = tmp_path / "local.h"
    header.write_text(
        "#pragma once\n"
        "class Outside { };\n"
        "inline void f() { class Inner { }; }\n",
        encoding="utf-8",
    )
    assert [d.name for d in type_declarations(header)] == ["Outside"]


def test_type_declarations_marks_anonymous_namespace(tmp_path: Path) -> None:
    header = tmp_path / "anon.h"
    header.write_text(
        "#pragma once\nnamespace { class Hidden { }; }\n", encoding="utf-8"
    )
    (declaration,) = type_declarations(header)
    assert declaration.internal_linkage


def test_render_declaration_nests_namespaces(tmp_path: Path) -> None:
    header = tmp_path / "deep.h"
    header.write_text("#pragma once\nnamespace a { namespace b { class X; } }\n", "utf-8")
    (declaration,) = type_declarations(header)
    assert render_declaration(declaration) == "namespace a { namespace b { class X; } }"


def test_plan_patch_comments_include_and_inserts_declaration(tmp_path: Path) -> None:
    header = tmp_path / "h.h"
    header.write_text(HEADER_TEXT, encoding="utf-8")
    (declaration,) = type_declarations(header)
    patched = plan_patch(TU_TEXT, 1, [declaration])
    lines = patched.splitlines()
    assert lines[0].startswith("// [heavyheader] #include")
    # 声明必须留在原 include 的位置：挪到文件头会改变它在 #ifdef 下的可见性。
    assert lines[1] == "namespace demo { class Handle; }"
    assert "#include" not in patched.replace("// [heavyheader] #include", "")


# --------------------------------------------------------------------------
# 状态判定（假驱动 + 可编排的汇编闸门）
# --------------------------------------------------------------------------


def test_verified_when_codegen_matches_and_saving_exceeds_noise(
    project: Path, monkeypatch
) -> None:
    _patch_codegen(monkeypatch, FakeCodegen(same=True))
    scan = _scan(project)
    trial = run_forward_trial(
        scan,
        driver=FakeDriver(baseline=1.0, patched=0.6),
        candidates=[_candidate(project)],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_VERIFIED
    assert unit.saved_seconds == pytest.approx(0.4)
    assert trial.equivalent_saved_percent == pytest.approx(40.0)


def test_equivalent_when_saving_is_inside_noise_floor(project: Path, monkeypatch) -> None:
    """省了几毫秒不是收益——报成 verified 会让「20ms 噪声」变成一条建议。"""
    _patch_codegen(monkeypatch, FakeCodegen(same=True))
    tiny = TIME_NOISE_FLOOR / 4
    trial = run_forward_trial(
        _scan(project),
        driver=FakeDriver(baseline=1.0, patched=1.0 - tiny),
        candidates=[_candidate(project)],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_EQUIVALENT
    assert trial.equivalent_units == 1
    assert trial.verified_units == 0


def test_compiles_only_when_codegen_unsupported(project: Path, monkeypatch) -> None:
    """闸门不可用 ≠ 等价。结论必须停在「仅编译通过」。"""
    _patch_codegen(monkeypatch, FakeCodegen(supported=False))
    trial = run_forward_trial(
        _scan(project),
        driver=FakeDriver(baseline=1.0, patched=0.5),
        candidates=[_candidate(project)],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_COMPILES_ONLY
    assert trial.codegen_requested is True
    assert trial.codegen_checked is False
    assert trial.equivalent_units == 0
    assert trial.compiles_only_saved_seconds == pytest.approx(0.5)


def test_no_codegen_check_is_not_reported_as_toolchain_limitation(
    project: Path, monkeypatch
) -> None:
    _patch_codegen(monkeypatch, FakeCodegen(supported=True))
    trial = run_forward_trial(
        _scan(project),
        driver=FakeDriver(baseline=1.0, patched=0.5),
        candidates=[_candidate(project)],
        runs=1,
        check_codegen=False,
    )
    assert trial.codegen_checked is False
    assert trial.codegen_requested is False
    assert any("--no-codegen-check" in note for note in trial.notes)


def test_codegen_difference_blocks_adoption(project: Path, monkeypatch) -> None:
    """编译通过但汇编不同 = 这次改动改变了代码生成，不可采纳。"""
    _patch_codegen(monkeypatch, FakeCodegen(same=False))
    trial = run_forward_trial(
        _scan(project),
        driver=FakeDriver(baseline=1.0, patched=0.5),
        candidates=[_candidate(project)],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_CODEGEN_DIFFERS
    assert trial.equivalent_units == 0
    assert trial.equivalent_saved_seconds == 0.0
    # 指纹不同时也不许把省下的时间算进任何收益口径。
    assert trial.compiles_only_saved_seconds == 0.0


def test_compile_error_is_a_finding_not_a_crash(project: Path, monkeypatch) -> None:
    _patch_codegen(monkeypatch, FakeCodegen())
    trial = run_forward_trial(
        _scan(project),
        driver=FakeDriver(patched_error="error: 'Handle' is incomplete"),
        candidates=[_candidate(project)],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_COMPILE_ERROR
    assert "incomplete" in unit.error
    assert trial.compile_error_units == 1


def test_baseline_failure_says_so(project: Path, monkeypatch) -> None:
    """原文件本身编不过时，不能把「没测」说成「前向声明不行」。"""
    _patch_codegen(monkeypatch, FakeCodegen())
    trial = run_forward_trial(
        _scan(project),
        driver=FakeDriver(baseline_error="error: boom"),
        candidates=[_candidate(project)],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_COMPILE_ERROR
    assert "基线" in unit.note


def test_blocking_types_are_needs_split_not_compile_error(project: Path) -> None:
    """带阻塞类型的候选整条换掉必然编不过——那是「先拆头」，不是「前向声明不成立」。"""
    trial = run_forward_trial(
        _scan(project),
        driver=FakeDriver(),
        candidates=[_candidate(project, blocking_types=["Config"])],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_NEEDS_SPLIT
    assert "Config" in unit.note
    assert trial.needs_split_units == 1
    # 没开测就不该有编译调用。
    assert trial.measured_units == 0


def test_ambiguous_include_is_not_tested(project: Path) -> None:
    (project / "src" / "main.cpp").write_text(
        '#include "handle.h"\n#include "handle.h"\nint peek(demo::Handle* h);\n',
        encoding="utf-8",
    )
    trial = run_forward_trial(
        _scan(project), driver=FakeDriver(), candidates=[_candidate(project)], runs=1
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_AMBIGUOUS
    assert trial.measured_units == 0


def test_no_candidates_returns_actionable_note(project: Path) -> None:
    trial = run_forward_trial(
        _scan(project), driver=FakeDriver(), candidates=[], runs=1
    )
    assert trial.trials == []
    assert trial.notes


def test_max_trials_limits_measured_but_reports_considered(
    project: Path, monkeypatch
) -> None:
    _patch_codegen(monkeypatch, FakeCodegen())
    candidates = [_candidate(project) for _ in range(3)]
    trial = run_forward_trial(
        _scan(project), driver=FakeDriver(), candidates=candidates, runs=1, max_trials=1
    )
    assert trial.considered == 3
    assert trial.measured_units == 1
    assert any("--max-trials" in note for note in trial.notes)


# --------------------------------------------------------------------------
# 真编译器
# --------------------------------------------------------------------------


GPP = shutil.which("g++")


@pytest.mark.skipif(GPP is None, reason="没有 g++，无法做真实编译")
def test_real_gpp_replaces_include_and_still_compiles(tmp_path: Path) -> None:
    root = tmp_path / "real"
    (root / "include").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "include" / "handle.h").write_text(HEADER_TEXT, encoding="utf-8")
    (root / "src" / "main.cpp").write_text(TU_TEXT, encoding="utf-8")

    tu = TranslationUnit(source=root / "src" / "main.cpp")
    tu.compile_options = DriverOptions(
        standard="c++17", include_dirs=[root / "include"]
    )
    tu.compile_cwd = None
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
        header_stats={},
    )

    trial = run_forward_trial(scan, candidates=[_candidate(root)], runs=1)
    (unit,) = trial.trials
    assert unit.status in (STATUS_VERIFIED, STATUS_EQUIVALENT), unit.error
    assert unit.declarations == "namespace demo { class Handle; }"
    # 实验必须是可逆的：真源码一个字节都不该动。
    assert (root / "src" / "main.cpp").read_text(encoding="utf-8") == TU_TEXT


@pytest.mark.skipif(GPP is None, reason="没有 g++，无法做真实编译")
def test_real_gpp_reports_incomplete_type(tmp_path: Path) -> None:
    """编译不过时，错误必须来自编译器，而不是我们编不出来。"""
    root = tmp_path / "real2"
    (root / "include").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "include" / "handle.h").write_text(HEADER_TEXT, encoding="utf-8")
    tu_path = root / "src" / "main.cpp"
    tu_path.write_text(
        '#include "handle.h"\nint size_of() { return sizeof(demo::Handle); }\n',
        encoding="utf-8",
    )
    tu = TranslationUnit(source=tu_path)
    tu.compile_options = DriverOptions(
        standard="c++17", include_dirs=[root / "include"]
    )
    tu.compile_cwd = None
    scan = ProjectScan(
        root=root,
        compiler="g++",
        compiler_family=CompilerFamily.GCC,
        standard="c++17",
        tus=[tu],
        header_stats={},
    )

    trial = run_forward_trial(
        scan,
        candidates=[_candidate(root, forwardable_types=["Handle"])],
        runs=1,
    )
    (unit,) = trial.trials
    assert unit.status == STATUS_COMPILE_ERROR
    assert "incomplete" in unit.error


@pytest.mark.skipif(GPP is None, reason="没有 g++，无法做真实编译")
def test_real_gpp_pipeline_finds_its_own_candidate(tmp_path: Path) -> None:
    """走一遍「扫描找候选 → 实测」的完整链路，确认两边口径接得上。"""
    from heavyheader.drivers.base import detect_driver
    from heavyheader.scanner import ScanOptions, scan_project

    root = tmp_path / "real3"
    (root / "include").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "include" / "handle.h").write_text(HEADER_TEXT, encoding="utf-8")
    (root / "src" / "main.cpp").write_text(TU_TEXT, encoding="utf-8")

    scan = scan_project(
        ScanOptions(root=root, include_dirs=[root / "include"], forward_decl=True),
        driver=detect_driver("g++"),
    )
    candidates = find_forward_declaration_candidates(scan)
    assert [c.header.name for c in candidates] == ["handle.h"]

    trial = run_forward_trial(scan, driver=detect_driver("g++"), runs=1)
    (unit,) = trial.trials
    assert unit.status in (STATUS_VERIFIED, STATUS_EQUIVALENT), unit.error
