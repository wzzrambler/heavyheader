"""增量扫描缓存。

全量扫描的代价是「每个编译单元都要真编译一次」。大项目动辄上千个编译单元，
改一行代码也要全跑一遍，于是没人愿意把它接进日常流程。增量扫描要做的就是
回答一个问题：**这个编译单元上次的结论，到今天还成立吗？**

判据只有一条——编译器看到的输入没变，结论就没变。输入包括：

- 编译单元自己的源文件；
- 它依赖的每一个头文件（传递闭包，来自编译器 ``-H`` 的真实输出）；
- 编译选项（编译器、标准、宏、flags、包含路径）。

三者都没变，就不用再编译一次。

## 为什么不只用 mtime

``git checkout``、``touch``、``rsync`` 会改 mtime 但不动内容，纯 mtime 判定会
白白重编整个项目。所以分两级：

1. **快路径**：size 与 mtime_ns 都没变 → 判定未改动，**不读文件内容**；
2. **兜底**：mtime 变过 → 比内容哈希（blake2b），内容真的一样就复用。

这样做的正确性只依赖「内容变了就重编」，而快路径让未改动的文件连打开都不用。

## 为什么不缓存失败

编译失败的单元通常依赖缺失（少一个头）。它记录下来的依赖集是不完整的，
等有人把头补上，缓存里那批依赖“看起来没变”，于是会一直重复报同一个错误。
失败的编译还有个特点：编译器遇到错误就中止，本身很快。所以失败不进缓存，
下次老老实实重编。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import IncludeEvent, PhaseTiming, TranslationUnit

__all__ = [
    "CacheStats",
    "FileStamp",
    "ScanCache",
    "StampResolver",
    "compiler_fingerprint",
    "load_cache",
    "save_cache",
]

_SCHEMA_VERSION = 1
_CHUNK = 1 << 20  # 1 MiB


# --------------------------------------------------------------------------
# file stamps
# --------------------------------------------------------------------------


class FileStamp:
    """一个文件的指纹：stat 立即拿到，内容哈希按需计算。"""

    __slots__ = ("path", "size", "mtime_ns", "_digest")

    def __init__(self, path: Path, size: int, mtime_ns: int):
        self.path = path
        self.size = size
        self.mtime_ns = mtime_ns
        self._digest: str | None = None

    @property
    def digest(self) -> str | None:
        """内容哈希，首次访问时才读文件；不可读返回 None。"""
        if self._digest is None:
            self._digest = _hash_file(self.path)
        return self._digest


def _hash_file(path: Path) -> str | None:
    try:
        digest = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _stamp(path: Path) -> FileStamp | None:
    """只 stat，不读内容。文件不存在返回 None。"""
    try:
        info = path.stat()
    except OSError:
        return None
    return FileStamp(path, info.st_size, info.st_mtime_ns)


def _encode(stamp: FileStamp) -> str:
    """把指纹写成 ``size:mtime_ns:digest``；这里会触发一次内容哈希。"""
    digest = stamp.digest
    return f"{stamp.size}:{stamp.mtime_ns}:{digest if digest else '-'}"


def _matches(stamp: FileStamp, encoded: str) -> bool:
    """先比 stat（不读文件），不同再比内容。"""
    parts = encoded.split(":", 2)
    if len(parts) != 3:
        return False
    size_text, mtime_text, digest_text = parts
    try:
        if stamp.size == int(size_text) and stamp.mtime_ns == int(mtime_text):
            return True
    except ValueError:
        return False
    # mtime 动过：只有内容哈希一致才算没变。缓存里没有哈希（例如早期版本
    # 写的文件）时，保守地判定为已改动。
    return digest_text != "-" and stamp.digest == digest_text


class StampResolver:
    """按路径记忆 stat 结果，避免同一头文件（被上百个 TU 依赖）反复 stat。"""

    def __init__(self) -> None:
        self._stamps: dict[Path, FileStamp | None] = {}

    def stamp(self, path: Path) -> FileStamp | None:
        key = Path(path)
        if key not in self._stamps:
            self._stamps[key] = _stamp(key)
        return self._stamps[key]


# --------------------------------------------------------------------------
# cache model
# --------------------------------------------------------------------------


@dataclass
class CacheStats:
    """这次扫描复用/重编了多少，用于让用户看见增量确实生效。"""

    total: int = 0
    reused: int = 0
    compiled: int = 0
    cache_loaded: bool = False
    fingerprint_matched: bool = False

    @property
    def reused_percent(self) -> float:
        return (self.reused / self.total * 100.0) if self.total else 0.0


@dataclass
class CacheEntry:
    source: str
    source_stamp: str
    #: 依赖头 -> 指纹。来自编译器 -H 的真实输出，不是猜的。
    deps: dict[str, str] = field(default_factory=dict)
    tu: dict = field(default_factory=dict)

    def is_fresh(self, resolver: StampResolver) -> bool:
        source = Path(self.source)
        stamp = resolver.stamp(source)
        if stamp is None or not _matches(stamp, self.source_stamp):
            return False
        for dep, encoded in self.deps.items():
            dep_stamp = resolver.stamp(Path(dep))
            # 依赖头消失了也是一种变化：include 图已经不同。
            if dep_stamp is None or not _matches(dep_stamp, encoded):
                return False
        return True

    def to_tu(self) -> TranslationUnit | None:
        return _tu_from_dict(self.tu)


@dataclass
class ScanCache:
    schema: int = _SCHEMA_VERSION
    fingerprint: str = ""
    root: str = ""
    entries: dict[str, CacheEntry] = field(default_factory=dict)

    def lookup(self, source: Path, resolver: StampResolver) -> TranslationUnit | None:
        entry = self.entries.get(str(source))
        if entry is None or not entry.is_fresh(resolver):
            return None
        return entry.to_tu()

    def record(self, tu: TranslationUnit, resolver: StampResolver) -> None:
        """记录一次成功编译的结果，供下次复用。失败的跳过（见模块文档）。"""
        if not tu.ok:
            return
        source = Path(tu.source)
        stamp = resolver.stamp(source)
        if stamp is None:  # 源文件都没了，没什么可记的
            return
        deps: dict[str, str] = {}
        for dep in tu.dependencies:
            dep_stamp = resolver.stamp(Path(dep))
            if dep_stamp is not None:
                deps[str(dep_stamp.path)] = _encode(dep_stamp)
        self.entries[str(source)] = CacheEntry(
            source=str(source),
            source_stamp=_encode(stamp),
            deps=deps,
            tu=_tu_to_dict(tu),
        )


# --------------------------------------------------------------------------
# (de)serialisation of a single translation unit
# --------------------------------------------------------------------------


def _tu_to_dict(tu: TranslationUnit) -> dict:
    return {
        "source": str(tu.source),
        "dependencies": [str(p) for p in tu.dependencies],
        "include_events": [
            {
                "includer": str(e.includer),
                "included": str(e.included),
                "depth": e.depth,
                "line": e.line,
            }
            for e in tu.include_events
        ],
        "timings": [
            {
                "name": t.name,
                "wall_seconds": t.wall_seconds,
                "wall_percent": t.wall_percent,
                "memory_bytes": t.memory_bytes,
            }
            for t in tu.timings
        ],
        "unguarded_headers": [str(p) for p in tu.unguarded_headers],
        "timing_discarded": list(tu.timing_discarded),
        "error": tu.error,
    }


def _tu_from_dict(data: dict) -> TranslationUnit | None:
    try:
        return TranslationUnit(
            source=Path(data["source"]),
            dependencies=[Path(p) for p in data.get("dependencies", [])],
            include_events=[
                IncludeEvent(
                    includer=Path(e["includer"]),
                    included=Path(e["included"]),
                    depth=e.get("depth", 0),
                    line=e.get("line"),
                )
                for e in data.get("include_events", [])
            ],
            timings=[
                PhaseTiming(
                    name=t["name"],
                    wall_seconds=t.get("wall_seconds", 0.0),
                    wall_percent=t.get("wall_percent"),
                    memory_bytes=t.get("memory_bytes"),
                )
                for t in data.get("timings", [])
            ],
            unguarded_headers=[Path(p) for p in data.get("unguarded_headers", [])],
            timing_discarded=list(data.get("timing_discarded", [])),
            error=data.get("error"),
        )
    except (KeyError, TypeError):
        # 缓存被手工改坏或来自旧版本：当作没有缓存，而不是让扫描挂掉。
        return None


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def compiler_fingerprint(
    compiler: str,
    family: str,
    standard: str | None,
    include_dirs,
    defines,
    extra_flags,
    full_compile: bool,
) -> str:
    """编译选项指纹。任何一项变了，上次的结论都不再成立。"""
    payload = json.dumps(
        {
            "schema": _SCHEMA_VERSION,
            "compiler": compiler,
            "family": family,
            "standard": standard,
            "include_dirs": sorted(str(Path(d)) for d in include_dirs),
            "defines": sorted(str(d) for d in defines),
            "extra_flags": [str(f) for f in extra_flags],
            "full_compile": full_compile,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def load_cache(path: Path) -> ScanCache | None:
    """读缓存。任何问题都当作「没有缓存」——缓存坏了该退化成全量，不是崩掉。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA_VERSION:
            return None
        entries: dict[str, CacheEntry] = {}
        for item in raw.get("entries", []):
            try:
                entry = CacheEntry(
                    source=item["source"],
                    source_stamp=item["source_stamp"],
                    deps=dict(item.get("deps", {})),
                    tu=dict(item.get("tu", {})),
                )
                entries[entry.source] = entry
            except (KeyError, TypeError):
                continue
        return ScanCache(
            schema=_SCHEMA_VERSION,
            fingerprint=str(raw.get("fingerprint", "")),
            root=str(raw.get("root", "")),
            entries=entries,
        )
    except (OSError, ValueError):
        return None


def save_cache(path: Path, cache: ScanCache) -> bool:
    """写缓存失败不算错误——下次大不了全量扫一遍。"""
    payload = {
        "schema": _SCHEMA_VERSION,
        "fingerprint": cache.fingerprint,
        "root": cache.root,
        "entries": [
            {
                "source": e.source,
                "source_stamp": e.source_stamp,
                "deps": e.deps,
                "tu": e.tu,
            }
            for e in cache.entries.values()
        ],
    }
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return True
    except OSError:
        return False
