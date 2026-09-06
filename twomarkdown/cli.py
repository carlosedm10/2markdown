"""CLI entry point for batch folder-to-markdown conversion."""

import logging
from pathlib import Path
from typing import Literal

import typer

from twomarkdown.batch.processor import process_batch
from twomarkdown.config import conversion_config, llm_config, pdf_ocr_config
from twomarkdown.paths import normalize_batch_input

app = typer.Typer(
    name="twomarkdown",
    help="Batch convert folders to markdown using MarkItDown.",
    no_args_is_help=True,
)


def resolve_ocr_backend(
    *,
    ollama: bool,
    ocr_backend: Literal["tesseract", "ollama"],
    llm_enabled: bool,
) -> tuple[Literal["tesseract", "ollama"], bool]:
    """Return (backend, llm_enabled). ollama flag or backend=ollama implies LLM."""
    if ollama:
        return "ollama", True
    if ocr_backend == "ollama":
        return "ollama", True
    return ocr_backend, llm_enabled


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if verbose:
        # MarkItDown/pdfminer are very noisy at DEBUG
        for name in ("pdfminer", "charset_normalizer", "PIL", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)


@app.command("convert")
def convert(
    input: Path = typer.Option(
        ...,
        "--input",
        "-i",
        help="Input file or directory to convert",
        exists=True,
        file_okay=True,
        dir_okay=True,
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: <input_dir>_2markdown next to input)",
    ),
    ocr: bool = typer.Option(
        conversion_config.ocr_enabled,
        "--ocr/--no-ocr",
        help="Run OCR on markdown image references",
    ),
    skip_existing: bool = typer.Option(
        conversion_config.skip_existing,
        "--skip-existing/--force",
        help="Skip files whose output .md is newer than source",
    ),
    ollama: bool = typer.Option(
        False,
        "--ollama",
        help=(
            "Use Ollama vision for OCR "
            "(shortcut for --ocr-backend=ollama --llm-enabled)"
        ),
    ),
    ocr_backend: Literal["tesseract", "ollama"] = typer.Option(
        conversion_config.ocr_backend,
        "--ocr-backend",
        help="OCR engine for images and scanned PDF fallback",
    ),
    llm_enabled: bool = typer.Option(
        llm_config.llm_enabled,
        "--llm-enabled/--no-llm-enabled",
        help=(
            "Enable Ollama vision for OCR (optional alias; not required when "
            "--ocr-backend=ollama)"
        ),
    ),
    pdf_ocr: bool = typer.Option(
        pdf_ocr_config.pdf_ocr_enabled,
        "--pdf-ocr/--no-pdf-ocr",
        help="OCR scanned PDFs when MarkItDown returns little text",
    ),
    fetch_remote_images: bool = typer.Option(
        conversion_config.fetch_remote_images,
        "--fetch-remote-images/--no-fetch-remote-images",
        help="Fetch http(s) images referenced in markdown for OCR",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Verbose logging",
    ),
    progress: bool | None = typer.Option(
        None,
        "--progress/--no-progress",
        help="Show progress bar (default: on in TTY, off with --verbose)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="List files that would be converted without writing output",
    ),
    workers: int | None = typer.Option(
        None,
        "--workers",
        help=(
            "Parallel file conversions "
            f"(default: {conversion_config.parallel_workers})"
        ),
    ),
    emit_chunks: bool = typer.Option(
        conversion_config.emit_chunks,
        "--emit-chunks/--no-emit-chunks",
        help="Write a .chunks.json sidecar next to each markdown file",
    ),
    clean: bool = typer.Option(
        conversion_config.clean_markdown,
        "--clean/--no-clean",
        help="Deterministic markdown cleanup (quotes, hyphenation, headers)",
    ),
    tables: bool = typer.Option(
        conversion_config.extract_tables,
        "--tables/--no-tables",
        help="Extract PDF/Excel tables as GitHub-flavored markdown",
    ),
    describe_figures: bool = typer.Option(
        conversion_config.describe_figures,
        "--describe-figures/--no-describe-figures",
        help="Describe images with little OCR text when vision LLM is enabled",
    ),
    report: bool = typer.Option(
        conversion_config.write_export_report,
        "--report/--no-report",
        help="Write 2markdown-report.html/.pdf in the output folder",
    ),
) -> None:
    """Convert all supported files under INPUT to markdown under OUTPUT."""
    _configure_logging(verbose)

    batch_root, default_out, only_files = normalize_batch_input(input)
    resolved_output = output.resolve() if output is not None else default_out

    conversion_config.input_dir = batch_root
    conversion_config.output_dir = resolved_output
    conversion_config.ocr_enabled = ocr
    conversion_config.skip_existing = skip_existing
    ocr_backend, llm_enabled = resolve_ocr_backend(
        ollama=ollama,
        ocr_backend=ocr_backend,
        llm_enabled=llm_enabled,
    )

    conversion_config.ocr_backend = ocr_backend
    conversion_config.fetch_remote_images = fetch_remote_images
    conversion_config.clean_markdown = clean
    conversion_config.extract_tables = tables
    conversion_config.describe_figures = describe_figures
    conversion_config.emit_chunks = emit_chunks
    conversion_config.write_export_report = report
    if workers is not None:
        conversion_config.parallel_workers = max(1, workers)
    llm_config.llm_enabled = llm_enabled
    pdf_ocr_config.pdf_ocr_enabled = pdf_ocr

    typer.echo(f"Input:  {input.resolve()}")
    typer.echo(f"Output: {resolved_output}")

    if dry_run:
        resolved_output.mkdir(parents=True, exist_ok=True)
        result = process_batch(
            batch_root,
            resolved_output,
            only_files=only_files,
            skip_existing=skip_existing,
            ocr_enabled=ocr,
            verbose=verbose,
            show_progress=progress,
            dry_run=True,
        )
        typer.echo(f"Dry run: {len(result.planned)} file(s)")
        for path in result.planned:
            typer.echo(f"  {path}")
        raise typer.Exit(code=0)

    resolved_output.mkdir(parents=True, exist_ok=True)

    result = process_batch(
        batch_root,
        resolved_output,
        only_files=only_files,
        skip_existing=skip_existing,
        ocr_enabled=ocr,
        verbose=verbose,
        show_progress=progress,
    )

    typer.echo(
        f"Done: converted={result.converted}, "
        f"failed={result.failed}, skipped={result.skipped}"
    )
    if conversion_config.write_export_report:
        typer.echo(f"Report: {resolved_output / '2markdown-report.html'}")
        typer.echo(f"PDF:    {resolved_output / '2markdown-report.pdf'}")

    if result.failed > 0 and result.failed_paths:
        typer.echo("Failed files:", err=True)
        for path in result.failed_paths:
            typer.echo(f"  - {path}", err=True)

    raise typer.Exit(code=1 if result.failed > 0 else 0)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
