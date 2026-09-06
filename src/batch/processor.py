"""Batch conversion with soft-fail per file, optional parallelism, and timeouts."""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

from src.batch.manifest import Manifest, file_checksum
from src.batch.ocr_cache import OcrCache
from src.batch.walker import discover_files
from src.config import conversion_config, iwork_config
from src.converter import ereader, iwork, markitdown_converter, ocr, pdf_ocr
from src.converter.markitdown_converter import ConversionError
from src.frontmatter import build_frontmatter

logger = logging.getLogger(__name__)

_OCR_FN_DEFAULT: object = object()


@dataclass
class BatchResult:
    converted: int = 0
    failed: int = 0
    skipped: int = 0
    failed_paths: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)


def _mirror_output_path(source: Path, input_dir: Path, output_dir: Path) -> Path:
    rel = source.resolve().relative_to(input_dir.resolve())
    return output_dir / rel.with_suffix(".md")


def _get_ocr_fn() -> Callable[[bytes], str] | None:
    if not conversion_config.ocr_enabled:
        return None
    if conversion_config.ocr_backend == "ollama":
        from src.agents.image_ocr import ocr_image_bytes_llm

        return ocr_image_bytes_llm
    return ocr.extract_text_with_tesseract


def _cached_ocr_fn(
    inner: Callable[[bytes], str] | None,
    cache: OcrCache | None,
) -> Callable[[bytes], str] | None:
    if inner is None:
        return None
    if cache is None:
        return inner

    def _wrapped(image_bytes: bytes) -> str:
        hit = cache.get(image_bytes)
        if hit is not None:
            return hit
        text = inner(image_bytes)
        cache.put(image_bytes, text)
        return text

    return _wrapped


def _resolve_show_progress(show_progress: bool | None, *, verbose: bool) -> bool:
    if show_progress is not None:
        return show_progress
    return sys.stderr.isatty() and not verbose


def _effective_suffix(path: Path) -> str:
    try:
        from src.converter.filetype import effective_suffix

        return effective_suffix(path)
    except Exception:
        return path.suffix.lower()


def _clean(markdown: str) -> str:
    try:
        from src.converter.clean import clean_markdown

        return clean_markdown(markdown)
    except Exception:
        return markdown


def _compose_pdf(
    markdown: str, source_path: Path, *, show_progress: bool = False
) -> tuple[str, list[int]]:
    ocr_fn = _get_ocr_fn() or ocr.extract_text_with_tesseract
    ocr_pages: list[tuple[int, str]] = []
    if pdf_ocr.should_fallback(markdown, suffix=".pdf", pdf_path=source_path):
        if not show_progress:
            logger.info("Scanned PDF detected, running page OCR: %s", source_path)
        ocr_pages = pdf_ocr.extract_pages(
            source_path, ocr_fn=ocr_fn, show_progress=show_progress
        )

    tables: list[tuple[int, str]] = []
    try:
        from src.converter.tables import extract_pdf_tables

        tables = extract_pdf_tables(source_path)
    except Exception as exc:
        logger.debug("PDF table extract skipped: %s", exc)

    compose = getattr(pdf_ocr, "compose_pdf_markdown", None)
    if callable(compose):
        text = compose(
            pdf_path=source_path,
            markitdown_text=markdown,
            ocr_pages=ocr_pages,
            tables=tables,
        )
    else:
        text = pdf_ocr.merge(markdown, ocr_pages)

    return text, [n for n, _ in ocr_pages]


def _convert_pdf_with_ocr(pdf_path: Path, *, show_progress: bool = False) -> str:
    markdown = markitdown_converter.convert_file(pdf_path)
    composed, _ = _compose_pdf(markdown, pdf_path, show_progress=show_progress)
    return composed


