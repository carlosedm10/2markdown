"""Pipe tables from different converters arrive malformed in opposite ways."""

from twomarkdown.converter.clean import repair_markdown_tables


class TestRepairMarkdownTables:
    def test_adds_a_missing_separator(self) -> None:
        """repair_markdown_tables() — LibreOffice tables render as pipes without it."""
        src = "| Apellidos | Nombre |\n| Dominguez | Carlos |\n"
        out = repair_markdown_tables(src).split("\n")

        assert out[0] == "| Apellidos | Nombre |"
        assert out[1] == "| --- | --- |"
        assert out[2] == "| Dominguez | Carlos |"

    def test_promotes_a_blank_header_row(self) -> None:
        """repair_markdown_tables() — MarkItDown leaves the real header in the body."""
        src = "|  |  |\n| --- | --- |\n| Apellidos | Nombre |\n| Dominguez | Carlos |\n"
        out = repair_markdown_tables(src).split("\n")

        assert out[0] == "| Apellidos | Nombre |"
        assert out[1] == "| --- | --- |"
        assert out[2] == "| Dominguez | Carlos |"

    def test_well_formed_table_is_untouched(self) -> None:
        """repair_markdown_tables() — a correct table is left exactly as it is."""
        src = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
        assert repair_markdown_tables(src) == src

    def test_alignment_markers_count_as_a_separator(self) -> None:
        """repair_markdown_tables() — :--- and ---: are valid separators."""
        src = "| a | b |\n| :--- | ---: |\n| 1 | 2 |\n"
        assert repair_markdown_tables(src) == src

    def test_multiple_tables_are_each_repaired(self) -> None:
        """repair_markdown_tables() — every table in the document is fixed."""
        src = "| a | b |\n| 1 | 2 |\n\ntexto\n\n| c | d |\n| 3 | 4 |\n"
        assert repair_markdown_tables(src).count("| --- | --- |") == 2

    def test_tables_inside_code_fences_are_left_alone(self) -> None:
        """repair_markdown_tables() — pipes in a code block are content."""
        src = "```\n| a | b |\n| 1 | 2 |\n```\n"
        assert repair_markdown_tables(src) == src

    def test_single_row_table_still_gets_a_separator(self) -> None:
        """repair_markdown_tables() — a lone row is still a header."""
        out = repair_markdown_tables("| solo |\n").split("\n")
        assert out[1] == "| --- |"

    def test_blank_header_with_no_body_is_not_dropped(self) -> None:
        """repair_markdown_tables() — never delete the only row present."""
        src = "|  |  |\n| --- | --- |\n"
        assert repair_markdown_tables(src) == src
