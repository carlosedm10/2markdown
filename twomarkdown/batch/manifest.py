"""Conversion manifest for resume and failure tracking."""

from __future__ import annotations

import hashlib
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
    checksum: str | None = None
    duration_ms: int | None = None
    char_count: int | None = None


def _hash_file_bytes(path: Path, hasher: Any, *, remaining: int) -> int:
    with path.open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            hasher.update(chunk)
            remaining -= len(chunk)
    return remaining


def _dir_checksum(path: Path, *, max_bytes: int) -> str:
    hasher = hashlib.sha256()
    remaining = max_bytes
    files = sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file()
        and not any(part.startswith(".") for part in candidate.relative_to(path).parts)
    )
    for file_path in files:
        rel = file_path.relative_to(path).as_posix().encode()
        hasher.update(rel)
        hasher.update(b"\0")
        remaining = _hash_file_bytes(file_path, hasher, remaining=remaining)
        hasher.update(b"\n")
        if remaining <= 0:
            break
    return hasher.hexdigest()


def file_checksum(path: Path, *, max_bytes: int = 32_000_000) -> str | None:
    """SHA-256 of a file, or of a directory tree (relative paths + contents)."""
    try:
        resolved = path.resolve()
        if resolved.is_dir():
            return _dir_checksum(resolved, max_bytes=max_bytes)
        hasher = hashlib.sha256()
        _hash_file_bytes(resolved, hasher, remaining=max_bytes)
        return hasher.hexdigest()
    except OSError:
        return None


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
        checksum: str | None = None,
        duration_ms: int | None = None,
        char_count: int | None = None,
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
            checksum=checksum,
            duration_ms=duration_ms,
            char_count=char_count,
        )
        self.save()

    def should_skip(
        self,
        source: Path,
        output_md: Path,
        *,
        skip_existing: bool,
        ocr_backend: str | None = None,
        checksum: str | None = None,
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
        if checksum is not None and record is not None and record.checksum is not None:
            if record.checksum != checksum:
                return False
            # Content is provably identical, so skip regardless of timestamps.
            # iCloud Drive rewrites mtime when it syncs or re-downloads a file; the
            # old mtime gate then re-ran hours of vision OCR on unchanged sources.
            return True
        try:
            return source.stat().st_mtime <= output_md.stat().st_mtime
        except OSError:
            return False
