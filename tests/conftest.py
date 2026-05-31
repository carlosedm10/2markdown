"""Shared pytest fixtures (Arrange helpers for batch/converter tests)."""

from pathlib import Path

import pytest

# Minimal 1x1 PNG for image/OCR tests
MINIMAL_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00"
    b"\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def batch_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Empty input/output directory pair for batch processor tests."""
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    return input_dir, output_dir


@pytest.fixture
def nested_batch_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Input tree with output nested under input (walker skip scenarios)."""
    input_dir = tmp_path / "data"
    output_dir = input_dir / "out"
    input_dir.mkdir()
    output_dir.mkdir()
    return input_dir, output_dir


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def minimal_png_bytes() -> bytes:
    return MINIMAL_PNG_BYTES
