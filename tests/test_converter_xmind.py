"""XMind mind maps convert to nested Markdown lists."""

import json
import zipfile
from pathlib import Path

import pytest

from twomarkdown.converter.xmind import convert_xmind, is_xmind

_CONTENT = [
    {
        "class": "sheet",
        "title": "Mapa 1",
        "rootTopic": {
            "title": "GESTION DE RRHH",
            "children": {
                "attached": [
                    {
                        "title": "Retribución",
                        "children": {
                            "attached": [
                                {"title": "Dineraria"},
                                {"title": "En especie"},
                            ]
                        },
                    },
                    {"title": "Selección"},
                ]
            },
        },
    }
]


def _make_xmind(path: Path, content: object = _CONTENT) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("content.json", json.dumps(content))
        zf.writestr("metadata.json", "{}")
    return path


class TestConvertXmind:
    def test_renders_nested_topics(self, tmp_path: Path) -> None:
        """convert_xmind() — the topic tree becomes an indented Markdown list."""
        out = convert_xmind(_make_xmind(tmp_path / "esquema.xmind"))
        assert "## GESTION DE RRHH" in out
        assert "- Retribución" in out
        assert "  - Dineraria" in out
        assert "- Selección" in out

    def test_is_xmind_matches_suffix(self, tmp_path: Path) -> None:
        """is_xmind() — only existing .xmind files match."""
        path = _make_xmind(tmp_path / "e.xmind")
        assert is_xmind(path) is True
        assert is_xmind(tmp_path / "missing.xmind") is False

    def test_rejects_a_non_xmind_zip(self, tmp_path: Path) -> None:
        """convert_xmind() — a zip without content.json raises ValueError."""
        path = tmp_path / "plain.xmind"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "hi")
        with pytest.raises(ValueError):
            convert_xmind(path)

    def test_rejects_empty_topic_tree(self, tmp_path: Path) -> None:
        """convert_xmind() — a sheet with no topics raises instead of emitting."""
        path = _make_xmind(tmp_path / "empty.xmind", [{"rootTopic": {"title": "Solo"}}])
        with pytest.raises(ValueError):
            convert_xmind(path)
