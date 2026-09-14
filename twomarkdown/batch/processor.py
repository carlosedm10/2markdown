"""Batch conversion with soft-fail per file, optional parallelism, and timeouts."""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path

import fitz
from tqdm import tqdm

from twomarkdown.batch import events, gpu_memory
from twomarkdown.batch.manifest import Manifest, file_checksum
from twomarkdown.batch.ocr_cache import OcrCache
from twomarkdown.batch.walker import discover_files
from twomarkdown.config import (
    SKIP_DIR_NAMES,
    conversion_config,
    figure_config,
    iwork_config,
    llm_config,
)
from twomarkdown.converter import ereader, iwork, markitdown_converter, ocr, pdf_ocr
from twomarkdown.converter.markitdown_converter import ConversionError
from twomarkdown.frontmatter import build_frontmatter
from twomarkdown.telemetry import (
    begin_batch,
    begin_file,
    end_batch,
    end_file,
    note,
    set_converter,
    span,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchResult:
    converted: int = 0
    failed: int = 0
    skipped: int = 0
    # A file that converted but had a real LLM-backed stage silently skipped
    # (an unreachable local model, an exhausted cloud quota — see
    # converter.ocr.record_soft_failure) counts in both `converted` and here,
    # never in `failed`: the output exists, it is just not what was asked for.
    warn: int = 0
    failed_paths: list[str] = field(default_factory=list)
    warn_paths: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)


def _source_relpath(source: Path, input_dir: Path, output_dir: Path) -> Path:
    """Path of source relative to the input tree, or to `.unzipped/` staging."""
    source = source.resolve()
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    unzipped_root = (output_dir / ".unzipped").resolve()
    for root in (input_dir, unzipped_root, output_dir):
        try:
            return source.relative_to(root)
        except ValueError:
            continue
    return Path(source.name)


def _mirror_output_path(
    source: Path,
    input_dir: Path,
    output_dir: Path,
    *,
    disambiguate: bool = False,
) -> Path:
    """Output path for a source file, mirroring the input tree.

    ``disambiguate`` keeps the original extension in the name. Study folders
    routinely hold "Seminario 1.pages" beside its own "Seminario 1.pdf"; both
    map to "Seminario 1.md", so without this one silently overwrites the other
    and which one survives depends on worker timing.
    """
    rel = _source_relpath(source, input_dir, output_dir)
    if disambiguate:
        suffix = source.suffix.lower().lstrip(".")
        if suffix:
            return output_dir / rel.with_name(f"{rel.stem}.{suffix}.md")
    return output_dir / rel.with_suffix(".md")


def plan_output_paths(
    files: list[Path], input_dir: Path, output_dir: Path
) -> dict[Path, Path]:
    """Map every source to a unique output path, disambiguating stem collisions.

    Only the files that actually collide get the extension in their name, so the
    common case keeps the clean "Tema 1.md".
    """
    grouped: dict[Path, list[Path]] = {}
    for source in files:
        plain = _mirror_output_path(source, input_dir, output_dir)
        grouped.setdefault(plain, []).append(source)

    planned: dict[Path, Path] = {}
    for plain, sources in grouped.items():
        if len(sources) == 1:
            planned[sources[0]] = plain
            continue
        for source in sources:
            planned[source] = _mirror_output_path(
                source, input_dir, output_dir, disambiguate=True
            )
    return planned


def _unzip_dest(zip_path: Path, input_dir: Path, output_dir: Path) -> Path:
    unzipped_root = output_dir / ".unzipped"
    rel = _source_relpath(zip_path, input_dir, output_dir)
    return unzipped_root / rel.with_suffix("")


def _filter_exploded(paths: list[Path], dest: Path) -> list[Path]:
    dest = dest.resolve()
    extensions = conversion_config.include_extensions
    kept: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        try:
            rel = resolved.relative_to(dest)
        except ValueError:
            rel = Path(path.name)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(part in SKIP_DIR_NAMES for part in rel.parts):
            continue
        suffix = _effective_suffix(resolved)
        if suffix not in extensions:
            continue
        if suffix == ".md" and not conversion_config.convert_existing_md:
            continue
        kept.append(resolved)
    return kept


def _get_ocr_fn() -> Callable[[bytes], str] | None:
    if not conversion_config.ocr_enabled:
        return None
    if conversion_config.ocr_backend == "ollama":
        from twomarkdown.agents.image_ocr import ocr_image_bytes_llm

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
            note("ocr.cache_hit")
            return hit
        text = inner(image_bytes)
        cache.put(image_bytes, text)
        return text

    return _wrapped


