"""Rejection of slide frames and figure borders reported as tables."""

from twomarkdown.converter.tables import is_real_table


class TestIsRealTable:
    def test_accepts_an_ordinary_table(self) -> None:
        """is_real_table() — several populated columns and short cells pass."""
        rows = [
            ["Protocolo", "Puerto", "Fiable"],
            ["TCP", "6", "sí"],
            ["UDP", "17", "no"],
        ]
        assert is_real_table(rows) is True

    def test_rejects_slide_frame_with_one_giant_cell(self) -> None:
        """is_real_table() — a whole slide crammed into one cell is not a table."""
        rows = [["x" * 400, "", "", ""], ["(ETSIT. UPV)", "", "", "17 / 52"]]
        assert is_real_table(rows) is False

    def test_rejects_single_populated_column(self) -> None:
        """is_real_table() — a bordered text box has only one real column."""
        rows = [["Funciones de transporte", "", ""], ["Control de flujo", "", ""]]
        assert is_real_table(rows) is False

    def test_rejects_single_row(self) -> None:
        """is_real_table() — one row is a caption strip, not a table."""
        assert is_real_table([["A", "B", "C"]]) is False

    def test_rejects_region_covering_the_page(self) -> None:
        """is_real_table() — a near-full-page region is the slide border."""
        rows = [["A", "B"], ["1", "2"]]
        assert is_real_table(rows, area_ratio=0.95) is False
        assert is_real_table(rows, area_ratio=0.4) is True

    def test_rejects_a_sparse_list_layout(self) -> None:
        """is_real_table() — a bulleted list laid out in columns is mostly empty."""
        rows = [
            ["Componentes:", "", "", "", "", ""],
            ["-", "Terminal", "", "(host)", "", "Dispositivo"],
            ["", "Nodo", "(router)", "", "", ""],
            ["", "Linea", "", "(link)", "", ""],
        ]
        assert is_real_table(rows) is False

    def test_accepts_a_densely_filled_table(self) -> None:
        """is_real_table() — a real grid is populated in nearly every cell."""
        rows = [
            ["Curso", "Asignatura", "Nota"],
            ["1", "Fisica I", "6.1"],
            ["1", "Matematicas I", "9.5"],
        ]
        assert is_real_table(rows) is True
