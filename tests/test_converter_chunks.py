"""Tests for markdown chunking (src.converter.chunks)."""

import json
from pathlib import Path

from src.converter.chunks import chunk_markdown, write_chunks_sidecar


class TestChunkMarkdown:
    def test_chunk_markdown_splits_on_headings(self) -> None:
        text = "## Alpha\n\nFirst section.\n\n## Beta\n\nSecond section."
        chunks = chunk_markdown(text, max_chars=500, overlap=0)

        assert len(chunks) == 2
        assert chunks[0]["heading"] == "Alpha"
        assert "First section." in chunks[0]["text"]
        assert chunks[1]["heading"] == "Beta"
        assert "Second section." in chunks[1]["text"]
        assert chunks[0]["char_count"] == len(chunks[0]["text"])

    def test_chunk_markdown_keeps_table_intact(self) -> None:
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        text = f"## Data\n\nIntro paragraph.\n\n{table}\n\nAfter table."
        chunks = chunk_markdown(text, max_chars=40, overlap=0)

        combined = "\n".join(chunk["text"] for chunk in chunks)
        assert "| A | B |" in combined
        assert "| --- | --- |" in combined
        assert "| 1 | 2 |" in combined
        for chunk in chunks:
            body = chunk["text"]
            if "| A | B |" in body:
                assert "| 1 | 2 |" in body

    def test_write_chunks_sidecar_writes_json(self, tmp_path: Path) -> None:
        md_path = tmp_path / "doc.md"
        md_path.write_text("# Doc", encoding="utf-8")
        chunks = [{"heading": "Doc", "text": "# Doc", "char_count": 5}]

        sidecar = write_chunks_sidecar(md_path, chunks)

        assert sidecar == md_path.with_suffix(".chunks.json")
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
        assert loaded == chunks
