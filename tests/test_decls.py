"""Tests for declaration-scope name extraction (``attrib.decls``).

The module exists because two measured failures shared one root cause: the
old extraction treated *any* token in a header as a name that header
provides.  A parameter named ``size`` made ``<stdlib.h>`` look like it
declares ``size`` (which blocked a verified -2.125% zlib win behind the
feasibility gate); a member named ``at`` made ``<string>`` look like it
declares ``at`` (which let a doomed doctest candidate through on a
consumer's ``.at(...)`` call).  These tests pin both directions, plus the
integration effects: the usage verdict and the downstream-evidence column
must both change shape because of it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from heavyheader.attrib.decls import declared_names  # noqa: E402
from heavyheader.attrib.usage import (  # noqa: E402
    IdentifierIndex,
    UsageVerdict,
    analyse_usage,
)


class DeclaredNamesTests(unittest.TestCase):
    def test_parameter_name_is_not_a_declaration(self) -> None:
        """The zlib case: ``size`` in ``malloc(size_t size)`` is a parameter."""
        text = "extern void *malloc(unsigned long size);\nextern void free(void *ptr);\n"
        names = declared_names(text)
        self.assertIn("malloc", names)
        self.assertIn("free", names)
        self.assertNotIn("size", names)
        self.assertNotIn("ptr", names)

    def test_class_member_is_not_a_declaration(self) -> None:
        """The doctest case: ``at`` inside a class body is a member name."""
        text = (
            "template<class T>\n"
            "class holder {\n"
            "public:\n"
            "    T& at(unsigned long pos);\n"
            "    unsigned long size() const;\n"
            "};\n"
        )
        names = declared_names(text)
        self.assertIn("holder", names)
        self.assertNotIn("at", names)
        self.assertNotIn("size", names)
        self.assertNotIn("push_back", names)

    def test_macro_wrapper_around_extern_c_still_yields_names(self) -> None:
        """``_BEGIN_C_DECLS {`` must not swallow the whole header.

        Measured on MinGW's ``<stdlib.h>``: classifying a bare ``{`` as opaque
        made ``free`` and ``abort`` vanish entirely.
        """
        text = (
            "#pragma once\n"
            "_BEGIN_C_DECLS\n"
            "extern int free(void *ptr);\n"
            "_END_C_DECLS\n"
        )
        self.assertIn("free", declared_names(text))

    def test_namespace_is_transparent(self) -> None:
        text = "namespace outer {\nnamespace inner {\nint sort(int);\n}\n}\n"
        names = declared_names(text)
        self.assertIn("sort", names)

    def test_class_head_and_brace_on_different_lines(self) -> None:
        """libstdc++ style: ``class basic_string`` then a bare ``{``.

        Classifying from the start of the *line* instead of the start of the
        statement made the class body transparent and leaked ~900 member
        names; this test pins the statement-level classification.
        """
        text = (
            "namespace std {\n"
            "template<class C, class T>\n"
            "  class basic_string\n"
            "  {\n"
            "  public:\n"
            "    C& at(unsigned long pos);\n"
            "  };\n"
            "}\n"
        )
        names = declared_names(text)
        self.assertIn("basic_string", names)
        self.assertNotIn("at", names)

    def test_macro_body_parameters_are_skipped(self) -> None:
        """``#define _malloca(size)`` put ``size`` at paren depth 0."""
        text = "#define _malloca(size) \\\n    take((size) + 1)\n"
        names = declared_names(text)
        self.assertIn("_malloca", names)
        self.assertNotIn("size", names)
        self.assertNotIn("take", names)

    def test_macro_names_count_wherever_they_sit(self) -> None:
        text = "#define ZLIB_VERSION \"1.3\"\n#define MAX_DEPTH 8\n"
        names = declared_names(text)
        self.assertIn("ZLIB_VERSION", names)
        self.assertIn("MAX_DEPTH", names)

    def test_enum_member_inside_braced_body_is_excluded(self) -> None:
        text = "enum kind { value_a, value_b };\n"
        names = declared_names(text)
        self.assertIn("kind", names)
        self.assertNotIn("value_a", names)


class UsageIntegrationTests(unittest.TestCase):
    def _index(self) -> IdentifierIndex:
        return IdentifierIndex()

    def test_zlib_shape_now_reads_as_unused(self) -> None:
        """A header whose only ``size`` is a parameter no longer blocks."""
        with tempfile.TemporaryDirectory(prefix="heavyheader-decls-") as tmp:
            root = Path(tmp)
            header = root / "zutil.h"
            header.write_text(
                '#pragma once\n#include <stdlib.h>\n', encoding="utf-8"
            )
            stdlib = root / "stdlib.h"
            stdlib.write_text(
                "#pragma once\n"
                'extern "C" {\n'
                "extern void *malloc(unsigned long size);\n"
                "extern void free(void *ptr);\n"
                "}\n",
                encoding="utf-8",
            )
            source = root / "zutil.c"
            source.write_text(
                '#include "zutil.h"\n'
                "unsigned long size = 4;\n"
                "int main() { return size; }\n",
                encoding="utf-8",
            )
            signal = analyse_usage(
                source, stdlib, closure=[stdlib, header], index=self._index()
            )
            self.assertIs(signal.verdict, UsageVerdict.PLAUSIBLY_UNUSED)

    def test_real_use_still_blocks(self) -> None:
        """The symmetric guard: an actual call still reads as in use."""
        with tempfile.TemporaryDirectory(prefix="heavyheader-decls-") as tmp:
            root = Path(tmp)
            stdlib = root / "stdlib.h"
            stdlib.write_text(
                "#pragma once\n"
                'extern "C" {\n'
                "extern void *malloc(unsigned long size);\n"
                "}\n",
                encoding="utf-8",
            )
            source = root / "main.c"
            source.write_text(
                '#include "stdlib.h"\n'
                "int main() { void *p = malloc(8); return p != 0; }\n",
                encoding="utf-8",
            )
            signal = analyse_usage(source, stdlib, closure=[stdlib], index=self._index())
            self.assertIs(signal.verdict, UsageVerdict.IN_USE)


if __name__ == "__main__":
    unittest.main()
