"""In-process conversion API (import as `twomarkdown`, not `2markdown`)."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from src.batch.processor import BatchResult, convert_file_to_markdown, process_batch
from src.cli import resolve_ocr_backend
from src.config import conversion_config, llm_config, pdf_ocr_config
from src.paths import default_output_dir, normalize_batch_input

Source = str | Path | bytes
OcrBackend = Literal["tesseract", "ollama"]


@contextmanager
def conversion_options(
    *,
    ocr: bool | None = None,
    ollama: bool = False,
    ocr_backend: OcrBackend | None = None,
    llm_enabled: bool | None = None,
    pdf_ocr: bool | None = None,
    fetch_remote_images: bool | None = None,
    skip_existing: bool | None = None,
    workers: int | None = None,
    emit_chunks: bool | None = None,
    clean: bool | None = None,
    tables: bool | None = None,
    describe_figures: bool | None = None,
) -> Iterator[None]:
    """Temporarily overlay conversion settings; restore on exit."""
    previous = (
        conversion_config.ocr_enabled,
        conversion_config.ocr_backend,
        conversion_config.fetch_remote_images,
        conversion_config.skip_existing,
        conversion_config.parallel_workers,
        conversion_config.emit_chunks,
        conversion_config.clean_markdown,
        conversion_config.extract_tables,
        conversion_config.describe_figures,
        llm_config.llm_enabled,
        pdf_ocr_config.pdf_ocr_enabled,
    )
    try:
        if ocr is not None:
            conversion_config.ocr_enabled = ocr
        if ocr_backend is not None or ollama:
            backend, llm = resolve_ocr_backend(
                ollama=ollama,
                ocr_backend=ocr_backend or conversion_config.ocr_backend,
                llm_enabled=(
                    llm_enabled if llm_enabled is not None else llm_config.llm_enabled
                ),
            )
            conversion_config.ocr_backend = backend
            llm_config.llm_enabled = llm
        elif llm_enabled is not None:
            llm_config.llm_enabled = llm_enabled
        if pdf_ocr is not None:
            pdf_ocr_config.pdf_ocr_enabled = pdf_ocr
        if fetch_remote_images is not None:
            conversion_config.fetch_remote_images = fetch_remote_images
        if skip_existing is not None:
            conversion_config.skip_existing = skip_existing
        if workers is not None:
            conversion_config.parallel_workers = max(1, workers)
        if emit_chunks is not None:
            conversion_config.emit_chunks = emit_chunks
        if clean is not None:
            conversion_config.clean_markdown = clean
        if tables is not None:
            conversion_config.extract_tables = tables
        if describe_figures is not None:
            conversion_config.describe_figures = describe_figures
        yield
    finally:
        (
            conversion_config.ocr_enabled,
            conversion_config.ocr_backend,
            conversion_config.fetch_remote_images,
            conversion_config.skip_existing,
            conversion_config.parallel_workers,
            conversion_config.emit_chunks,
            conversion_config.clean_markdown,
            conversion_config.extract_tables,
            conversion_config.describe_figures,
            llm_config.llm_enabled,
            pdf_ocr_config.pdf_ocr_enabled,
        ) = previous


def _bytes_to_temp_path(
    data: bytes, *, suffix: str | None, filename: str | None
) -> Path:
    ext = suffix or (Path(filename).suffix if filename else "")
    if not ext:
        raise ValueError("bytes source requires suffix= or filename= with an extension")
    if not ext.startswith("."):
        ext = f".{ext}"
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp.write(data)
    tmp.close()
    return Path(tmp.name)


def convert_batch(
    source: str | Path,
    output: str | Path | None = None,
    *,
    ocr: bool | None = None,
    ollama: bool = False,
    ocr_backend: OcrBackend | None = None,
    llm_enabled: bool | None = None,
    pdf_ocr: bool | None = None,
    fetch_remote_images: bool | None = None,
    skip_existing: bool | None = None,
    workers: int | None = None,
    emit_chunks: bool | None = None,
    clean: bool | None = None,
    tables: bool | None = None,
    describe_figures: bool | None = None,
    verbose: bool = False,
    show_progress: bool | None = None,
    dry_run: bool = False,
) -> BatchResult:
    """Walk a file or folder and write Markdown under `output`.

    Default output is a sibling `*_2markdown` directory.
    """
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(path)
    batch_root, default_out, only_files = normalize_batch_input(path)
    resolved_output = Path(output).resolve() if output is not None else default_out
    with conversion_options(
        ocr=ocr,
        ollama=ollama,
        ocr_backend=ocr_backend,
        llm_enabled=llm_enabled,
        pdf_ocr=pdf_ocr,
        fetch_remote_images=fetch_remote_images,
        skip_existing=skip_existing,
        workers=workers,
        emit_chunks=emit_chunks,
        clean=clean,
        tables=tables,
        describe_figures=describe_figures,
    ):
        conversion_config.input_dir = batch_root
        conversion_config.output_dir = resolved_output
        resolved_output.mkdir(parents=True, exist_ok=True)
        return process_batch(
            batch_root,
            resolved_output,
            only_files=only_files,
            skip_existing=skip_existing,
            ocr_enabled=ocr,
            verbose=verbose,
            show_progress=show_progress,
            dry_run=dry_run,
        )


def convert_file(
    source: Source,
    *,
    suffix: str | None = None,
    filename: str | None = None,
    ocr: bool | None = None,
    ollama: bool = False,
    ocr_backend: OcrBackend | None = None,
    llm_enabled: bool | None = None,
    pdf_ocr: bool | None = None,
    fetch_remote_images: bool | None = None,
    clean: bool | None = None,
    tables: bool | None = None,
    describe_figures: bool | None = None,
) -> str:
    """Convert one file (path or bytes) to Markdown and return the body."""
    cleanup: Path | None = None
    if isinstance(source, bytes):
        path = _bytes_to_temp_path(source, suffix=suffix, filename=filename)
        cleanup = path
    else:
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(path)

    try:
        with conversion_options(
            ocr=ocr,
            ollama=ollama,
            ocr_backend=ocr_backend,
            llm_enabled=llm_enabled,
            pdf_ocr=pdf_ocr,
            fetch_remote_images=fetch_remote_images,
            clean=clean,
            tables=tables,
            describe_figures=describe_figures,
        ):
            return convert_file_to_markdown(path)
    finally:
        if cleanup is not None:
            cleanup.unlink(missing_ok=True)


def convert(
    source: Source,
    /,
    output: str | Path | None = None,
    *,
    suffix: str | None = None,
    filename: str | None = None,
    ocr: bool | None = None,
    ollama: bool = False,
    ocr_backend: OcrBackend | None = None,
    llm_enabled: bool | None = None,
    pdf_ocr: bool | None = None,
    fetch_remote_images: bool | None = None,
    skip_existing: bool | None = None,
    workers: int | None = None,
    emit_chunks: bool | None = None,
    clean: bool | None = None,
    tables: bool | None = None,
    describe_figures: bool | None = None,
    verbose: bool = False,
    show_progress: bool | None = None,
    dry_run: bool = False,
) -> str | BatchResult:
    """Convert `source` to Markdown.

    A file path or bytes returns the markdown string. A directory runs a batch
    and returns ``BatchResult`` (writes a sibling ``*_2markdown`` folder unless
    ``output`` is set). Pass ``output`` with a file to use the batch writer
    (manifest, frontmatter, assets).
    """
    if isinstance(source, bytes):
        if output is not None:
            raise ValueError("bytes source cannot write a batch output; omit output=")
        return convert_file(
            source,
            suffix=suffix,
            filename=filename,
            ocr=ocr,
            ollama=ollama,
            ocr_backend=ocr_backend,
            llm_enabled=llm_enabled,
            pdf_ocr=pdf_ocr,
            fetch_remote_images=fetch_remote_images,
            clean=clean,
            tables=tables,
            describe_figures=describe_figures,
        )

    path = Path(source)
    if path.is_dir() or output is not None:
        result = convert_batch(
            path,
            output,
            ocr=ocr,
            ollama=ollama,
            ocr_backend=ocr_backend,
            llm_enabled=llm_enabled,
            pdf_ocr=pdf_ocr,
            fetch_remote_images=fetch_remote_images,
            skip_existing=skip_existing,
            workers=workers,
            emit_chunks=emit_chunks,
            clean=clean,
            tables=tables,
            describe_figures=describe_figures,
            verbose=verbose,
            show_progress=show_progress,
            dry_run=dry_run,
        )
        if path.is_file() and not dry_run:
            out_dir = (
                Path(output).resolve()
                if output is not None
                else default_output_dir(path)
            )
            written = out_dir / f"{path.stem}.md"
            if written.is_file():
                return written.read_text(encoding="utf-8")
        return result
    return convert_file(
        path,
        suffix=suffix,
        filename=filename,
        ocr=ocr,
        ollama=ollama,
        ocr_backend=ocr_backend,
        llm_enabled=llm_enabled,
        pdf_ocr=pdf_ocr,
        fetch_remote_images=fetch_remote_images,
        clean=clean,
        tables=tables,
        describe_figures=describe_figures,
    )
