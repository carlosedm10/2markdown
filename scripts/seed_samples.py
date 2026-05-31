#!/usr/bin/env python3
"""Create sample input files under data/in for manual conversion tests."""

from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "in"


def write_html() -> None:
    path = INPUT / "samples" / "guide.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """<!DOCTYPE html>
<html>
<head><title>2markdown Guide</title></head>
<body>
  <h1>Getting started</h1>
  <p>Place documents in <code>data/in</code> and run the converter.</p>
  <h2>Supported formats</h2>
  <ul>
    <li>PDF, DOCX, PPTX, XLSX</li>
    <li>HTML, CSV, JSON, images</li>
  </ul>
  <img src="diagram.png" alt="diagram placeholder" />
</body>
</html>
""",
        encoding="utf-8",
    )


def write_csv() -> None:
    path = INPUT / "samples" / "metrics.csv"
    path.write_text(
        "month,revenue,users\n"
        "2026-01,12000,340\n"
        "2026-02,14500,410\n"
        "2026-03,16200,455\n",
        encoding="utf-8",
    )


def write_text() -> None:
    path = INPUT / "notes" / "meeting-notes.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Meeting notes — 2026-05-31\n\n"
        "- Batch conversion uses MarkItDown\n"
        "- OCR defaults to Tesseract (local)\n"
        "- Scanned PDFs trigger page OCR fallback\n",
        encoding="utf-8",
    )


def write_text_pdf() -> None:
    """PDF with selectable text (MarkItDown path)."""
    path = INPUT / "samples" / "report-text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Quarterly report\n\nRevenue grew 12% year over year.\n",
        fontsize=12,
    )
    doc.save(path)
    doc.close()


def write_scanned_pdf() -> None:
    """Image-only PDF to exercise page OCR fallback."""
    path = INPUT / "samples" / "report-scanned.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Render text to a pixmap and insert as image (no text layer)
    text_pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 120), False)
    text_pix.clear_with(255)
    page.insert_text((0, 0), "Scanned line one\nScanned line two", fontsize=14)
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.IRect(50, 50, 450, 200))
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(50, 50, 450, 250), pixmap=pix)
    doc.save(path)
    doc.close()


def main() -> None:
    INPUT.mkdir(parents=True, exist_ok=True)
    write_html()
    write_csv()
    write_text()
    write_text_pdf()
    write_scanned_pdf()
    print(f"Sample files written under {INPUT}")


if __name__ == "__main__":
    main()