def effective_workers() -> int:
    """How many files to convert at once, given what actually limits the batch.

    A local vision model serves one request at a time, so extra workers never do
    extra work — they queue on the same GPU permit while their own per-file clock
    runs. With four workers on 8 handwritten PDFs every file hit the 600s timeout
    without finishing a page, because three of the four were always waiting. When
    the model is local the GPU is the batch, so one worker feeds it.
    """
    configured = max(1, conversion_config.parallel_workers)
    if not (conversion_config.ocr_enabled and llm_config.llm_enabled):
        return configured
    try:
        from twomarkdown.agents.image_ocr import is_local_model

        if is_local_model(llm_config.vision_model):
            return 1
    except Exception:
        pass
    return configured


def _warn_once_if_figure_model_collapses() -> None:
    """Log `gpu_memory.effective_figure_model`'s collapse message once for
    this call, from the config already applied by the caller, rather than
    leaving it to be discovered once per figure (`agents.image_ocr.
    effective_figure_model()` asks the same question again for every figure
    it captions).

    Only for a caller with no job sink attached (the CLI: one `process_batch`
    call is its whole run). `twomarkdown.server.jobs._run_job` calls
    `process_batch` once per *file*, not once per job (see that module's own
    docstring), so logging here as well would print the same message once
    per file instead of once per job — `jobs.py` already logs it exactly
    once, at job start, before its per-file loop begins, so this stays
    silent whenever a sink is attached.
    """
    if events.has_sink():
        return
    if not (figure_config.describe_figures_llm and llm_config.figure_model):
        return
    review_model = llm_config.review_model or None
    _, message = gpu_memory.effective_figure_model(
        llm_config.vision_model, llm_config.figure_model, review_model
    )
    if message:
        logger.warning(message)
        events.log("warning", message)


def _resolve_show_progress(show_progress: bool | None, *, verbose: bool) -> bool:
    if show_progress is not None:
        return show_progress
    return sys.stderr.isatty() and not verbose


def _effective_suffix(path: Path) -> str:
    try:
        from twomarkdown.converter.filetype import effective_suffix

        return effective_suffix(path)
    except Exception:
        return path.suffix.lower()


def _clean(markdown: str) -> str:
    try:
        from twomarkdown.converter.clean import clean_markdown

        return clean_markdown(markdown)
    except Exception:
        return markdown


def _compose_pdf(
    markdown: str,
    source_path: Path,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
    show_progress: bool = False,
    doc: fitz.Document | None = None,
    cancel: threading.Event | None = None,
    file_index: int | None = None,
) -> tuple[str, list[int]]:
    _cancelled(cancel)
    engine = ocr_fn or _get_ocr_fn() or ocr.extract_text_with_tesseract
    ocr_pages: list[tuple[int, str]] = []
    close = False
    opened = doc
    if opened is None:
        opened = fitz.open(source_path)
        close = True
    try:
        if pdf_ocr.should_fallback(
            markdown, suffix=".pdf", pdf_path=source_path, doc=opened
        ):
            if not show_progress:
                logger.info("Scanned PDF detected, running page OCR: %s", source_path)
            with span("pdf.ocr_pages"):
                ocr_pages = pdf_ocr.extract_pages(
                    source_path,
                    ocr_fn=engine,
                    show_progress=show_progress,
                    doc=opened,
                    cancel=cancel,
                    file_index=file_index,
                )
        _cancelled(cancel)

        tables: list[tuple[int, str]] = []
        try:
            from twomarkdown.converter.tables import extract_pdf_tables

            with span("pdf.tables"):
                tables = extract_pdf_tables(source_path, doc=opened)
        except Exception as exc:
            logger.debug("PDF table extract skipped: %s", exc)

        with span("pdf.compose"):
            text = pdf_ocr.compose_pdf_markdown(
                pdf_path=source_path,
                markitdown_text=markdown,
                ocr_pages=ocr_pages,
                tables=tables,
                doc=opened,
            )
        return text, [n for n, _ in ocr_pages]
    except ConversionError:
        raise
    except Exception as exc:
        logger.warning("PDF compose failed for %s: %s", source_path, exc)
        return markdown, []
    finally:
        if close and opened is not None:
            opened.close()


def _convert_pdf_with_ocr(
    pdf_path: Path,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
    show_progress: bool = False,
    cancel: threading.Event | None = None,
    file_index: int | None = None,
) -> str:
    markdown = markitdown_converter.convert_file(pdf_path)
    composed, _ = _compose_pdf(
        markdown,
        pdf_path,
        ocr_fn=ocr_fn,
        show_progress=show_progress,
        cancel=cancel,
        file_index=file_index,
    )
    return composed


