"""Governance guard: the zero-runtime-dependency constraint must be enforced,
not just promised.

Two checks:

1. ``pyproject.toml`` must declare no runtime dependencies;
2. every module under ``src/`` must import only the standard library.

If a future change adds a runtime dependency, this test fails loudly - the
architecture promise ("the default path runs in the plainest CI container")
is enforced by CI, not by documentation alone.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]

#: First-party package name: ``heavyheader`` importing itself (absolutely or
#: relatively) is not a third-party dependency.
FIRST_PARTY = "heavyheader"


def test_no_runtime_dependencies_declared() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"].get("dependencies", [])
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
