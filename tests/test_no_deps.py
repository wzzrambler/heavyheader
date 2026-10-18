"""Governance guard: the zero-runtime-dependency constraint must be enforced,
not just promised.

Two checks:

1. ``pyproject.toml`` must declare no runtime dependencies;
2. every module under ``src/`` must import only the standard library.

If a future change adds a runtime dependency, this test fails loudly - the
architecture promise ("the default path runs in the plainest CI container")
is enforced by CI, not by documentation alone.

Python 3.10 note: ``tomllib`` is stdlib only from 3.11 on, and the CI matrix
still runs 3.10 (``requires-python = ">=3.10"``).  On 3.10 we fall back to a
conservative textual read of the ``dependencies`` key.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: First-party package name: ``heavyheader`` importing itself (absolutely or
#: relatively) is not a third-party dependency.
FIRST_PARTY = "heavyheader"


def _declared_dependencies(pyproject_text: str) -> list[str]:
    """Runtime dependencies from ``pyproject.toml``.

    Prefers ``tomllib`` (3.11+).  On 3.10 the textual fallback reads the
    ``dependencies`` key inside ``[project]``; if the key cannot be found at
    all it reports a non-empty list, so the caller fails loudly instead of
    silently passing on an unproven claim.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: no stdlib TOML parser
        match = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)\]", pyproject_text)
        if match is None:
            return ["<dependencies key not found>"]
        return [item for item in re.split(r"[,\n]", match.group(1)) if item.strip()]
    return tomllib.loads(pyproject_text)["project"].get("dependencies", [])


def test_no_runtime_dependencies_declared() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = _declared_dependencies(pyproject)
    assert dependencies == [], (
        "runtime dependencies break the zero-dependency architecture: "
        f"{dependencies}. Discuss in an issue first (GOVERNANCE.md §2)."
    )


def test_src_imports_are_stdlib_only() -> None:
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import inside the package
                    continue
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                if root and root != FIRST_PARTY and root not in sys.stdlib_module_names:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {root}")
    assert not offenders, "non-stdlib imports found in src/:\n" + "\n".join(offenders)
