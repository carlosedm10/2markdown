"""MATLAB sources: plain .m scripts and .mlx Live Scripts."""

import zipfile
from pathlib import Path

import pytest

from twomarkdown.converter.matlab import (
    MatlabConversionError,
    convert_matlab,
    is_live_script,
    is_matlab,
)

_DOC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>
<w:p><w:pPr><w:pStyle w:val="text"/></w:pPr><w:r><w:t>Tubo cerrado</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="code"/></w:pPr><w:r><w:t>L1 = 0.899;</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="code"/></w:pPr><w:r><w:t>T1 = 24;</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="text"/></w:pPr><w:r><w:t>Operaciones:</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="code"/></w:pPr>
<w:r><w:t>c1 = 331.4 + 0.61*T1</w:t></w:r></w:p>
</w:body></w:document>
"""


def _make_mlx(path: Path, doc: str = _DOC_XML) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("matlab/document.xml", doc)
    return path


class TestPlainScripts:
    def test_m_file_becomes_a_fenced_block(self, tmp_path: Path) -> None:
        """convert_matlab() — a .m script stays copy-pasteable code."""
        src = tmp_path / "calc.m"
        src.write_text("x = 1:10;\ny = x.^2;\n", encoding="utf-8")

        out = convert_matlab(src)

        assert "## calc.m" in out
        assert "```matlab" in out
        assert "y = x.^2;" in out

    def test_empty_m_file_raises(self, tmp_path: Path) -> None:
        """convert_matlab() — nothing to convert is an error, not empty output."""
        src = tmp_path / "empty.m"
        src.write_text("   \n", encoding="utf-8")

        with pytest.raises(MatlabConversionError):
            convert_matlab(src)

    def test_backticks_in_code_get_a_longer_fence(self, tmp_path: Path) -> None:
        """convert_matlab() — a fence must outlast backticks in the source."""
        src = tmp_path / "ticks.m"
        src.write_text("s = '```';\n", encoding="utf-8")

        out = convert_matlab(src)

        assert "````matlab" in out


class TestLiveScripts:
    def test_narrative_and_code_stay_interleaved(self, tmp_path: Path) -> None:
        """convert_matlab() — a Live Script's structure is the point of it."""
        out = convert_matlab(_make_mlx(tmp_path / "prac.mlx"))

        assert out.index("Tubo cerrado") < out.index("L1 = 0.899;")
        assert out.index("L1 = 0.899;") < out.index("Operaciones:")
        assert out.index("Operaciones:") < out.index("c1 = 331.4")

    def test_consecutive_code_paragraphs_are_one_block(self, tmp_path: Path) -> None:
        """convert_matlab() — adjacent code lines share a fence, not one each."""
        out = convert_matlab(_make_mlx(tmp_path / "prac.mlx"))

        assert "L1 = 0.899;\nT1 = 24;" in out
        assert out.count("```matlab") == 2

    def test_is_live_script_requires_the_document(self, tmp_path: Path) -> None:
        """is_live_script() — an .mlx without the MATLAB part is not one."""
        bogus = tmp_path / "fake.mlx"
        with zipfile.ZipFile(bogus, "w") as zf:
            zf.writestr("hello.txt", "hi")

        assert is_live_script(bogus) is False
        assert is_live_script(_make_mlx(tmp_path / "real.mlx")) is True

    def test_unreadable_live_script_raises(self, tmp_path: Path) -> None:
        """convert_matlab() — a corrupt .mlx fails loudly, not silently."""
        broken = tmp_path / "broken.mlx"
        broken.write_bytes(b"not a zip")

        with pytest.raises(MatlabConversionError):
            convert_matlab(broken)

    def test_is_matlab_accepts_both_forms(self, tmp_path: Path) -> None:
        """is_matlab() — .m and .mlx both route to this converter."""
        m = tmp_path / "a.m"
        m.write_text("x=1;", encoding="utf-8")

        assert is_matlab(m) is True
        assert is_matlab(_make_mlx(tmp_path / "b.mlx")) is True
        assert is_matlab(tmp_path / "missing.m") is False
