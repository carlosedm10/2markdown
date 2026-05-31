"""Sequential batch conversion with soft-fail per file."""

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tqdm import tqdm

from src.config import conversion_config, iwork_config, llm_config
from src.converter import iwork, markitdown_converter, ocr, pdf_ocr
from src.converter.markitdown_converter import ConversionError

from .manifest import Manifest
from .walker import discover_files

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    converted: int = 0
    failed: int = 0
    skipped: int = 0
    failed_paths: list[str] | None = None

    def __post_init__(self) -> None:
        if self.failed_paths is None:
            self.failed_paths = []


def _mirror_output_path(source: Path, input_dir: Path, output_dir: Path) -> Path:
    rel = source.resolve().relative_to(input_dir.resolve())
    return output_dir / rel.with_suffix(".md")


def _build_frontmatter(source: Path, input_dir: Path) -> str:
    rel = source.resolve().relative_to(input_dir.resolve())
    now = datetime.now(UTC).isoformat()
    backend = conversion_config.ocr_backend if conversion_config.ocr_enabled else "none"
    return (
        f"---\n"
        f"source: {rel.as_posix()}\n"
        f"converted_at: {now}\n"
        f"ocr_backend: {backend}\n"
        f"---\n\n"
    )


def _get_ocr_fn() -> Callable[[bytes], str] | None:
    if not conversion_config.ocr_enabled:
        return None
    if conversion_config.ocr_backend == "ollama" and llm_config.llm_enabled:
        from src.agents.image_ocr import ocr_image_bytes_llm

        return ocr_image_bytes_llm
    if conversion_config.ocr_backend == "tesseract":
        return ocr.extract_text_with_tesseract
    return ocr.extract_text_with_tesseract


def _resolve_show_progress(show_progress: bool | None, *, verbose: bool) -> bool:
    if show_progress is not None:
        return show_progress
    return sys.stderr.isatty() and not verbose


def _apply_pdf_fallback(
    markdown: str, source_path: Path, *, show_progress: bool = False
) -> str:
    if not pdf_ocr.should_fallback(markdown, suffix=source_path.suffix):
        return markdown

    if not show_progress:
        logger.info("Scanned PDF detected, running page OCR: %s", source_path)
    ocr_fn = _get_ocr_fn() or ocr.extract_text_with_tesseract
    pages = pdf_ocr.extract_pages(
        source_path, ocr_fn=ocr_fn, show_progress=show_progress
    )
    return pdf_ocr.merge(markdown, pages)


def _convert_pdf_with_ocr(
    pdf_path: Path, *, show_progress: bool = False
) -> str:
    markdown = markitdown_converter.convert_file(pdf_path)
    return _apply_pdf_fallback(markdown, pdf_path, show_progress=show_progress)


def _convert_source_to_markdown(
    source_path: Path, *, show_progress: bool = False
) -> str:
    if iwork_config.iwork_enabled and iwork.is_iwork_bundle(source_path):
        convert_pdf = None
        if source_path.suffix.lower() == ".pages":
            convert_pdf = lambda p: _convert_pdf_with_ocr(  # noqa: E731
                p, show_progress=show_progress
            )
        return iwork.convert_bundle(source_path, convert_pdf=convert_pdf)

    markdown = markitdown_converter.convert_file(source_path)
    if source_path.suffix.lower() == ".pdf":
        markdown = _apply_pdf_fallback(
            markdown, source_path, show_progress=show_progress
        )
    return markdown


def process_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    only_files: list[Path] | None = None,
    skip_existing: bool | None = None,
    ocr_enabled: bool | None = None,
    verbose: bool = False,
    show_progress: bool | None = None,
) -> BatchResult:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    skip_existing = (
        skip_existing if skip_existing is not None else conversion_config.skip_existing
    )
    if ocr_enabled is not None:
        conversion_config.ocr_enabled = ocr_enabled

    if only_files is not None:
        files = sorted(path.resolve() for path in only_files)
    else:
        files = discover_files(input_dir, output_dir)
    total = len(files)
    manifest_path = output_dir / ".2markdown-manifest.json"
    manifest = Manifest(manifest_path)
    result = BatchResult()
    ocr_fn = _get_ocr_fn()
    use_progress = _resolve_show_progress(show_progress, verbose=verbose)

    with tqdm(
        files,
        desc="Converting",
        unit="file",
        disable=not use_progress,
    ) as pbar:
        for i, source_path in enumerate(pbar, 1):
            output_md = _mirror_output_path(source_path, input_dir, output_dir)

            if use_progress:
                pbar.set_postfix_str(source_path.name, refresh=False)

            if manifest.should_skip(
                source_path, output_md, skip_existing=skip_existing
            ):
                manifest.record(source_path, status="skipped", output=output_md)
                result.skipped += 1
                if verbose:
                    logger.info(
                        "[%s/%s] Skipped (up to date): %s", i, total, source_path
                    )
                continue

            try:
                markdown = _convert_source_to_markdown(
                    source_path, show_progress=use_progress
                )

                if not markdown or not markdown.strip():
                    raise ConversionError("empty result")

                if conversion_config.ocr_enabled:
                    markdown = ocr.enrich_markdown_images(
                        markdown,
                        source_path,
                        ocr_fn=ocr_fn,
                    )

                content = _build_frontmatter(source_path, input_dir) + markdown
                output_md.parent.mkdir(parents=True, exist_ok=True)
                output_md.write_text(content, encoding="utf-8")

                manifest.record(source_path, status="ok", output=output_md)
                result.converted += 1
                if verbose:
                    logger.info("[%s/%s] Converted: %s", i, total, source_path)

            except Exception as exc:
                if use_progress:
                    tqdm.write(
                        f"Failed to convert {source_path} ({i}/{total}): {exc}"
                    )
                else:
                    logger.warning(
                        "Failed to convert %s (%s/%s): %s",
                        source_path,
                        i,
                        total,
                        exc,
                    )
                manifest.record(source_path, status="failed", error=str(exc))
                result.failed += 1
                if result.failed_paths is not None:
                    result.failed_paths.append(str(source_path))

    manifest.save()
    return result
