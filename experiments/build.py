"""The one build driver used by every arm of the experiment.

Why this file exists instead of "just call CMake":

* The machine used for the experiment has no CMake, make or ninja.  Shipping a
  protocol that cannot be executed on the machine that produced the numbers is
  not reproducible, so the build is driven directly.
* Every flag below is a **literal copy** of what the project's own
  ``tests/CMakeLists.txt`` passes (source list, include dirs, compile
  definitions, per-target exceptions).  The mapping is documented in
  ``experiments/README.md``; if you doubt a flag, you can check it against
  upstream in one diff.
* A CMake build would also *help us* in a way the project never gets: CMake
  would regenerate the whole graph.  Driving the commands ourselves keeps both
  arms byte-identical except for the one variable under test.

Deliberate choices, each with a cost:

* ``-O0``: the upstream test suite is built without optimisation in CI; at
  ``-O2`` the optimiser dominates the wall clock and hides header cost.
* ``--jobs 8`` fixed, not "all cores": on a 20-core box the tail of a
  63-unit build is all waiting on stragglers, and the measured wall time
  becomes a scheduling artefact.  A fixed, documented parallelism is worse
  hardware-wise but better as an experiment.
* ``g++`` only, no matrices.  The three-compiler comparison already exists in
  ``benchmarks/results/toolchains-demo.md``; mixing compilers here would
  multiply the runs without changing the conclusion this experiment tests.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "CXX",
    "BuildResult",
    "TestResult",
    "build_and_run_one",
    "clean_build",
    "discover_tus",
    "pch_incompatible_tus",
    "run_tests",
    "write_compile_commands",
    "write_generated_headers",
]

CXX = os.environ.get("HH_CXX", "g++")

#: Copied from tests/CMakeLists.txt: target_compile_definitions(test_main …)
#: plus the C++ standard the whole matrix is built with.
BASE_FLAGS = [
    "-std=c++11",
    "-O0",
    "-DDOCTEST_CONFIG_SUPER_FAST_ASSERTS",
    "-DJSON_TEST_KEEP_MACROS",
    "-DJSON_TEST_USING_MULTIPLE_HEADERS=0",
]

#: ``tests/thirdparty/fifo_map`` is in upstream's target_include_directories.
INCLUDE_DIRS = [
    "include",
    "tests",
    "tests/thirdparty/doctest",
    "tests/thirdparty/fifo_map",
    "tests/src",
]

#: The shared ``main`` for every test binary (upstream: add_library(test_main)).
MAIN_TU = "tests/src/unit.cpp"

#: Excluded on 64-bit hosts by upstream's own filter.
EXCLUDED_TUS = {"unit-32bit"}

#: ``#define JSON_TESTS_PRIVATE`` re-opens nlohmann's private section; other
#: units define ``JSON_DIAGNOSTICS`` or override ``JSON_ASSERT``.  All of them
#: change what ``json.hpp`` *means*, so they cannot share one precompiled
#: copy of it -- a PCH captures the header's preprocessed state, including the
#: configuration macros.  Detected structurally (any ``JSON*`` define before
#: the include) rather than by a hard-coded file list, because a list would go
#: stale the moment upstream adds a unit.
LIBRARY_INCLUDE = "nlohmann/json.hpp"
_CONFIG_MACRO_RE = re.compile(r"^\s*#\s*define\s+(JSON\w*)", re.MULTILINE)


@dataclass(slots=True)
class BuildResult:
    arm: str
    ok: bool
    wall_seconds: float
    pch_seconds: float
    compile_seconds: dict[str, float]
    link_seconds: dict[str, float]
    failures: list[str] = field(default_factory=list)
    #: Units compiled *without* the PCH because of the private-macro conflict.
    pch_excluded: list[str] = field(default_factory=list)
    #: Diagnostics mentioning a PCH: g++ silently ignores a mismatched .gch,
    #: which would make the "optimised" arm look fast for the wrong reason.
    pch_diagnostics: list[str] = field(default_factory=list)

    @property
    def compile_cpu_seconds(self) -> float:
        return sum(self.compile_seconds.values())

    @property
    def total_cpu_seconds(self) -> float:
        return self.compile_cpu_seconds + sum(self.link_seconds.values()) + self.pch_seconds


@dataclass(slots=True)
class TestResult:
    ok: bool
    wall_seconds: float
    #: binary name -> (passed, failed, return code)
    suites: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    crashed: list[str] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(v[0] for v in self.suites.values())

    @property
    def failed(self) -> int:
        return sum(v[1] for v in self.suites.values())


# --------------------------------------------------------------------------
# source discovery
# --------------------------------------------------------------------------


def discover_tus(root: Path) -> list[str]:
    """The test units upstream globs: ``src/unit-*.cpp`` minus 32-bit."""
    names = sorted(p.stem for p in (root / "tests" / "src").glob("unit-*.cpp"))
    return [n for n in names if n not in EXCLUDED_TUS]


def pch_incompatible_tus(root: Path) -> dict[str, str]:
    """Units that configure the library before including it.

    Returns ``{unit: macro}``.  A precompiled ``json.hpp`` bakes in whatever
    macros were set when it was built, so a unit that flips one of them would
    silently get the *other* configuration -- which is exactly how the first
    version of this experiment ended up with 15 failing assertions and a
    build that looked faster for the wrong reason.
    """
    blocked: dict[str, str] = {}
    for name in discover_tus(root):
        text = (root / "tests" / "src" / f"{name}.cpp").read_text(
            encoding="utf-8", errors="replace"
        )
        head = text.split(LIBRARY_INCLUDE, 1)[0]
        match = _CONFIG_MACRO_RE.search(head)
        if match:
            blocked[name] = match.group(1)
    return blocked


def test_data_dir() -> Path:
    """Where ``fetch.py`` put the upstream corpus."""
    return Path(__file__).resolve().parent / "_work" / "json_test_data"


def write_generated_headers(root: Path, build_dir: Path, test_data: Path) -> Path:
    """Recreate the one header upstream's CMake *generates*.

    ``tests/test_data.hpp`` does not exist in the tarball: CMake writes it with
    the absolute path of the downloaded corpus.  Building without it fails on
    every unit that touches the corpus, so it is generated here with the same
    content.
    """
    gen = build_dir / "gen" / "include"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "test_data.hpp").write_text(
        f'#define TEST_DATA_DIRECTORY "{test_data.as_posix()}"\n', encoding="utf-8"
    )
    return gen


# --------------------------------------------------------------------------
# precompiled header
# --------------------------------------------------------------------------

PCH_NAME = "hh_prefix.h"

PCH_SOURCE = """\
// Generated by experiments/build.py -- do not edit.
//
// The three defines mirror tests/CMakeLists.txt; they must match the TU
// compiles exactly, otherwise g++ ignores the .gch *silently* and the
// "faster" arm is faster for no reason at all.
#define DOCTEST_CONFIG_SUPER_FAST_ASSERTS
#define JSON_TEST_KEEP_MACROS
#define JSON_TEST_USING_MULTIPLE_HEADERS 0
#include "doctest_compatibility.h"
#include <nlohmann/json.hpp>
"""


def _build_pch(root: Path, build_dir: Path, gen: Path) -> tuple[float, Path, list[str]]:
    pch_dir = build_dir / "pch"
    pch_dir.mkdir(parents=True, exist_ok=True)
    header = pch_dir / PCH_NAME
    header.write_text(PCH_SOURCE, encoding="utf-8")
    cmd = [CXX, *BASE_FLAGS, *_include_flags(root, gen, pch_dir)]
    cmd += ["-x", "c++-header", str(header), "-o", str(header) + ".gch"]
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - started
    if proc.returncode != 0:
        raise SystemExit(f"[build] PCH generation failed:\n{proc.stderr[:2000]}")
    diagnostics = [line for line in proc.stderr.splitlines() if "pch" in line.lower()]
    return elapsed, pch_dir, diagnostics


# --------------------------------------------------------------------------
# command construction
# --------------------------------------------------------------------------


def _include_flags(root: Path, gen: Path, pch_dir: Path | None = None) -> list[str]:
    """All ``-I`` for one compile, in the order upstream plus the generated dir.

    ``pch_dir`` is appended last on purpose: the .gch must be reachable by the
    same ``-include`` name the TUs use, and putting it last keeps every
    project include resolving to the real source tree.
    """
    args: list[str] = []
    for directory in INCLUDE_DIRS:
        args += ["-I", str(root / directory)]
    args += ["-I", str(gen)]
    if pch_dir is not None:
        args += ["-I", str(pch_dir)]
    return args


def _compile_cmd(
    source: Path, obj: Path, root: Path, gen: Path, pch_dir: Path | None
) -> list[str]:
    cmd = [CXX, *BASE_FLAGS, *_include_flags(root, gen, pch_dir)]
    if pch_dir is not None:
        # -Winvalid-pch turns "silently ignored" into a diagnostic we can see.
        cmd += ["-include", PCH_NAME, "-Winvalid-pch"]
    cmd += ["-c", str(source), "-o", str(obj)]
    return cmd


# --------------------------------------------------------------------------
# the build itself
# --------------------------------------------------------------------------


def clean_build(
    root: Path,
    build_dir: Path,
    *,
    arm: str,
    jobs: int = 8,
    pch: bool = False,
    keep: bool = False,
) -> BuildResult:
    """Compile every test unit, link every test binary, from a clean tree."""
    if build_dir.exists() and not keep:
        shutil.rmtree(build_dir)
    obj_dir = build_dir / "obj"
    bin_dir = build_dir / "bin"
    obj_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)

    gen = write_generated_headers(root, build_dir, test_data_dir())

    excluded = set(pch_incompatible_tus(root)) if pch else set()
    pch_seconds = 0.0
    pch_dir: Path | None = None
    pch_diagnostics: list[str] = []
    if pch:
        pch_seconds, pch_dir, pch_diagnostics = _build_pch(root, build_dir, gen)

    units = discover_tus(root)
    results: BuildResult = BuildResult(
        arm=arm,
        ok=True,
        wall_seconds=0.0,
        pch_seconds=pch_seconds,
        compile_seconds={},
        link_seconds={},
        pch_excluded=sorted(excluded),
        pch_diagnostics=pch_diagnostics,
    )

    started = time.perf_counter()

    def compile_unit(name: str) -> tuple[str, float, str | None]:
        source = root / "tests" / "src" / f"{name}.cpp"
        obj = obj_dir / f"{name}.o"
        use_pch = pch_dir is not None and name not in excluded
        begin = time.perf_counter()
        proc = subprocess.run(
            _compile_cmd(source, obj, root, gen, pch_dir if use_pch else None),
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - begin
        if proc.returncode != 0:
            return name, elapsed, proc.stderr[-1500:]
        if use_pch:
            for line in proc.stderr.splitlines():
                if "valid pch" in line.lower() or "pch" in line.lower():
                    results.pch_diagnostics.append(f"{name}: {line.strip()}")
        return name, elapsed, None

    def compile_main(_: str) -> tuple[str, float, str | None]:
        begin = time.perf_counter()
        proc = subprocess.run(
            _compile_cmd(root / MAIN_TU, obj_dir / "unit.o", root, gen, None),
            capture_output=True,
            text=True,
        )
        elapsed = time.perf_counter() - begin
        return "__main__", elapsed, None if proc.returncode == 0 else proc.stderr[-1500:]

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(compile_main, "unit")]
        futures += [pool.submit(compile_unit, name) for name in units]
        for future in futures:
            name, elapsed, error = future.result()
            results.compile_seconds[name] = elapsed
            if error is not None:
                results.ok = False
                results.failures.append(f"{name}: {error}")

    if results.ok:
        def link(name: str) -> tuple[str, float, str | None]:
            out = bin_dir / f"{name}.exe"
            begin = time.perf_counter()
            proc = subprocess.run(
                [CXX, str(obj_dir / "unit.o"), str(obj_dir / f"{name}.o"), "-o", str(out)],
                capture_output=True,
                text=True,
            )
            elapsed = time.perf_counter() - begin
            return name, elapsed, None if proc.returncode == 0 else proc.stderr[-1500:]

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(link, name) for name in units]
            for future in futures:
                name, elapsed, error = future.result()
                results.link_seconds[name] = elapsed
                if error is not None:
                    results.ok = False
                    results.failures.append(f"link {name}: {error}")

    results.wall_seconds = time.perf_counter() - started
    # Marker so a later step can tell which arm the artifacts on disk belong to
    # without guessing from timestamps.
    (build_dir / ".arm").write_text(arm, encoding="utf-8")
    return results


# --------------------------------------------------------------------------
# the correctness gate
# --------------------------------------------------------------------------

_ASSERT_RE = r"assertions:\s*(\d+)\s*\|\s*(\d+)\s+passed\s*\|\s*(\d+)\s+failed"


def run_tests(build_dir: Path, *, jobs: int = 8, timeout: float = 900.0) -> TestResult:
    """Run every test binary; capture per-suite assertion counts.

    The assertion counts matter more than the exit code: a suite that already
    failed before the change (one does, on this compiler) must fail with the
    *same* count afterwards, otherwise the build was not merely faster but
    different.
    """
    import re

    pattern = re.compile(_ASSERT_RE)
    binaries = sorted((build_dir / "bin").glob("*.exe"))
    result = TestResult(ok=True, wall_seconds=0.0)
    started = time.perf_counter()

    def run_one(path: Path) -> tuple[str, int, int, int, bool]:
        try:
            proc = subprocess.run(
                [str(path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                # 在 bin/ 里跑，不继承调用者的工作目录：上游有些单元会把临时
                # 文件写到 cwd，从仓库根运行就会在仓库里留下 test.json 之类的
                # 垃圾，把后续 git status 弄脏。
                cwd=str(path.parent),
            )
        except subprocess.TimeoutExpired:
            return path.stem, 0, 0, -1, True
        match = pattern.search(proc.stdout) or pattern.search(proc.stderr)
        passed, failed = (int(match.group(2)), int(match.group(3))) if match else (0, 0)
        return path.stem, passed, failed, proc.returncode, False

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for name, passed, failed, code, crashed in pool.map(run_one, binaries):
            result.suites[name] = (passed, failed, code)
            if crashed:
                result.crashed.append(name)
                result.ok = False

    result.wall_seconds = time.perf_counter() - started
    return result


# --------------------------------------------------------------------------
# compile_commands.json (for HeavyHeader's own scan)
# --------------------------------------------------------------------------


def write_compile_commands(root: Path, out: Path, *, with_pch: bool = False) -> Path:
    """Emit the exact commands so the scan sees the same world the build does."""
    build_dir = out.parent / "compile-commands-build"
    gen = write_generated_headers(root, build_dir, test_data_dir())
    entries = []
    pch_dir = build_dir / "pch" if with_pch else None
    for name in discover_tus(root):
        cmd = _compile_cmd(
            root / "tests" / "src" / f"{name}.cpp",
            build_dir / f"{name}.o",
            root,
            gen,
            pch_dir if with_pch and name not in pch_incompatible_tus(root) else None,
        )
        entries.append(
            {
                "directory": str(root),
                "file": str(root / "tests" / "src" / f"{name}.cpp"),
                "command": " ".join(_quote(part) for part in cmd),
            }
        )
    out.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return out


def _quote(text: str) -> str:
    return f'"{text}"' if " " in text else text


def build_and_run_one(
    source: Path, work_dir: Path, root: Path
) -> tuple[int, int, str | None]:
    """Compile ``source`` + the shared main, link, run, count assertions.

    This is the unit-test half of the evidence grade: ``verify`` can prove a
    unit still *compiles* without an include, and this proves its *tests*
    still pass.  The two are different claims, and only the second one is
    what a reviewer means by "safe to merge".

    ``source`` may live anywhere (``verify`` hands us a patched copy in a
    temp directory) -- the include flags are absolute, so the copy compiles
    exactly like the original.  Returns ``(passed, failed, error)``; ``error``
    is a string when compilation, linking or the run itself failed, which is
    **not** the same as "tests failed" and must not be recorded as such.
    """
    gen = write_generated_headers(root, work_dir / "gen", test_data_dir())
    obj_dir = work_dir / "obj"
    obj_dir.mkdir(parents=True, exist_ok=True)
    unit_obj = obj_dir / "unit.o"
    main_obj = obj_dir / "main.o"
    exe = work_dir / "bin" / f"{source.stem}.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)

    for target_source, target_obj in (
        (root / MAIN_TU, main_obj),
        (source, unit_obj),
    ):
        proc = subprocess.run(
            _compile_cmd(target_source, target_obj, root, gen, None),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return 0, 0, f"compile failed: {proc.stderr.strip().splitlines()[-1][:160]}"

    link = subprocess.run(
        [CXX, str(main_obj), str(unit_obj), "-o", str(exe)],
        capture_output=True,
        text=True,
    )
    if link.returncode != 0:
        return 0, 0, f"link failed: {link.stderr.strip().splitlines()[-1][:160]}"

    try:
        run = subprocess.run(
            [str(exe)],
            capture_output=True,
            text=True,
            timeout=900.0,
            # 在二进制自己的目录里跑：上游有些单元会往 cwd 写临时文件，
            # 从仓库根跑会把仓库弄脏。
            cwd=str(exe.parent),
        )
    except subprocess.TimeoutExpired:
        return 0, 0, "test binary timed out"

    import re as _re

    match = _re.compile(_ASSERT_RE).search(run.stdout) or _re.compile(_ASSERT_RE).search(
        run.stderr
    )
    if match is None:
        return 0, 0, f"no assertion summary in output (exit {run.returncode})"
    passed, failed = int(match.group(2)), int(match.group(3))
    if run.returncode != 0:
        return passed, failed, f"test binary exited {run.returncode}"
    return passed, failed, None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build one arm of the experiment")
    parser.add_argument("--root", type=Path, default=Path(__file__).parent / "_work" / "json")
    parser.add_argument("--build-dir", type=Path, default=Path(__file__).parent / "_work" / "build")
    parser.add_argument("--arm", default="baseline")
    parser.add_argument("--pch", action="store_true")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    build = clean_build(
        args.root, args.build_dir, arm=args.arm, jobs=args.jobs, pch=args.pch
    )
    tests = run_tests(args.build_dir, jobs=args.jobs)
    print(
        f"{args.arm}: ok={build.ok} wall={build.wall_seconds:.2f}s "
        f"cpu={build.total_cpu_seconds:.1f}s tests={tests.passed}p/{tests.failed}f"
    )
    if not build.ok:
        for failure in build.failures[:3]:
            print(f"  FAILURE {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
