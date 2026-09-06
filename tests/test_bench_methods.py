"""Replay the same inputs under different conversion methods."""

import time
from pathlib import Path

import pytest

from twomarkdown.config import conversion_config
from twomarkdown.telemetry.bench import Method, run_methods

pytestmark = pytest.mark.bench


def test_run_methods_compares_serial_and_parallel(tmp_path: Path) -> None:
    """run_methods() — serial vs parallel workers on mocked converters."""
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    for index in range(8):
        (input_dir / f"doc{index}.txt").write_text(f"body {index}")

    def _slow_convert(_path: Path) -> str:
        time.sleep(0.04)
        return "converted"

    previous_report = conversion_config.write_export_report
    conversion_config.write_export_report = False
    try:
        rows = run_methods(
            input_dir,
            tmp_path / "out",
            [
                Method(name="serial", workers=1),
                Method(name="parallel-4", workers=4),
            ],
            convert=_slow_convert,
        )
    finally:
        conversion_config.write_export_report = previous_report

    by_name = {row["method"]: row for row in rows}
    assert by_name["serial"]["converted"] == 8
    assert by_name["parallel-4"]["converted"] == 8
    assert by_name["serial"]["failed"] == 0
    assert by_name["parallel-4"]["wall_ms"] < by_name["serial"]["wall_ms"]
