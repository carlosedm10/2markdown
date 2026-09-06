"""Tests for .eml conversion (twomarkdown.converter.eml)."""

from pathlib import Path

from twomarkdown.converter.eml import convert_eml, is_email


def _write_sample_eml(path: Path) -> None:
    path.write_text(
        "From: Alice <alice@example.com>\r\n"
        "To: Bob <bob@example.com>\r\n"
        "Subject: Hello\r\n"
        "Date: Sun, 6 Sep 2026 09:00:00 +0000\r\n"
        "MIME-Version: 1.0\r\n"
        "Content-Type: multipart/mixed; boundary=boundary42\r\n"
        "\r\n"
        "--boundary42\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "Plain body text.\r\n"
        "\r\n"
        "--boundary42\r\n"
        "Content-Type: application/octet-stream\r\n"
        'Content-Disposition: attachment; filename="notes.txt"\r\n'
        "\r\n"
        "binary data\r\n"
        "\r\n"
        "--boundary42--\r\n",
        encoding="utf-8",
    )


class TestEmlConverter:
    def test_is_email_true_for_eml_false_for_msg(self, tmp_path: Path) -> None:
        assert is_email(tmp_path / "mail.eml") is True
        assert is_email(tmp_path / "mail.msg") is False

    def test_convert_eml_headers_body_and_attachments(self, tmp_path: Path) -> None:
        eml_path = tmp_path / "mail.eml"
        _write_sample_eml(eml_path)

        markdown = convert_eml(eml_path)

        assert "From: Alice <alice@example.com>" in markdown
        assert "To: Bob <bob@example.com>" in markdown
        assert "Subject: Hello" in markdown
        assert "Date: Sun, 06 Sep 2026" in markdown
        assert "Plain body text." in markdown
        assert "## Attachments" in markdown
        assert "- notes.txt" in markdown
        assert "binary data" not in markdown

    def test_convert_eml_html_only_strips_tags(self, tmp_path: Path) -> None:
        eml_path = tmp_path / "html.eml"
        eml_path.write_text(
            "From: web@example.com\r\n"
            "To: user@example.com\r\n"
            "Subject: HTML\r\n"
            "Date: Sun, 6 Sep 2026 09:00:00 +0000\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "\r\n"
            "<html><body><p>Hello <b>world</b></p></body></html>\r\n",
            encoding="utf-8",
        )

        markdown = convert_eml(eml_path)

        assert "Hello world" in markdown
        assert "<p>" not in markdown
