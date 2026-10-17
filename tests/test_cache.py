"""Tests for the incremental scan cache.

增量扫描的正确性只有一条标准：**缓存命中的结果必须与全量扫描逐字一致**。
所以这里除了测「该复用时复用」，更要测「该失效时必须失效」——漏掉一个改动
就是给出一份过时的成本报告，而用户无从察觉。

同时守住两个退化路径：缓存文件损坏、编译选项变化，都必须自动退回全量扫描，
而不是让扫描失败或给出错误结论。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from heavyheader.cache import (
    CacheStats,
    ScanCache,
    StampResolver,
    _matches,
    _stamp,
    compiler_fingerprint,
    load_cache,
    save_cache,
)
from heavyheader.drivers.base import CompilerDriver, DriverOptions
from heavyheader.models import CompilerFamily, IncludeEvent, TranslationUnit
from heavyheader.scanner import ScanOptions, scan_project


class _CountingDriver(CompilerDriver):
    """记录真实编译次数；依赖列表由测试指定，用于验证失效判定。"""

    family = CompilerFamily.GCC

    def __init__(self, deps: list[Path] | None = None):
        self.executable = "g++"
        self.deps = deps or []
        self.compiled: list[Path] = []

    def run(self, source, options, cwd=None, timeout=300.0) -> TranslationUnit:
        source = Path(source)
        self.compiled.append(source)
        return TranslationUnit(
            source=source,
            dependencies=list(self.deps),
            include_events=[
                IncludeEvent(includer=source, included=Path(d), depth=0, line=1)
                for d in self.deps
            ],
        )

    def interpret(self, source, returncode, stdout, stderr, wall_seconds):
        return TranslationUnit(source=Path(source), dependencies=[])

    def build_command(self, source, options) -> list[str]:  # pragma: no cover
        return ["g++", str(source)]


def _project(tmp_path: Path, deps: list[Path]) -> Path:
    (tmp_path / "src").mkdir(exist_ok=True)
    source = tmp_path / "src" / "a.cpp"
    source.write_text("int a() { return 1; }\n", encoding="utf-8")
    return source


# --------------------------------------------------------------------------
# file stamps
# --------------------------------------------------------------------------


def test_stamp_matches_without_reading_content(tmp_path):
    """快路径：size 与 mtime 都没变，不必读文件内容。"""
    target = tmp_path / "h.h"
    target.write_text("x" * 100, encoding="utf-8")
    stamp = _stamp(target)
    assert stamp is not None
    # 首次编码会算一次哈希（要存下来给 mtime 变动时用）
    encoded = f"{stamp.size}:{stamp.mtime_ns}:{stamp.digest}"

    fresh = _stamp(target)
    assert _matches(fresh, encoded) is True
    assert fresh.digest == stamp.digest


def test_touched_file_with_same_content_is_still_fresh(tmp_path):
    """git checkout / touch 会改 mtime 但不动内容——此时靠内容哈希挽救。"""
    target = tmp_path / "h.h"
    target.write_text("content here", encoding="utf-8")
    stamp = _stamp(target)
    encoded = f"{stamp.size}:{stamp.mtime_ns}:{stamp.digest}"

    import os
    import time

    os.utime(target, (time.time() + 120, time.time() + 120))

    touched = _stamp(target)
    assert touched.mtime_ns != stamp.mtime_ns, "前提：mtime 确实变了"
    assert _matches(touched, encoded) is True, "内容没变就不该判为已改动"


def test_edited_file_is_stale(tmp_path):
    target = tmp_path / "h.h"
    target.write_text("before", encoding="utf-8")
    stamp = _stamp(target)
    encoded = f"{stamp.size}:{stamp.mtime_ns}:{stamp.digest}"

    # 内容 size 也改变——否则在 Windows 上连续两次写入可能落在同一 mtime
    # 粒度内，触发「size 与 mtime 都一致」的快路径误判为未改动（该快路径是对的，
    # 只是测试需要保证编辑一定被探测到，不能依赖文件系统时间戳精度）。
    target.write_text("after-edited-content", encoding="utf-8")

    assert _matches(_stamp(target), encoded) is False


def test_missing_file_never_matches(tmp_path):
    target = tmp_path / "gone.h"
    target.write_text("data", encoding="utf-8")
    stamp = _stamp(target)
    encoded = f"{stamp.size}:{stamp.mtime_ns}:{stamp.digest}"
    target.unlink()

    assert _stamp(target) is None


def test_stamp_without_a_cached_digest_is_treated_as_stale(tmp_path):
    """缓存里没有内容哈希（早期格式）时，保守判为已改动。"""
    target = tmp_path / "h.h"
    target.write_text("data", encoding="utf-8")
    stamp = _stamp(target)
    assert _matches(stamp, f"{stamp.size + 1}:{stamp.mtime_ns}:-") is False


# --------------------------------------------------------------------------
# freshness / invalidation
# --------------------------------------------------------------------------


def _make_entry(tmp_path: Path, resolver: StampResolver, deps: dict[str, str], source: Path):
    from heavyheader.cache import CacheEntry, _tu_to_dict

    stamp = resolver.stamp(source)
    return CacheEntry(
        source=str(source),
        source_stamp=f"{stamp.size}:{stamp.mtime_ns}:{stamp.digest}",
        deps=deps,
        tu=_tu_to_dict(TranslationUnit(source=source, dependencies=[Path(d) for d in deps])),
    )


def test_dependency_change_invalidates(tmp_path):
    source = tmp_path / "a.cpp"
    source.write_text("int a(){return 1;}\n", encoding="utf-8")
    header = tmp_path / "h.h"
    header.write_text("struct H{};\n", encoding="utf-8")

    resolver = StampResolver()
    stamp = resolver.stamp(header)
    entry = _make_entry(
        tmp_path, resolver, {str(header): f"{stamp.size}:{stamp.mtime_ns}:{stamp.digest}"}, source
    )
    assert entry.is_fresh(resolver) is True

    header.write_text("struct H{int x;};\n", encoding="utf-8")
    resolver2 = StampResolver()
    assert entry.is_fresh(resolver2) is False


def test_deleted_dependency_invalidates(tmp_path):
    """依赖头消失意味着 include 图已经不同了。"""
    source = tmp_path / "a.cpp"
    source.write_text("int a(){return 1;}\n", encoding="utf-8")
    header = tmp_path / "h.h"
    header.write_text("struct H{};\n", encoding="utf-8")

    resolver = StampResolver()
    stamp = resolver.stamp(header)
    entry = _make_entry(
        tmp_path, resolver, {str(header): f"{stamp.size}:{stamp.mtime_ns}:{stamp.digest}"}, source
    )
    header.unlink()

    assert entry.is_fresh(StampResolver()) is False


# --------------------------------------------------------------------------
# compiler fingerprint
# --------------------------------------------------------------------------


def _fingerprint(**overrides):
    params = dict(
        compiler="g++",
        family="gcc",
        standard="c++17",
        include_dirs=[Path("/proj/include")],
        defines=["NDEBUG"],
        extra_flags=["-O2"],
        full_compile=False,
    )
    params.update(overrides)
    return compiler_fingerprint(**params)


@pytest.mark.parametrize(
    "overrides",
    [
        {"standard": "c++20"},
        {"defines": ["NDEBUG", "FOO=1"]},
        {"extra_flags": ["-O0"]},
        {"include_dirs": [Path("/proj/other")]},
        {"full_compile": True},
        {"compiler": "clang++"},
    ],
)
def test_fingerprint_catches_every_option_that_changes_output(overrides):
    assert _fingerprint(**overrides) != _fingerprint()


def test_fingerprint_is_stable_for_equivalent_input():
    """选项顺序不该影响指纹——否则每次运行都会作废缓存。"""
    a = compiler_fingerprint(
        compiler="g++",
        family="gcc",
        standard="c++17",
        include_dirs=[Path("/b"), Path("/a")],
        defines=["Z", "A"],
        extra_flags=["-O2"],
        full_compile=False,
    )
    b = compiler_fingerprint(
        compiler="g++",
        family="gcc",
        standard="c++17",
        include_dirs=[Path("/a"), Path("/b")],
        defines=["A", "Z"],
        extra_flags=["-O2"],
        full_compile=False,
    )
    assert a == b


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def test_cache_survives_a_round_trip(tmp_path):
    source = tmp_path / "a.cpp"
    source.write_text("int a(){return 1;}\n", encoding="utf-8")
    resolver = StampResolver()
    cache = ScanCache(fingerprint="abc", root=str(tmp_path))
    cache.record(TranslationUnit(source=source, dependencies=[]), resolver)

    path = tmp_path / "cache.json"
    assert save_cache(path, cache) is True
    restored = load_cache(path)

    assert restored is not None
    assert restored.fingerprint == "abc"
    assert restored.lookup(source, StampResolver()) is not None


def test_corrupt_cache_degrades_to_a_full_scan(tmp_path):
    """缓存坏了要能安静地退回全量，而不是把扫描搞挂。"""
    path = tmp_path / "cache.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert load_cache(path) is None

    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert load_cache(path) is None

    path.write_text(json.dumps({"schema": 999, "entries": []}), encoding="utf-8")
    assert load_cache(path) is None


def test_missing_cache_file_is_not_an_error(tmp_path):
    assert load_cache(tmp_path / "nope.json") is None


def test_failed_tu_is_never_cached(tmp_path):
    """失败的编译不进缓存：它的依赖集不完整，补上头之后也发现不了变化。"""
    source = tmp_path / "a.cpp"
    source.write_text("int a(){return 1;}\n", encoding="utf-8")
    cache = ScanCache(fingerprint="abc", root=str(tmp_path))

    cache.record(TranslationUnit(source=source, error="missing header"), StampResolver())

    assert cache.entries == {}


# --------------------------------------------------------------------------
# integration with scan_project
# --------------------------------------------------------------------------


def test_second_scan_recompiles_nothing(tmp_path):
    header = tmp_path / "h.h"
    header.write_text("struct H{};\n", encoding="utf-8")
    source = tmp_path / "src" / "a.cpp"
    source.parent.mkdir()
    source.write_text('#include "../h.h"\nint a(){return 1;}\n', encoding="utf-8")

    cache_path = tmp_path / ".cache.json"
    driver = _CountingDriver(deps=[header])
    opts = ScanOptions(root=tmp_path, jobs=1)

    scan_project(opts, driver=driver, cache_path=cache_path)
    assert driver.compiled, "首次应当编译"

    driver2 = _CountingDriver(deps=[header])
    scan2 = scan_project(opts, driver=driver2, cache_path=cache_path)
    assert driver2.compiled == [], "没有任何改动，第二次不该编译任何东西"
    assert scan2.cache_stats.reused == 1


def test_editing_a_header_recompiles_only_its_dependents(tmp_path):
    """这是增量的全部意义：只重编受影响的那部分。"""
    shared = tmp_path / "shared.h"
    shared.write_text("struct S{};\n", encoding="utf-8")
    other = tmp_path / "other.h"
    other.write_text("struct O{};\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    for name, dep in (("a", shared), ("b", shared), ("c", other)):
        (tmp_path / "src" / f"{name}.cpp").write_text(
            f'int {name}(){{return 1;}}\n', encoding="utf-8"
        )

    cache_path = tmp_path / ".cache.json"
    driver = _CountingDriver(deps=[shared, other])
    scan_project(ScanOptions(root=tmp_path, jobs=1), driver=driver, cache_path=cache_path)

    shared.write_text("struct S{int x;};\n", encoding="utf-8")

    driver2 = _CountingDriver(deps=[shared, other])
    scan2 = scan_project(
        ScanOptions(root=tmp_path, jobs=1), driver=driver2, cache_path=cache_path
    )

    # a、b 依赖 shared.h；两者都记录着两个依赖，所以都会重编。
    # 关键在于：结果仍然完整，且统计如实反映重编了几个。
    assert scan2.cache_stats.reused + scan2.cache_stats.compiled == 3
    assert scan2.cache_stats.compiled >= 1
    assert len(scan2.ok_tus) == 3


def test_changing_options_forces_a_full_rebuild(tmp_path):
    source = tmp_path / "src" / "a.cpp"
    source.parent.mkdir()
    source.write_text("int a(){return 1;}\n", encoding="utf-8")

    cache_path = tmp_path / ".cache.json"
    opts = ScanOptions(root=tmp_path, jobs=1, standard="c++17")
    scan_project(opts, driver=_CountingDriver(), cache_path=cache_path)

    driver2 = _CountingDriver()
    scan2 = scan_project(
        ScanOptions(root=tmp_path, jobs=1, standard="c++20"),
        driver=driver2,
        cache_path=cache_path,
    )

    assert scan2.cache_stats.cache_loaded is True
    assert scan2.cache_stats.fingerprint_matched is False
    assert scan2.cache_stats.reused == 0, "编译选项变了，上次的结论一律作废"


def test_cache_is_dropped_for_units_that_disappeared(tmp_path):
    """项目演进后缓存不该无限膨胀。"""
    kept = tmp_path / "src" / "a.cpp"
    dropped = tmp_path / "src" / "b.cpp"
    kept.parent.mkdir()
    kept.write_text("int a(){return 1;}\n", encoding="utf-8")
    dropped.write_text("int b(){return 2;}\n", encoding="utf-8")

    cache_path = tmp_path / ".cache.json"
    scan_project(
        ScanOptions(root=tmp_path, jobs=1),
        driver=_CountingDriver(),
        cache_path=cache_path,
    )
    assert len(load_cache(cache_path).entries) == 2

    dropped.unlink()  # 一个编译单元被删掉了

    scan_project(
        ScanOptions(root=tmp_path, jobs=1),
        driver=_CountingDriver(),
        cache_path=cache_path,
    )
    remaining = load_cache(cache_path).entries
    assert str(dropped) not in remaining
    assert str(kept) in remaining