def _maybe_excel(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    if suffix not in {".xlsx", ".xlsm"}:
        return None
    try:
        from src.converter.excel import convert_xlsx

        return convert_xlsx(path)
    except Exception as exc:
        logger.debug("Native Excel conversion failed, MarkItDown fallback: %s", exc)
        return None


def _maybe_eml(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    if suffix != ".eml":
        return None
    try:
        from src.converter.eml import convert_eml

        return convert_eml(path)
    except Exception as exc:
        logger.debug("EML conversion failed: %s", exc)
        raise


def _maybe_legacy_office(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    if suffix not in {".doc", ".ppt"}:
        return None
    try:
        from src.converter.office_legacy import convert_legacy_office

        return convert_legacy_office(path)
    except ImportError as exc:
        raise ConversionError("LibreOffice converter unavailable") from exc


def _convert_source_to_markdown(
    source_path: Path, *, show_progress: bool = False
) -> str:
    suffix = _effective_suffix(source_path)

    if iwork_config.iwork_enabled and iwork.is_iwork_bundle(source_path):
        convert_pdf = None
        if suffix == ".pages":
            convert_pdf = lambda p: _convert_pdf_with_ocr(  # noqa: E731
                p, show_progress=show_progress
            )
        markdown = iwork.convert_bundle(source_path, convert_pdf=convert_pdf)
        embed = getattr(iwork, "embed_images_markdown", None)
        if callable(embed):
            extra = embed(source_path, ocr_fn=_get_ocr_fn())
            if extra and extra.strip():
                markdown = f"{markdown.rstrip()}\n\n{extra.strip()}\n"
        return markdown

    if ereader.is_ereader(source_path):
        return ereader.convert_ereader(source_path)

    native = _maybe_eml(source_path)
    if native is not None:
        return native

    native = _maybe_legacy_office(source_path)
    if native is not None:
        return native

    native = _maybe_excel(source_path)
    if native is not None:
        return native

    markdown = markitdown_converter.convert_file(source_path)
    if suffix == ".pdf":
        markdown, _ = _compose_pdf(markdown, source_path, show_progress=show_progress)
    elif ocr.is_raster_image(source_path):
        if not show_progress:
            logger.info("Raster image OCR: %s", source_path)
        markdown = ocr.convert_image_file(
            source_path,
            ocr_fn=_get_ocr_fn(),
            existing_markdown=markdown,
        )
    return markdown


def _expand_zips(files: list[Path], output_dir: Path) -> list[Path]:
    if not conversion_config.explode_zip:
        return files
    try:
        from src.converter.zip_ingest import extract_zip, is_explodable_zip
    except Exception:
        return files

    expanded: list[Path] = []
    for path in files:
        if not is_explodable_zip(path):
            expanded.append(path)
            continue
        dest = output_dir / ".unzipped" / path.stem
        dest.mkdir(parents=True, exist_ok=True)
        try:
            inner = extract_zip(path, dest)
        except Exception as exc:
            logger.warning("ZIP extract failed for %s: %s", path, exc)
            expanded.append(path)
            continue
        if inner:
            nested = discover_files(dest, output_dir)
            expanded.extend(nested or inner)
        else:
            expanded.append(path)
    return expanded


def _write_chunks(output_md: Path, markdown: str) -> None:
    if not conversion_config.emit_chunks:
        return
    try:
        from src.converter.chunks import chunk_markdown, write_chunks_sidecar

        chunks = chunk_markdown(markdown)
        write_chunks_sidecar(output_md, chunks)
    except Exception as exc:
        logger.debug("Chunk sidecar skipped: %s", exc)


def convert_file_to_markdown(
    source_path: Path,
    *,
    show_progress: bool = False,
    ocr_fn: Callable[[bytes], str] | None | object = _OCR_FN_DEFAULT,
) -> str:
    """Convert one local file to markdown text (no disk write, no frontmatter)."""
    resolved_ocr = _get_ocr_fn() if ocr_fn is _OCR_FN_DEFAULT else ocr_fn
    markdown = _convert_source_to_markdown(source_path, show_progress=show_progress)
    if not markdown or not markdown.strip():
        raise ConversionError("empty result")

    if conversion_config.ocr_enabled:
        markdown = ocr.enrich_markdown_images(
            markdown,
            source_path,
            ocr_fn=resolved_ocr if callable(resolved_ocr) else None,
        )

    return _clean(markdown)


def _convert_one(
    source_path: Path,
    *,
    input_dir: Path,
    output_dir: Path,
    ocr_fn: Callable[[bytes], str] | None,
    show_progress: bool,
) -> tuple[str, int]:
    markdown = convert_file_to_markdown(
        source_path,
        show_progress=show_progress,
        ocr_fn=ocr_fn,
    )

    suffix = _effective_suffix(source_path)
    output_md = _mirror_output_path(source_path, input_dir, output_dir)
    if suffix == ".pdf" and conversion_config.extract_assets:
        try:
            from src.converter.assets import extract_pdf_images, markdown_asset_index

            assets_dir = output_md.parent / f"{output_md.stem}_assets"
            extracted = extract_pdf_images(source_path, assets_dir)
            extra = markdown_asset_index(extracted, output_root=output_md.parent)
            if extra and extra.strip():
                markdown = f"{markdown.rstrip()}\n\n{extra.strip()}\n"
        except Exception as exc:
            logger.debug("PDF asset extract skipped: %s", exc)

    content = build_frontmatter(source_path, input_dir, markdown) + markdown
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(content, encoding="utf-8")
    _write_chunks(output_md, markdown)
    return str(output_md), len(markdown)


def process_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    only_files: list[Path] | None = None,
    skip_existing: bool | None = None,
    ocr_enabled: bool | None = None,
    verbose: bool = False,
    show_progress: bool | None = None,
    dry_run: bool = False,
) -> BatchResult:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    skip_existing = (
        skip_existing if skip_existing is not None else conversion_config.skip_existing
    )
    previous_ocr_enabled = conversion_config.ocr_enabled
    if ocr_enabled is not None:
        conversion_config.ocr_enabled = ocr_enabled

    try:
        if only_files is not None:
            files = sorted(path.resolve() for path in only_files)
        else:
            files = discover_files(input_dir, output_dir)
        files = _expand_zips(files, output_dir)
        manifest_path = output_dir / ".2markdown-manifest.json"
        manifest = Manifest(manifest_path)
        result = BatchResult()
        backend = (
            conversion_config.ocr_backend if conversion_config.ocr_enabled else "none"
        )
        cache = OcrCache(output_dir / ".2markdown-ocr-cache")
        ocr_fn = _cached_ocr_fn(_get_ocr_fn(), cache)
        use_progress = _resolve_show_progress(show_progress, verbose=verbose)
        workers = max(1, conversion_config.parallel_workers)
        timeout = conversion_config.file_timeout_sec

        if dry_run:
            result.planned = [str(p) for p in files]
            return result

        work: list[Path] = []
        for source_path in files:
            output_md = _mirror_output_path(source_path, input_dir, output_dir)
            checksum = file_checksum(source_path)
            if manifest.should_skip(
                source_path,
                output_md,
                skip_existing=skip_existing,
                ocr_backend=backend,
                checksum=checksum,
            ):
                manifest.record(
                    source_path,
                    status="skipped",
                    output=output_md,
                    ocr_backend=backend,
                    checksum=checksum,
                )
                result.skipped += 1
                if verbose:
                    logger.info("Skipped (up to date): %s", source_path)
                continue
            work.append(source_path)

        def _handle(source_path: Path) -> tuple[Path, str, int | None, int, str | None]:
            started = time.perf_counter()
            try:
                _out, chars = _convert_one(
                    source_path,
                    input_dir=input_dir,
                    output_dir=output_dir,
                    ocr_fn=ocr_fn,
                    show_progress=use_progress and workers == 1,
                )
                duration_ms = int((time.perf_counter() - started) * 1000)
                return source_path, "ok", duration_ms, chars, None
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                return source_path, "failed", duration_ms, 0, str(exc)

        def _consume(item: tuple[Path, str, int | None, int, str | None]) -> None:
            source_path, status, duration_ms, chars, error = item
            checksum = file_checksum(source_path)
            output_md = _mirror_output_path(source_path, input_dir, output_dir)
            if status == "ok":
                manifest.record(
                    source_path,
                    status="ok",
                    output=output_md,
                    ocr_backend=backend,
                    checksum=checksum,
                    duration_ms=duration_ms,
                    char_count=chars,
                )
                result.converted += 1
                if verbose:
                    logger.info("Converted: %s", source_path)
                return
            if use_progress:
                tqdm.write(f"Failed to convert {source_path}: {error}")
            else:
                logger.warning("Failed to convert %s: %s", source_path, error)
            manifest.record(
                source_path,
                status="failed",
                error=error,
                ocr_backend=backend,
                checksum=checksum,
                duration_ms=duration_ms,
            )
            result.failed += 1
            result.failed_paths.append(str(source_path))

        if workers == 1:
            with tqdm(
                work,
                desc="Converting",
                unit="file",
                disable=not use_progress,
            ) as pbar:
                for source_path in pbar:
                    if use_progress:
                        pbar.set_postfix_str(source_path.name, refresh=False)
                    if timeout:
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            future = pool.submit(_handle, source_path)
                            try:
                                _consume(future.result(timeout=timeout))
                            except FuturesTimeout:
                                _consume(
                                    (
                                        source_path,
                                        "failed",
                                        int(timeout * 1000),
                                        0,
                                        f"timeout after {timeout}s",
                                    )
                                )
                    else:
                        _consume(_handle(source_path))
        else:
            from concurrent.futures import as_completed

            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {
                    pool.submit(_handle, source_path): source_path
                    for source_path in work
                }
                with tqdm(
                    total=len(future_map),
                    desc="Converting",
                    unit="file",
                    disable=not use_progress,
                ) as pbar:
                    for future in as_completed(future_map):
                        source_path = future_map[future]
                        try:
                            item = future.result(timeout=timeout)
                        except FuturesTimeout:
                            item = (
                                source_path,
                                "failed",
                                int((timeout or 0) * 1000),
                                0,
                                f"timeout after {timeout}s",
                            )
                        except Exception as exc:
                            item = (source_path, "failed", None, 0, str(exc))
                        _consume(item)
                        pbar.update(1)

        manifest.save()
        return result
    finally:
        conversion_config.ocr_enabled = previous_ocr_enabled
