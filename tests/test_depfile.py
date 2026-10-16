"""Tests for the dependency-file parser.

The fixtures are deliberately awkward: escaped spaces in paths, multiple
targets, wrapped lines, CRLF endings.  Real ``-M`` output from a Windows
compiler looks exactly like this and a parser that only handles the pretty
case will fail on the first real project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from heavyheader.parse.depfile import DepfileError, parse_depfile_text


def test_simple_rule():
    text = "t.o: t.cpp a.h b.h\n"
    assert parse_depfile_text(text) == [
        Path("t.cpp"),
        Path("a.h"),
        Path("b.h"),
    ]


def test_line_continuations_are_joined():
    text = "t.o: t.cpp \\\n a.h \\\n b.h\n"
    assert parse_depfile_text(text) == [Path("t.cpp"), Path("a.h"), Path("b.h")]


def test_escaped_spaces_in_paths_are_not_separators():
    text = "t.o: my\\ src/main.cpp my\\ include/a.h\n"
    parsed = parse_depfile_text(text)
    assert Path("my src/main.cpp") in parsed
    assert Path("my include/a.h") in parsed
    assert len(parsed) == 2


def test_multiple_targets_are_dropped():
    text = "a.o b.o: a.cpp b.cpp\n"
    assert parse_depfile_text(text) == [Path("a.cpp"), Path("b.cpp")]


def test_duplicates_keep_first_occurrence_order():
    text = "t.o: a.h b.h a.h\n"
    assert parse_depfile_text(text) == [Path("a.h"), Path("b.h")]


def test_crlf_line_endings():
    text = "t.o: t.cpp \\\r\n a.h \\\r\n b.h\r\n"
    assert parse_depfile_text(text) == [Path("t.cpp"), Path("a.h"), Path("b.h")]


def test_windows_drive_letter_paths_are_normalised():
    text = "t.o: C:/mingw64/include/c++/vector\n"
    parsed = parse_depfile_text(text)
    assert parsed == [Path("C:/mingw64/include/c++/vector")]


def test_empty_input_yields_empty_list():
    assert parse_depfile_text("") == []
    assert parse_depfile_text("   \n  \n") == []


def test_garbage_raises_depfile_error():
    with pytest.raises(DepfileError):
        parse_depfile_text("this is not a make rule at all")


def test_colon_inside_a_path_does_not_split_the_rule():
    # A Windows path with a drive letter contains a colon; only the first
    # *unescaped* colon separates targets from prerequisites.
    text = "C:/build/t.o: C:/src/a.h\n"
    parsed = parse_depfile_text(text)
    assert parsed == [Path("C:/src/a.h")]
