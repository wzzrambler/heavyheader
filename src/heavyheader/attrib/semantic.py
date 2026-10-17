"""Optional AST-grade cross-check via clang-include-cleaner (ROADMAP v0.3, route 1).

The ctypes-in-process route (route 2) was proven non-viable on this machine:
see ``experiments/libclang_spike.py`` -- every TU-creation entry point returns
nothing with ``CXError_ASTReadError`` (code 4) and *zero* diagnostics, which is
un-debuggable.  The remaining viable route is to drive the *same* LLVM tool as
an external process -- which is exactly what ``experiments/competitor.py``
already does successfully on 63/64 units.  This module brings that capability
into the normal scan/verify pipeline, so the semantic signal is available
everywhere, not just in the competitor comparison script.

WHY "CORROBORATE ONLY, NEVER DEMOTE"
------------------------------------
clang-include-cleaner prints the includes *it* thinks are removable
(``- "header.h"`` lines under ``--print=changes``).  Silence about a header does
**not** mean "it is used" -- the tool may not have analysed that translation
unit, or its heuristics may simply differ from ours.  So we only ever *add*
confidence when it agrees with us; we never downgrade a verified winner on its
silence.  That keeps the contract honest: this layer can make a REMOVE *more*
trustworthy, never less.  (The alternative -- demoting every candidate the AST
tool stays silent on -- would be the ctypes failure all over again, just slower.)
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: A removal recommendation: ``- "some/header.h"`` in ``--print=changes`` output.
_REMOVE_RE = re.compile(r'^\s*-\s*"(?P<target>[^"]+)"\s*$')

#: Where clang-include-cleaner is likely installed, in probe order.
_DEFAULT_PATHS = [
    "C:/Program Files/LLVM/bin/clang-include-cleaner.exe",
    "/usr/lib/llvm-18/bin/clang-include-cleaner",
    "/usr/lib/llvm-17/bin/clang-include-cleaner",
    "/usr/bin/clang-include-cleaner",
    "/usr/local/bin/clang-include-cleaner",
    "clang-include-cleaner",
]


@dataclass(slots=True)
class SemanticVerdict:
    """What clang-include-cleaner concluded about one translation unit.

    ``removals`` holds the *basenames* (not full paths) the tool thinks are
    safe to drop -- matching on basename is deliberate, because the tool may
    report a normalised or resolved path that does not string-match our own.
    """

    unit: str
    removals: frozenset[str]
    ok: bool
    note: str = ""


def default_mingw_args() -> list[str]:
    """Include args that make clang parse a MinGW-built TU it did not produce.

    Without these, clang-include-cleaner targets the host default (MSVC on
    Windows), cannot find ``<string>`` where MinGW put it, and dies on its own
    ``__stddef_max_align_t.h``.  This is the same information g++ gets
    implicitly, stated explicitly -- exactly what a cross-toolchain comparison
    has to do.  Mirrors ``experiments/competitor.py::clang_extra_args`` so the
    two call sites cannot drift.
    """
    gcc = Path("C:/mingw64/lib/gcc/x86_64-w64-mingw32/16.2.0/include")
    mingw = Path("C:/mingw64/x86_64-w64-mingw32/include")
    return [
        "--target=x86_64-w64-windows-gnu",
        "-nostdinc++",
        "-nobuiltininc",
        f"-I{gcc / 'c++'}",
        f"-I{gcc / 'c++' / 'x86_64-w64-mingw32'}",
        f"-I{mingw}",
        f"-I{gcc}",
    ]


def _semantic_agrees(verdict: SemanticVerdict | None, header: Path) -> bool | None:
    """True = AST tool also says this header is removable (corroborated).

    False = the tool ran but did not list it as removable (silence -- *not* a
    verdict of "in use").  None = the cross-check was unavailable (tool absent
    or its analysis of this unit failed), so it carries no signal either way.
    """
    if verdict is None or not verdict.ok:
        return None
    return header.name in verdict.removals


class SemanticBackend:
    """Drive clang-include-cleaner as an optional AST cross-check.

    Construction never raises: if the tool is absent, ``available`` is False and
    every call is a cheap no-op that reports the tool as missing.  That is what
    lets the rest of the pipeline treat the semantic layer as strictly optional.
    """

    def __init__(
        self,
        tool_path: Path | str | None = None,
        extra_args: list[str] | None = None,
    ) -> None:
        self.tool: Path | None = self._detect(tool_path)
        self.extra_args = list(extra_args or [])

    @property
    def available(self) -> bool:
        return self.tool is not None

    @staticmethod
    def _detect(tool_path: Path | str | None) -> Path | None:
        candidates: list[str] = []
        if tool_path:
            candidates.append(str(tool_path))
        env = os.environ.get("CLANG_INCLUDE_CLEANER")
        if env:
            candidates.append(env)
        candidates.extend(_DEFAULT_PATHS)
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return Path(candidate)
        return None

    def analyse(self, unit: Path, build_path: Path) -> SemanticVerdict:
        """Run clang-include-cleaner on one unit; return its removals.

        ``build_path`` is the directory holding ``compile_commands.json`` (the
        ``-p`` argument).  Failure to run is reported in the verdict (``ok=False``)
        rather than raised, so one broken unit never aborts the whole scan.
        """
        assert self.tool is not None, "call analyse only when available"
        command = [
            str(self.tool),
            "-p",
            str(build_path),
            "--print=changes",
            *[arg for extra in self.extra_args for arg in ("--extra-arg-before", extra)],
            str(unit),
        ]
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return SemanticVerdict(
                unit=unit.stem, removals=frozenset(), ok=False, note=f"执行失败：{exc}"
            )

        removals: set[str] = set()
        for line in proc.stdout.splitlines():
            match = _REMOVE_RE.match(line)
            if match:
                removals.add(Path(match.group("target")).name)

        # clang-include-cleaner returns non-zero both for "ran, found nothing
        # actionable" and for "could not analyse this unit".  We keep the
        # removals either way but flag real analysis failure on stderr.
        ok = proc.returncode == 0 or bool(removals)
        note = ""
        if not ok:
            note = (proc.stderr.strip().splitlines() or ["unknown analysis failure"])[-1][:200]
        return SemanticVerdict(unit=unit.stem, removals=frozenset(removals), ok=ok, note=note)

    def agrees(self, verdict: SemanticVerdict | None, header: Path) -> bool | None:
        return _semantic_agrees(verdict, header)
