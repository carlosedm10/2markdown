"""Conversion manifest for resume and failure tracking."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

MANIFEST_FILENAME = ".2markdown-manifest.json"

FileStatus = Literal["ok", "failed", "skipped"]


@dataclass
class FileRecord:
    source: str
    status: FileStatus
    mtime: float | None = None
    error: str | None = None
    output: str | None = None
    ocr_backend: str | None = None


def _file_record_from_raw(raw: dict[str, Any]) -> FileRecord:
    known = {f.name for f in FileRecord.__dataclass_fields__.values()}
    return FileRecord(**{k: v for k, v in raw.items() if k in known})


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.records: dict[str, FileRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, raw in (data.get("files") or {}).items():
                self.records[key] = _file_record_from_raw(raw)
        except (json.JSONDecodeError, TypeError):
            pass

    def save(self) -> None:
        payload: dict[str, Any] = {
            "updated_at": datetime.now(UTC).isoformat(),
            "files": {k: asdict(v) for k, v in self.records.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def record(
        self,
        source: Path,
        *,
        status: FileStatus,
        error: str | None = None,
        output: Path | None = None,
        ocr_backend: str | None = None,
    ) -> None:
        key = str(source.resolve())
        mtime = source.stat().st_mtime if source.exists() else None
        self.records[key] = FileRecord(
            source=key,
            status=status,
            mtime=mtime,
            error=error,
            output=str(output.resolve()) if output else None,
            ocr_backend=ocr_backend,
        )

    def should_skip(
        self,
        source: Path,
        output_md: Path,
        *,
        skip_existing: bool,
        ocr_backend: str | None = None,
    ) -> bool:
        if not skip_existing:
            return False
        if not output_md.exists():
            return False
        key = str(source.resolve())
        record = self.records.get(key)
        if record is not None and record.status == "failed":
            return False
        if (
            ocr_backend is not None
            and record is not None
            and record.ocr_backend is not None
            and record.ocr_backend != ocr_backend
        ):
            return False
        try:
            return source.stat().st_mtime <= output_md.stat().st_mtime
        except OSError:
            return False
