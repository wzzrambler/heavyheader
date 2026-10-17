"""libclang spike (2026-10-18): the ctypes route is **not** viable here.

WHAT THIS FILE IS
-----------------
A documented negative result, not a working prototype.  The round-4 plan
listed "raise candidate-pool precision with AST-grade semantics" as the
third item, and the obvious way to do it without breaking the project's
zero-third-party-dependency promise is ``ctypes`` against a system
libclang.  This spike tested exactly that.  It failed, silently, and the
failure is recorded here with its full evidence trail so the next person
does not repeat the afternoon.

WHAT WORKS (proven, kept as a smoke test)
-----------------------------------------
* ``libclang.dll`` loads through ``ctypes.cdll`` -- the zero-dependency
  path is *loadable*;
* ``clang_createIndex`` returns a valid pointer;
* ``clang_getClangVersion`` returns a sane string when its CXString return
  is bound correctly (struct-by-value returns need ``restype=_CXString``,
  not ``c_void_p`` -- getting that wrong crashes with an access violation,
  which is how this was verified).

WHAT DOES NOT WORK (the blocker)
--------------------------------
Every TU-creation entry point returns nothing:

===========================  ======  ======================================
attempt                     result  note
===========================  ======  ======================================
clang_parseTranslationUnit  NULL    original API, no args
parseTranslationUnit2       err 4    CXError_ASTReadError, no diagnostics
createTranslationUnitFrom   NULL    oldest API
SourceFile
+ ``-resource-dir``         err 4    builtin headers were not the issue
+ ``-fsyntax-only``         err 4
+ displayDiagnostics=1      err 4    clang printed nothing at all
winmode=0 load              err 4    dependency-search hypothesis ruled out
os.add_dll_directory load   err 4    same
===========================  ======  ======================================

Error 4 with zero diagnostics is the worst kind of failure: there is
nothing to grep.  The same libclang works when driven by its own CLI
(``clang-include-cleaner`` analyses 63/64 units of the experiment project
-- see ``competitor.py``), so the library is functional and the problem is
specific to the in-process ctypes path on this machine.

CONCLUSION FOR v0.3
-------------------
Do not ship an in-process libclang backend on the strength of "ctypes can
load it".  The loadable part is the easy 10%; the parse is where it dies,
and the failure gives no diagnostics to debug against.

Two routes remain, both recorded in ROADMAP v0.3:

1. **Detected external tool** (recommended): when ``clang-include-cleaner``
   is present on the machine (it ships with any LLVM toolchain, and this
   project already speaks its output format in ``competitor.py``), run it
   as an optional ``--semantic`` cross-check that demotes candidates the
   AST says are in use.  Absent tool = current behaviour, stated plainly.
   This matches the project's existing philosophy: ask a real tool what it
   did; never require one the user does not have.
2. **Fix the ctypes binding** (unknown effort): the blocker is silent, so
   the first step would be a C reproducer against the same DLL to
   establish whether the fault is in the binding or in the library's
   in-process initialisation.  That is a separate afternoon, not a
   sub-task of a release.

Running this file re-verifies the loadable half (it exits non-zero if even
that regresses) and re-prints the blocker, so the negative result stays
reproducible instead of rotting in a commit message.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LIBCLANG_PATHS = [
    "C:/Program Files/LLVM/bin/libclang.dll",
    "/usr/lib/llvm-18/lib/libclang.so",
    "/usr/lib/x86_64-linux-gnu/libclang-18.so",
    "libclang.so",
    "libclang.dylib",
]


class _CXString(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p), ("private_flags", ctypes.c_uint)]


def load_libclang():
    for candidate in LIBCLANG_PATHS:
        try:
            return ctypes.cdll.LoadLibrary(candidate), candidate
        except OSError:
            continue
    raise OSError(f"libclang not loadable from any of: {', '.join(LIBCLANG_PATHS)}")


def bind(lib):
    lib.clang_createIndex.restype = ctypes.c_void_p
    lib.clang_createIndex.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.clang_getClangVersion.restype = _CXString
    lib.clang_getClangVersion.argtypes = []
    lib.clang_getCString.restype = ctypes.c_char_p
    lib.clang_getCString.argtypes = [_CXString]
    lib.clang_disposeString.argtypes = [_CXString]
    lib.clang_parseTranslationUnit2.restype = ctypes.c_int
    lib.clang_parseTranslationUnit2.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_char_p),
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.clang_disposeIndex.argtypes = [ctypes.c_void_p]
    return lib


def main() -> int:
    lib, loaded_from = load_libclang()
    lib = bind(lib)
    print(f"[spike] libclang loaded from: {loaded_from}")

    version = lib.clang_getClangVersion()
    text = lib.clang_getCString(version)
    print(f"[spike] version: {text.decode('utf-8', 'replace').splitlines()[0][:80]}")
    lib.clang_disposeString(version)

    index = lib.clang_createIndex(0, 1)
    print(f"[spike] createIndex: {'OK' if index else 'FAILED'}")
    if not index:
        return 1

    tiny = HERE / "_work" / "spike_tiny.cpp"
    tiny.parent.mkdir(parents=True, exist_ok=True)
    tiny.write_text("int main() { return 0; }\n", encoding="utf-8")
    argv = (ctypes.c_char_p * 1)(str(tiny).encode("utf-8"))
    out = ctypes.c_void_p()
    err = lib.clang_parseTranslationUnit2(
        index, str(tiny).encode("utf-8"), argv, 1, None, 0, 0, ctypes.byref(out)
    )
    lib.clang_disposeIndex(index)

    if out.value:
        print("[spike] parse: OK -- the blocker is gone; revisit ROADMAP v0.3 route 2")
        return 0
    print(
        f"[spike] parse: BLOCKED (CXError_ASTReadError={err}, no diagnostics).\n"
        "[spike] In-process ctypes libclang is not viable on this machine;\n"
        "[spike] see the module docstring for the full evidence trail and the\n"
        "[spike] two remaining routes (ROADMAP v0.3)."
    )
    # 负面结果本身是预期结局：退出 0，让「复现」不等于「必须成功」。
    return 0


if __name__ == "__main__":
    sys.exit(main())
