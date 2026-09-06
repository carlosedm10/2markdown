"""Tests for deterministic markdown cleaning (src.converter.clean)."""

from unittest.mock import patch

from src.converter.clean import (
    _utf8_as_latin1,
    clean_markdown,
    dehyphenate_line_breaks,
    group_broken_paragraphs,
    replace_unicode_mojibake,
    strip_repeated_running_headers,
)


class TestReplaceUnicodeMojibake:
    def test_replace_unicode_mojibake_fixes_apostrophe(self) -> None:
        raw = "it" + _utf8_as_latin1(b"\xe2\x80\x99") + "s fine"
        assert replace_unicode_mojibake(raw) == "it's fine"

    def test_replace_unicode_mojibake_fixes_curly_quotes(self) -> None:
        left = _utf8_as_latin1(b"\xe2\x80\x9c")
        right = _utf8_as_latin1(b"\xe2\x80\x9d")
        raw = f"{left}Hello{right}"
        assert replace_unicode_mojibake(raw) == '"Hello"'

    def test_replace_unicode_mojibake_strips_stray_c2_a0(self) -> None:
        raw = "word" + _utf8_as_latin1(b"\xc2\xa0") + "next"
        assert replace_unicode_mojibake(raw) == "word next"

    def test_replace_unicode_mojibake_maps_windows_quotes(self) -> None:
        raw = "\x91single\x92 and \x93double\x94"
        assert replace_unicode_mojibake(raw) == "'single' and \"double\""


class TestDehyphenateLineBreaks:
    def test_dehyphenate_line_breaks_joins_split_word(self) -> None:
        raw = "docu-\nment"
        assert dehyphenate_line_breaks(raw) == "document"

    def test_dehyphenate_line_breaks_leaves_intentional_hyphen(self) -> None:
        raw = "well-\nknown idea"
        assert dehyphenate_line_breaks(raw) == "wellknown idea"

    def test_dehyphenate_line_breaks_ignores_non_letter_boundaries(self) -> None:
        raw = "1-\n2"
        assert dehyphenate_line_breaks(raw) == "1-\n2"


class TestGroupBrokenParagraphs:
    def test_group_broken_paragraphs_joins_wrapped_lines(self) -> None:
        raw = "First line\nsecond line\n\nNew paragraph"
        assert group_broken_paragraphs(raw) == "First line second line\n\nNew paragraph"

    def test_group_broken_paragraphs_preserves_table_rows(self) -> None:
        raw = "| a | b |\n| c | d |\n\nAfter table"
        assert group_broken_paragraphs(raw) == "| a | b |\n| c | d |\n\nAfter table"

    def test_group_broken_paragraphs_preserves_headings(self) -> None:
        raw = "# Title\nBody line\nmore body"
        assert group_broken_paragraphs(raw) == "# Title\nBody line more body"

    def test_group_broken_paragraphs_preserves_fenced_code(self) -> None:
        raw = "```\nline one\nline two\n```\n\nProse"
        assert group_broken_paragraphs(raw) == "```\nline one\nline two\n```\n\nProse"


class TestStripRepeatedRunningHeaders:
    def test_strip_repeated_running_headers_removes_triplicate_header(self) -> None:
        header = "Annual Report 2024"
        pages = []
        for n in range(1, 4):
            pages.append(
                f"## Page {n}\n{header}\nUnique content page {n}\nPage {n} of 40"
            )
        raw = "\n\n".join(pages)
        result = strip_repeated_running_headers(raw)
        assert header not in result
        assert "Unique content page 1" in result
        assert "Unique content page 3" in result
        assert "Page 1 of 40" not in result

    def test_strip_repeated_running_headers_keeps_two_page_repeats(self) -> None:
        header = "Short Header"
        raw = f"## Page 1\n{header}\nAlpha\n\n## Page 2\n{header}\nBeta"
        result = strip_repeated_running_headers(raw)
        assert result.count(header) == 2

    def test_strip_repeated_running_headers_drops_page_number_formats(self) -> None:
        raw = "Intro\n- 12 -\n12/40\nPage 3 of 40\nBody"
        result = strip_repeated_running_headers(raw)
        assert result == "Intro\nBody"


class TestCleanMarkdown:
    def test_clean_markdown_applies_pipeline_when_enabled(self) -> None:
        apostrophe = _utf8_as_latin1(b"\xe2\x80\x99")
        raw = f"docu-\nment{apostrophe}s\n\nNext"
        with patch("src.converter.clean.conversion_config.clean_markdown", True):
            result = clean_markdown(raw)
        assert result == "document's\n\nNext"

    def test_clean_markdown_identity_when_disabled(self) -> None:
        raw = "docu-\nment\n\nunchanged"
        with patch("src.converter.clean.conversion_config.clean_markdown", False):
            assert clean_markdown(raw) == raw
