"""CLI entry point for batch folder-to-markdown conversion."""

import logging
from pathlib import Path
from typing import Literal

import typer

from src.batch.processor import process_batch
from src.config import conversion_config, llm_config, pdf_ocr_config
from src.paths import normalize_batch_input

app = typer.Typer(
    name="twomarkdown",
    help="Batch convert folders to markdown using MarkItDown.",
    no_args_is_help=True,
)


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
        True,
        "--ocr/--no-ocr",
        help="Run OCR on markdown image references",
    ),
    skip_existing: bool = typer.Option(
        True,
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
        "tesseract",
        "--ocr-backend",
        help="OCR engine for images and scanned PDF fallback",
    ),
    llm_enabled: bool = typer.Option(
        False,
        "--llm-enabled",
        help="Use Ollama vision for OCR (requires ocr-backend=ollama)",
    ),
    pdf_ocr: bool = typer.Option(
        True,
        "--pdf-ocr/--no-pdf-ocr",
        help="OCR scanned PDFs when MarkItDown returns little text",
    ),
    fetch_remote_images: bool = typer.Option(
        False,
        "--fetch-remote-images",
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
) -> None:
    """Convert all supported files under INPUT to markdown under OUTPUT."""
    _configure_logging(verbose)

    batch_root, default_out, only_files = normalize_batch_input(input)
    resolved_output = output.resolve() if output is not None else default_out

    conversion_config.input_dir = batch_root
    conversion_config.output_dir = resolved_output
    conversion_config.ocr_enabled = ocr
    conversion_config.skip_existing = skip_existing
    if ollama:
        ocr_backend = "ollama"
        llm_enabled = True

    conversion_config.ocr_backend = ocr_backend
    conversion_config.fetch_remote_images = fetch_remote_images
    llm_config.llm_enabled = llm_enabled
    pdf_ocr_config.pdf_ocr_enabled = pdf_ocr

    if ocr_backend == "ollama" and not llm_enabled:
        typer.echo(
            "Warning: --ocr-backend=ollama without --llm-enabled; "
            "falling back to tesseract for OCR.",
            err=True,
        )

    typer.echo(f"Input:  {input.resolve()}")
    typer.echo(f"Output: {resolved_output}")

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

    if result.failed > 0 and result.failed_paths:
        typer.echo("Failed files:", err=True)
        for path in result.failed_paths:
            typer.echo(f"  - {path}", err=True)

    raise typer.Exit(code=1 if result.failed > 0 else 0)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