def _maybe_numbers(path: Path) -> str | None:
    if _effective_suffix(path) != ".numbers":
        return None
    from twomarkdown.converter import numbers_doc

    if not numbers_doc.is_numbers_doc(path):
        return None
    try:
        with span("numbers"):
            return numbers_doc.convert_numbers(path)
    except Exception as exc:
        # Fall through to the LibreOffice/PDF route rather than failing the file.
        logger.warning("numbers-parser failed for %s, falling back: %s", path, exc)
        return None


def _maybe_matlab(path: Path) -> str | None:
    if _effective_suffix(path) not in {".m", ".mlx"}:
        return None
    from twomarkdown.converter import matlab

    if not matlab.is_matlab(path):
        return None
    with span("matlab"):
        return matlab.convert_matlab(path)


def _maybe_xmind(path: Path) -> str | None:
    if _effective_suffix(path) != ".xmind":
        return None
    from twomarkdown.converter import xmind

    if not xmind.is_xmind(path):
        return None
    with span("xmind"):
        return xmind.convert_xmind(path)


def _maybe_excel(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    if suffix not in {".xlsx", ".xlsm"}:
        return None
    try:
        from twomarkdown.converter.excel import convert_xlsx

        return convert_xlsx(path)
    except Exception as exc:
        logger.debug("Native Excel conversion failed, MarkItDown fallback: %s", exc)
        return None


def _maybe_eml(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    if suffix != ".eml":
        return None
    try:
        from twomarkdown.converter.eml import convert_eml

        return convert_eml(path)
    except Exception as exc:
        logger.debug("EML conversion failed: %s", exc)
        raise


def _maybe_json(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    if suffix != ".json":
        return None
    import json

    raw = path.read_bytes().decode("utf-8-sig", errors="replace").strip()
    if not raw:
        return None
    try:
        pretty = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        pretty = raw
    return f"```json\n{pretty}\n```\n"


def _maybe_legacy_office(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    from twomarkdown.converter.office_legacy import (
        LEGACY_SUFFIXES,
        convert_legacy_office,
    )

    if suffix not in LEGACY_SUFFIXES:
        return None
    try:
        return convert_legacy_office(path)
    except ImportError as exc:
        raise ConversionError("LibreOffice converter unavailable") from exc


def _maybe_audio(path: Path) -> str | None:
    suffix = _effective_suffix(path)
    if suffix not in {".wav", ".mp3"}:
        return None
    try:
        from twomarkdown.converter.audio import convert_audio

        return convert_audio(path)
    except ImportError:
        return None
    except Exception as exc:
        logger.debug("Local Whisper skipped: %s", exc)
        return None


def _convert_source_to_markdown(
    source_path: Path,
    *,
    ocr_fn: Callable[[bytes], str] | None = None,
    show_progress: bool = False,
    cancel: threading.Event | None = None,
    file_index: int | None = None,
) -> str:
    suffix = _effective_suffix(source_path)
    engine = ocr_fn if ocr_fn is not None else _get_ocr_fn()

    # .numbers before the generic iWork route: the native parser keeps cells and
    # formulas, while the LibreOffice/PDF path renders a picture of the grid.
    native = _maybe_numbers(source_path)
    if native is not None:
        set_converter("numbers")
        return native

    if iwork_config.iwork_enabled and iwork.is_iwork_bundle(source_path):
        set_converter("iwork")
        convert_pdf = lambda p: _convert_pdf_with_ocr(  # noqa: E731
            p,
            ocr_fn=engine,
            show_progress=show_progress,
            cancel=cancel,
            file_index=file_index,
        )
        with span("iwork"):
            return iwork.convert_bundle(
                source_path, convert_pdf=convert_pdf, ocr_fn=engine
            )

    if ereader.is_ereader(source_path):
        set_converter("ereader")
        with span("ereader"):
            return ereader.convert_ereader(source_path)

    native = _maybe_matlab(source_path)
    if native is not None:
        set_converter("matlab")
        return native

    native = _maybe_xmind(source_path)
    if native is not None:
        set_converter("xmind")
        return native

    native = _maybe_eml(source_path)
    if native is not None:
        set_converter("eml")
        return native

    native = _maybe_legacy_office(source_path)
    if native is not None:
        set_converter("legacy_office")
        return native

    native = _maybe_json(source_path)
    if native is not None:
        set_converter("json")
        return native

    native = _maybe_excel(source_path)
    if native is not None:
        set_converter("excel")
        return native

    native = _maybe_audio(source_path)
    if native is not None:
        set_converter("audio")
        return native

    set_converter("markitdown")
    markdown = markitdown_converter.convert_file(source_path)
    if suffix == ".pdf":
        set_converter("pdf")
        markdown, _ = _compose_pdf(
            markdown,
            source_path,
            ocr_fn=engine,
            show_progress=show_progress,
            cancel=cancel,
            file_index=file_index,
        )
    elif ocr.is_raster_image(source_path) or suffix == ".svg":
        if not show_progress:
            logger.info("Raster image OCR: %s", source_path)
        set_converter("image")
        with span("image.ocr"):
            markdown = ocr.convert_image_file(
                source_path,
                ocr_fn=engine,
                existing_markdown=markdown,
            )
    return markdown


def _expand_zips(files: list[Path], input_dir: Path, output_dir: Path) -> list[Path]:
    if not conversion_config.explode_zip:
        return files
    try:
        from twomarkdown.converter.zip_ingest import extract_zip, is_explodable_zip
    except Exception:
        return files

    expanded: list[Path] = []
    for path in files:
        try:
            explodable = is_explodable_zip(path)
        except OSError as exc:
            logger.warning("Skipping zip sniff for %s: %s", path, exc)
            expanded.append(path)
            continue
        if not explodable:
            expanded.append(path)
            continue
        dest = _unzip_dest(path, input_dir, output_dir)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            inner = extract_zip(path, dest)
        except Exception as exc:
            logger.warning("ZIP extract failed for %s: %s", path, exc)
            expanded.append(path)
            continue
        members = _filter_exploded(inner, dest)
        if members:
            expanded.extend(members)
        else:
            expanded.append(path)
    return expanded


def _write_chunks(output_md: Path, markdown: str) -> None:
    if not conversion_config.emit_chunks:
        return
    try:
        from twomarkdown.converter.chunks import chunk_markdown, write_chunks_sidecar

        chunks = chunk_markdown(markdown)
        write_chunks_sidecar(output_md, chunks)
    except Exception as exc:
        logger.debug("Chunk sidecar skipped: %s", exc)


def _cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ConversionError("cancelled")


def _skip_ocr_if_cancelled(
    inner: Callable[[bytes], str] | None,
    cancel: threading.Event | None,
) -> Callable[[bytes], str] | None:
    if inner is None or cancel is None:
        return inner

    def _wrapped(image_bytes: bytes) -> str:
        if cancel.is_set():
            return ""
        return inner(image_bytes)

    return _wrapped


INCOMPLETE_MARKER = "> **INCOMPLETO:**"


def _incomplete_banner(source_path: Path, budget: float | None) -> str:
    """Header saying plainly that this file is a partial conversion.

    Without it a truncated file is indistinguishable from a finished one, and
    the pages that did convert are thrown away for the sake of that ambiguity.
    """
    limit = f" tras agotar su presupuesto de {budget / 60:.0f} min" if budget else ""
    return (
        f"{INCOMPLETE_MARKER} la conversión de `{source_path.name}` se detuvo"
        f"{limit}, así que faltan páginas al final. Vuelve a lanzar `make process`: "
        "las páginas ya transcritas salen de caché y sólo se completan las que "
        "faltan.\n"
    )


def file_timeout_budget(
    planned_seconds: float, floor: float | None, factor: float
) -> float | None:
    """Seconds a file may take: its own estimated work, never below the floor.

    The floor alone cannot serve both a two-page note and a sixty-call chapter.
    Keeping this at module level makes the rule testable on its own — the bug it
    replaces was a deadline computed from the floor while the log reported the
    per-file budget, so every file was silently capped at the minimum.
    """
    if not floor:
        return None
    return max(floor, planned_seconds * factor)


def _describe_figure_cached(
    image_bytes: bytes,
    cache: OcrCache | None,
    language: str | None,
    *,
    page_number: int | None = None,
) -> str:
    if cache is not None:
        hit = cache.get(image_bytes)
        if hit is not None:
            note("figure.cache_hit")
            return hit
    from twomarkdown.converter.ocr import describe_image_bytes

    with events.stage("figures", page=page_number):
        text = describe_image_bytes(image_bytes, language=language)
    if cache is not None:
        cache.put(image_bytes, text)
    return text


def _inline_pdf_figures(
    markdown: str,
    source_path: Path,
    *,
    assets_dir: Path,
    output_root: Path,
    output_dir: Path,
    cancel: threading.Event | None,
) -> tuple[str, bool]:
    """Render figure regions, describe them, and place each under its own page."""
    from twomarkdown.converter import figures as figures_mod

    found = figures_mod.extract_figures(source_path, assets_dir)
    if not found:
        return markdown, False

    # Describe figures in the document's own language, not the model's default.
    from twomarkdown.language import guess_language

    language = guess_language(markdown)

    describe = figure_config.describe_figures_llm and llm_config.llm_enabled
    cache: OcrCache | None = None
    if describe:
        cache = OcrCache(
            output_dir / ".2markdown-figure-cache",
            backend="figure",
            model=llm_config.vision_model,
        )

    blocks_by_page: dict[int, list[str]] = {}
    for figure in found:
        description = ""
        if describe and (cancel is None or not cancel.is_set()):
            try:
                description = _describe_figure_cached(
                    figure.path.read_bytes(),
                    cache,
                    language,
                    page_number=figure.page_number,
                ).strip()
                # Figures are inlined after the clean pass, so the model's
                # \( .. \) delimiters would otherwise reach the .md unconverted.
                from twomarkdown.converter.clean import normalize_latex_markup

                description = normalize_latex_markup(description).strip()
            except Exception as exc:
                logger.debug("Figure description failed %s: %s", figure.path, exc)
        blocks_by_page.setdefault(figure.page_number, []).append(
            figures_mod.figure_block(figure, description, output_root=output_root)
        )

    return figures_mod.insert_figure_blocks(markdown, blocks_by_page), True


def _convert_one(
    source_path: Path,
    *,
    input_dir: Path,
    output_dir: Path,
    ocr_fn: Callable[[bytes], str] | None,
    show_progress: bool,
    cancel: threading.Event | None = None,
    planned_output: Path | None = None,
    timeout_budget: float | None = None,
    file_index: int | None = None,
) -> tuple[str, int]:
    _cancelled(cancel)
    ocr.begin_engine_record()
    engine = _skip_ocr_if_cancelled(ocr_fn, cancel)
    markdown = _convert_source_to_markdown(
        source_path,
        ocr_fn=engine,
        show_progress=show_progress,
        cancel=cancel,
        file_index=file_index,
    )
    _cancelled(cancel)
    if not markdown or not markdown.strip():
        if ocr.is_raster_image(source_path):
            markdown = (
                f"## {source_path.name}\n\n*No text extracted from this image.*\n"
            )
        else:
            raise ConversionError("empty result")

    if conversion_config.ocr_enabled:
        with span("ocr.enrich_images"):
            markdown = ocr.enrich_markdown_images(
                markdown,
                source_path,
                ocr_fn=engine,
            )

    _cancelled(cancel)
    with span("clean"):
        markdown = _clean(markdown)

    suffix = _effective_suffix(source_path)
    output_md = (
        planned_output
        if planned_output is not None
        else _mirror_output_path(source_path, input_dir, output_dir)
    )
    if suffix == ".pdf":
        assets_dir = output_md.parent / f"{output_md.stem}_assets"
        inlined = False
        if figure_config.figures_enabled:
            try:
                with span("pdf.figures"):
                    markdown, inlined = _inline_pdf_figures(
                        markdown,
                        source_path,
                        assets_dir=assets_dir,
                        output_root=output_md.parent,
                        output_dir=output_dir,
                        cancel=cancel,
                    )
            except Exception as exc:
                logger.debug("PDF figure pass skipped: %s", exc)

        # Figures already carry the page images inline; the flat trailing index
        # only duplicates them, so keep it for the no-figure case.
        if not inlined and conversion_config.extract_assets:
            try:
                from twomarkdown.converter.assets import (
                    extract_pdf_images,
                    markdown_asset_index,
                )

                with span("pdf.assets"):
                    extracted = extract_pdf_images(source_path, assets_dir)
                extra = markdown_asset_index(extracted, output_root=output_md.parent)
                if extra and extra.strip():
                    markdown = f"{markdown.rstrip()}\n\n{extra.strip()}\n"
            except Exception as exc:
                logger.debug("PDF asset extract skipped: %s", exc)

    # Deliberately no _cancelled() here: if the clock ran out mid-file we still
    # write what converted, flagged, instead of discarding good pages. The
    # manifest keeps the file as failed, so the next run finishes it.
    interrupted = cancel is not None and cancel.is_set()
    source_rel = _source_relpath(source_path, input_dir, output_dir)
    body = markdown
    if interrupted:
        body = f"{_incomplete_banner(source_path, timeout_budget)}\n{markdown}"
    content = (
        build_frontmatter(
            source_path,
            input_dir,
            body,
            source_rel=source_rel,
            ocr_fallback_pages=ocr.engine_fallback_count(),
        )
        + body
    )
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(content, encoding="utf-8")
    if interrupted:
        raise ConversionError(f"cancelled; partial output written to {output_md.name}")
    _write_chunks(output_md, markdown)
    return str(output_md), len(markdown)


def _timeout_item(
    source_path: Path, timeout: float
) -> tuple[Path, str, int | None, int, str | None]:
    return (
        source_path,
        "failed",
        int(timeout * 1000),
        0,
        f"timeout after {timeout}s",
    )


def _write_run_artifacts(
    *,
    manifest: Manifest,
    collector,
    wall_ms: float,
    input_dir: Path,
    output_dir: Path,
    result: BatchResult,
) -> None:
    if not conversion_config.write_export_report:
        return
    import json

    from twomarkdown.telemetry.report import write_html, write_pdf
    from twomarkdown.telemetry.store import write_run
    from twomarkdown.telemetry.summary import (
        build_summary,
        config_snapshot,
        traces_payload,
    )

    traces = list(getattr(collector, "files", []) or [])
    batch_spans = list(getattr(collector, "batch_spans", []) or [])
    summary = build_summary(
        manifest=manifest,
        traces=traces,
        batch_spans=batch_spans,
        wall_ms=wall_ms,
        input_dir=input_dir,
        output_dir=output_dir,
        converted=result.converted,
        failed=result.failed,
        skipped=result.skipped,
        config=config_snapshot(),
    )
    write_html(summary, output_dir)
    write_pdf(summary, output_dir)
    payload = traces_payload(traces, batch_spans)
    (output_dir / ".2markdown-trace.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    write_run(summary, payload)


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
    cancel: threading.Event | None = None,
    file_index_offset: int = 0,
) -> BatchResult:
    input_dir = input_dir.resolve()
    output_dir = output_dir.resolve()
    skip_existing = (
        skip_existing if skip_existing is not None else conversion_config.skip_existing
    )
    previous_conversion = conversion_config.model_copy()
    previous_llm = llm_config.model_copy()
    if ocr_enabled is not None:
        conversion_config.ocr_enabled = ocr_enabled

    collector = begin_batch()
    wall_started = time.perf_counter()
    try:
        if only_files is not None:
            files = sorted(path.resolve() for path in only_files)
        else:
            files = discover_files(input_dir, output_dir)
        with span("zip.explode"):
            files = _expand_zips(files, input_dir, output_dir)
        # Fixed once, from the final (post-expansion) file list, so a file's
        # index in `file_started`/`file_done`/`page_*` events stays the same
        # whether it converts first or last — `order_by_cost()` below only
        # reorders *scheduling*, never this identity.
        # `file_index_offset` lets a caller that converts one file at a time
        # through its own outer loop (`server/jobs.py`'s `_run_job`/`retry`,
        # each calling this with `only_files=[one_file]`) keep every
        # `file_started`/`file_done`/`page_*` event's `index` equal to that
        # file's real position in *its own* job, instead of always `0` (this
        # call's own `files` list has exactly one entry either way). Left at
        # its default for every other caller (the CLI batch, which always
        # converts the whole list in one call and needs no offset).
        file_index_map: dict[Path, int] = {
            p: i for i, p in enumerate(files, start=file_index_offset)
        }
        manifest_path = output_dir / ".2markdown-manifest.json"
        manifest = Manifest(manifest_path)
        result = BatchResult()
        backend = (
            conversion_config.ocr_backend if conversion_config.ocr_enabled else "none"
        )
        cache = OcrCache(
            output_dir / ".2markdown-ocr-cache",
            backend=backend,
            lang=conversion_config.tesseract_lang,
            model=llm_config.vision_model if backend == "ollama" else "",
        )
        ocr_fn = _cached_ocr_fn(_get_ocr_fn(), cache)
        use_progress = _resolve_show_progress(show_progress, verbose=verbose)
        workers = effective_workers()
        events.set_cpu_workers(workers)
        _warn_once_if_figure_model_collapses()
        timeout = conversion_config.file_timeout_sec

        if dry_run:
            result.planned = [str(p) for p in files]
            return result

        # One source per output file: "X.pages" and "X.pdf" would otherwise both
        # write "X.md" and the later worker would silently discard the earlier.
        planned = plan_output_paths(files, input_dir, output_dir)

        file_budget: dict[Path, float] = {}
        file_pages: dict[Path, int] = {}
        work: list[Path] = []
        for source_path in files:
            output_md = planned[source_path]
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

        # Cost the batch before running it: the cheap PyMuPDF pass that finds
        # model-bound pages costs seconds, and it buys both an honest up-front
        # estimate and a sane order to run in.
        if work:
            try:
                from twomarkdown.batch.planner import order_by_cost, plan_batch

                with span("batch.plan"):
                    batch_plan = plan_batch(work)
                logger.info("%s", batch_plan.describe())
                work = order_by_cost(work, batch_plan)
                # A flat per-file timeout cannot fit both a 2-page note and a
                # 60-call chapter: on a local model the long files always lost.
                # Budget each file from its own estimated work instead.
                file_budget = {
                    f.path: f.weight * conversion_config.timeout_safety_factor
                    for f in batch_plan.files
                }
                file_pages = {f.path: f.pages for f in batch_plan.files}
            except Exception as exc:
                logger.debug("Batch planning skipped: %s", exc)

        def _budget_for(source_path: Path) -> float | None:
            return file_timeout_budget(
                file_budget.get(source_path, 0.0),
                timeout,
                1.0,  # file_budget already carries the safety factor
            )

        def _emit_timeout(source_path: Path, budget: float | None) -> None:
            # A timed-out file is abandoned from *this* (scheduling) thread,
            # not the worker that called `events.begin_file` — hence the
            # explicit index rather than relying on thread-local context.
            job_index = file_index_map.get(source_path)
            if job_index is None:
                return
            events.end_file(
                job_index,
                "failed",
                budget or 0.0,
                reason=f"timeout after {budget or timeout}s",
            )

        def _handle(
            source_path: Path, cancel: threading.Event | None
        ) -> tuple[Path, str, int | None, int, str | None]:
            started = time.perf_counter()
            begin_file(source_path)
            job_index = file_index_map.get(source_path)
            if job_index is not None:
                events.begin_file(
                    job_index, source_path, pages=file_pages.get(source_path, 0)
                )
            try:
                _out, chars = _convert_one(
                    source_path,
                    planned_output=planned.get(source_path),
                    timeout_budget=_budget_for(source_path),
                    input_dir=input_dir,
                    output_dir=output_dir,
                    ocr_fn=ocr_fn,
                    show_progress=use_progress and workers == 1,
                    cancel=cancel,
                    file_index=job_index,
                )
                duration_ms = int((time.perf_counter() - started) * 1000)
                # Set inside `_convert_one` (same thread) whenever an LLM-backed
                # stage was silently skipped this file — an unreachable local
                # model or an exhausted cloud quota (see B1/M5). The file still
                # converted, so this is "ok" with an asterisk, not "failed".
                soft_reason = ocr.soft_failure_reason()
                status = "warn" if soft_reason else "ok"
                if job_index is not None:
                    events.end_file(
                        job_index, status, duration_ms / 1000.0, reason=soft_reason
                    )
                return source_path, status, duration_ms, chars, soft_reason
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                # A cancel token set for *this* file (server/jobs.py's per-file
                # `file_cancel`, forwarded here as `cancel`) is not a real
                # failure — `_convert_one` still wrote whatever converted so
                # far, flagged with the same "conversión incompleta" banner a
                # timeout produces. The WS event says so (`file_done{status:
                # "cancelled"}`) rather than "failed", even though the
                # aggregate `BatchResult`/manifest below still count it under
                # "failed" (no separate bucket there; the desktop job layer
                # reads its own `file_cancel`/`file_cancel_requested` state to
                # tell the two apart regardless of what this tuple says).
                was_cancelled = cancel is not None and cancel.is_set()
                event_status = "cancelled" if was_cancelled else "failed"
                if job_index is not None:
                    events.end_file(
                        job_index, event_status, duration_ms / 1000.0, reason=str(exc)
                    )
                return source_path, "failed", duration_ms, 0, str(exc)
            finally:
                end_file()

        consumed: set[Path] = set()

        def _consume(item: tuple[Path, str, int | None, int, str | None]) -> None:
            source_path, status, duration_ms, chars, error = item
            if source_path in consumed:
                return
            consumed.add(source_path)
            checksum = file_checksum(source_path)
            output_md = planned.get(
                source_path,
                _mirror_output_path(source_path, input_dir, output_dir),
            )
            if status in ("ok", "warn"):
                # The manifest only knows "ok"/"failed"/"skipped" (batch.manifest
                # has no warn concept — the file really did convert, on disk it
                # is indistinguishable from a clean run); "warn" is purely
                # `BatchResult`'s and the desktop job's way of saying so.
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
                if status == "warn":
                    result.warn += 1
                    result.warn_paths.append(str(source_path))
                    message = f"Converted with a warning: {source_path}: {error}"
                    if use_progress:
                        tqdm.write(message)
                    else:
                        logger.warning(message)
                elif verbose:
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

        if not work:
            _write_run_artifacts(
                manifest=manifest,
                collector=collector,
                wall_ms=(time.perf_counter() - wall_started) * 1000.0,
                input_dir=input_dir,
                output_dir=output_dir,
                result=result,
            )
            return result

        cancels = {path: threading.Event() for path in work}
        # `cancel` (an *external* stop request — `server/jobs.py` passes the
        # per-file token a cancel endpoint sets) is forwarded onto every
        # per-file token above so a caller that stops it mid-file gets the
        # same "finish the current page, keep the partial output" handling
        # this module already gives a per-file timeout (`_convert_one`'s
        # `interrupted` branch) — not a harder, mid-page-unsafe stop. A daemon
        # watcher (not a poll loop) so it costs nothing while `cancel` is
        # never set, which is every call this module's own CLI callers make
        # (they never pass `cancel` at all).
        _cancel_watcher: threading.Thread | None = None
        if cancel is not None:

            def _forward_cancel() -> None:
                cancel.wait()
                for ev in cancels.values():
                    ev.set()

            _cancel_watcher = threading.Thread(target=_forward_cancel, daemon=True)
            _cancel_watcher.start()

        if not timeout:
            with tqdm(
                work,
                desc="Converting",
                unit="file",
                disable=not use_progress,
            ) as pbar:
                for source_path in pbar:
                    if use_progress:
                        pbar.set_postfix_str(source_path.name, refresh=False)
                    _consume(_handle(source_path, cancels[source_path]))
        elif workers == 1:
            with tqdm(
                work,
                desc="Converting",
                unit="file",
                disable=not use_progress,
            ) as pbar:
                for source_path in pbar:
                    if use_progress:
                        pbar.set_postfix_str(source_path.name, refresh=False)
                    pool = ThreadPoolExecutor(max_workers=1)
                    future = pool.submit(_handle, source_path, cancels[source_path])
                    try:
                        _consume(future.result(timeout=_budget_for(source_path)))
                    except FuturesTimeout:
                        cancels[source_path].set()
                        budget = _budget_for(source_path)
                        _emit_timeout(source_path, budget)
                        _consume(_timeout_item(source_path, budget))
                    finally:
                        # Timed-out work may keep running; do not block the batch.
                        pool.shutdown(wait=False)
        else:
            # One executor per in-flight file so a timeout can start the next
            # file without waiting on the abandoned thread. Deadline starts when
            # that file is submitted (a slot is free), not when the batch began.
            remaining = list(work)
            in_flight: dict[Future, Path] = {}
            pools: dict[Future, ThreadPoolExecutor] = {}
            deadlines: dict[Future, float] = {}
            abandoned: set[Future] = set()
            zombie_budget = workers

            def _reap_zombies() -> None:
                for future in list(abandoned):
                    if not future.done():
                        continue
                    abandoned.remove(future)
                    pool = pools.pop(future, None)
                    if pool is not None:
                        pool.shutdown(wait=False)

            def _submit_more() -> None:
                _reap_zombies()
                while remaining and len(in_flight) < workers:
                    if len(abandoned) >= zombie_budget:
                        break
                    source_path = remaining.pop(0)
                    pool = ThreadPoolExecutor(max_workers=1)
                    future = pool.submit(_handle, source_path, cancels[source_path])
                    in_flight[future] = source_path
                    pools[future] = pool
                    # The deadline must use this file's own budget: using the
                    # flat floor here silently capped every file at the minimum,
                    # while the timeout message still quoted the real budget.
                    budget = _budget_for(source_path)
                    deadlines[future] = time.monotonic() + (budget or timeout)

            try:
                _submit_more()
                with tqdm(
                    total=len(work),
                    desc="Converting",
                    unit="file",
                    disable=not use_progress,
                ) as pbar:
                    while in_flight or remaining:
                        _reap_zombies()
                        if not in_flight:
                            if remaining and len(abandoned) >= zombie_budget:
                                wait(
                                    abandoned,
                                    timeout=0.1,
                                    return_when=FIRST_COMPLETED,
                                )
                                continue
                            _submit_more()
                            if not in_flight:
                                break
                        now = time.monotonic()
                        for future in list(in_flight):
                            if now < deadlines[future]:
                                continue
                            source_path = in_flight.pop(future)
                            abandoned.add(future)
                            cancels[source_path].set()
                            timeout_budget = _budget_for(source_path)
                            _emit_timeout(source_path, timeout_budget)
                            _consume(_timeout_item(source_path, timeout_budget))
                            pbar.update(1)
                            _submit_more()
                        if not in_flight:
                            continue
                        wait_s = min(deadlines[f] for f in in_flight) - time.monotonic()
                        done, _ = wait(
                            set(in_flight) | abandoned,
                            timeout=max(0.0, wait_s),
                            return_when=FIRST_COMPLETED,
                        )
                        for future in done:
                            if future in abandoned:
                                continue
                            if future not in in_flight:
                                continue
                            source_path = in_flight.pop(future)
                            pools.pop(future).shutdown(wait=False)
                            pbar.update(1)
                            try:
                                item = future.result()
                            except Exception as exc:
                                item = (source_path, "failed", None, 0, str(exc))
                            _consume(item)
                            _submit_more()
            finally:
                for pool in pools.values():
                    pool.shutdown(wait=False)

        _write_run_artifacts(
            manifest=manifest,
            collector=collector,
            wall_ms=(time.perf_counter() - wall_started) * 1000.0,
            input_dir=input_dir,
            output_dir=output_dir,
            result=result,
        )
        return result
    finally:
        conversion_config.ocr_enabled = previous_conversion.ocr_enabled
        llm_config.llm_enabled = previous_llm.llm_enabled
        end_batch()
