"""Optional Ollama vision OCR via Pydantic AI."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import httpx
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider, infer_provider_class
from pydantic_ai.providers.ollama import OllamaProvider

from twomarkdown.batch import events, gpu_memory
from twomarkdown.config import llm_config
from twomarkdown.converter.image_prep import prepare_image_for_vision_llm
from twomarkdown.prompts import (
    FIGURE_DESCRIBE_PROMPT,
    IMAGE_OCR_PROMPT,
    figure_language_instruction,
)

logger = logging.getLogger(__name__)

# Agents are per thread, never module-level globals. `run_sync` drives each call on
# its own event loop, and an httpx.AsyncClient's pooled connections belong to the
# loop that opened them. A client shared across worker threads therefore hands a
# connection from a dead loop to the next caller: the request logs "200 OK", the
# OpenAI client retries it, and the batch stalls at 0% CPU holding the vision permit.
_local = threading.local()

# One permit per model for hosted providers, one shared permit for everything
# local. Two local models are not genuinely concurrent: they share one GPU, and
# overlapping requests make Ollama's OpenAI-compatible endpoint answer with a
# malformed completion (`role: ""`, which pydantic-ai rejects) instead of text.
# Measured: 4 of 6 pages failed with a figure model running alongside, 2 of them
# after burning 200s of GPU time first. Serialized, the same 6 pages all pass.
#
# Wrapped in TrackedSemaphore (not a bare threading.Semaphore) so the desktop
# server's live view has something to read: every acquire/release reports a
# `semaphores` event naming the real holder (file/page/stage), not a guess
# reconstructed from logs. `_page_ocr_lock`/`_remote_lock` register as the
# "gpu"/"cloud" lanes the event actually reports; `_figure_lock` is wrapped the
# same way for consistency even though `_figure_permit()` below never reaches
# it today (a local figure model always shares `_page_ocr_lock` instead), so it
# registers under a name the `semaphores` event does not surface.
#
# `_page_ocr_lock`'s permit count is `llm_config.local_gpu_permits` (default
# 1, validated 1..2 by `Settings.local_gpu_permits`) — an experimental knob
# for a machine whose GPU can genuinely interleave two local calls (e.g. a
# page's OCR and the previous page's figure caption). `apply_gpu_permits`
# rebuilds it when the server applies a changed setting; `effective_workers()`
# is unaffected — permits let one file's own calls interleave, they do not
# make the batch run more files at once.
_page_ocr_lock = events.TrackedSemaphore(
    llm_config.local_gpu_permits, lane=events.GPU_LANE
)
_figure_lock = events.TrackedSemaphore(1, lane="figure_reserve_unused")
_remote_lock = events.TrackedSemaphore(
    llm_config.remote_max_concurrency, lane=events.CLOUD_LANE
)


def apply_gpu_permits(permits: int) -> None:
    """Resize `_page_ocr_lock` to `permits` (clamped to at least 1).

    Called by `server.settings` whenever the persisted `Settings.
    local_gpu_permits` is read or replaced, so a changed setting takes
    effect without restarting the server. Rebuilding the semaphore (rather
    than mutating a count inside it) is deliberate: `threading.Semaphore` has
    no public API to change its value, and nothing holds `_page_ocr_lock`
    across this call in practice — permits are acquired and released around
    one model call, never held for the run's duration.
    """
    global _page_ocr_lock
    _page_ocr_lock = events.TrackedSemaphore(max(1, permits), lane=events.GPU_LANE)


def _permit_for(
    model: str, local_lock: events.TrackedSemaphore
) -> events.TrackedSemaphore:
    """The right queue for a model: one permit locally, several for a hosted API.

    A single GPU serves one request at a time, so local work is serialized. A
    hosted provider has no such limit, and queueing against it would throw away
    most of the wall clock.
    """
    if not is_local_model(model):
        return _remote_lock
    return local_lock


def _page_permit() -> threading.Semaphore:
    return _permit_for(llm_config.vision_model, _page_ocr_lock)


def effective_figure_model() -> str:
    """The model that will actually caption figures.

    Delegates to `batch.gpu_memory.effective_figure_model`, the same
    resident-memory arithmetic `batch/estimate.py` shows in the Pipeline
    editor before a conversion ever starts, so the two can never disagree:
    a pipeline the UI says "fits" (e.g. qwen2.5vl:7b OCR + gemma3:4b
    figures, well under a 36 GB GPU limit) now really does run both models,
    instead of this silently collapsing onto the OCR model regardless of
    whether the pair would have fit.

    Collapse only happens when the two are local, genuinely distinct, and
    their combined resident size would exceed what macOS leaves the GPU —
    e.g. 29.1 GB for a 32B page model plus 8.8 GB for a 7B captioner, against
    the ~36 GB a 48 GB machine leaves the GPU. Past that limit the image
    encoder is what runs out first: Ollama logs
    `kIOGPUCommandBufferCallbackErrorOutOfMemory` inside `clip_encode` and
    answers with a malformed completion.

    This function itself stays silent about a collapse it finds — it runs
    once per figure captioned, and logging its message here would print the
    same sentence once per figure instead of once per job. The one-time,
    job-start report a substitution deserves (so a silently-substituted
    model doesn't look like a caption from the model the operator actually
    picked) is `twomarkdown.batch.processor._warn_once_if_figure_model_collapses`
    (the CLI) and `twomarkdown.server.jobs._warn_if_figure_model_collapses`
    (a server job) — both call `gpu_memory.effective_figure_model` directly,
    from the same config, before any figure is captioned.
    """
    figure = llm_config.figure_model
    vision = llm_config.vision_model
    review = llm_config.review_model or None
    resolved, _message = gpu_memory.effective_figure_model(vision, figure, review)
    return resolved


def _figure_permit() -> threading.Semaphore:
    if is_local_model(llm_config.figure_model):
        # Any local model shares the page queue: one GPU serves one request at a
        # time, and asking it for two at once corrupts the reply rather than
        # queueing it. A different model name does not buy real concurrency.
        return _page_ocr_lock
    return _permit_for(llm_config.figure_model, _figure_lock)


# Small vision models fall into repetition loops on dense pages (gemma3:4b will
# repeat one equation until it is cut off). Greedy decoding plus a hard token
# ceiling bounds the damage and the wall-clock cost of a bad page.
_TRANSCRIBE_SETTINGS: dict[str, object] = {"temperature": 0.0, "max_tokens": 3072}
_DESCRIBE_SETTINGS: dict[str, object] = {"temperature": 0.0, "max_tokens": 512}


def strip_wrapping_fence(text: str) -> str:
    """Drop a code fence the model wrapped the whole answer in.

    Vision models return ```markdown ... ``` however firmly the prompt says not to;
    left in place it turns a whole page into a literal code block.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text.strip()
    lines = stripped.split("\n")
    if len(lines) < 2:
        return text.strip()
    # Only unwrap when the fence really encloses everything.
    if not lines[-1].strip().startswith("```"):
        return text.strip()
    inner = lines[1:-1]
    if any(line.lstrip().startswith("```") for line in inner):
        return text.strip()
    return "\n".join(inner).strip()


# The model sees a picture, not a filesystem, so any ![alt](path) it writes points
# at a file that does not exist. The pipeline adds the real figure links itself.
_INVENTED_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")


def strip_invented_image_links(text: str) -> str:
    """Drop markdown image links a vision model invented during transcription.

    Keeps the alt text when it carries a caption, so "![Figura 3](fig3.png)"
    degrades to "Figura 3" rather than vanishing.
    """

    def _replace(match: re.Match) -> str:
        alt = match.group(1).strip()
        return alt if alt else ""

    return _INVENTED_IMAGE_RE.sub(_replace, text)


# A 429 that waiting cannot fix. The provider uses the same status for "too fast"
# and "out of credit", but only the first is worth retrying: an exhausted balance
# cost 178s of backoff per page before giving up, which over a batch is an hour
# spent confirming the same answer.
_PERMANENT_QUOTA_MARKERS = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_hard_limit_reached",
    "no credits remaining",
    "exceeded your current quota",
)


def _report_cloud_cost(model: str, result: Any) -> None:
    """Price one finished cloud call and add it to the job's running USD total.

    A no-op for a local (Ollama) model — nothing billed, nothing to add — and
    best-effort for a hosted one: an unrecognised model with no manual price
    on file prices as `None`. That `unknown` flag is passed through to
    `events.add_cost` (rather than discarded) so the job-level total can still
    report "unknown" rather than reading as a plain zero when nothing ever
    priced (see N16). Reuses `estimate.py`'s own pricing (manual price file
    first, then genai-prices) so a live total and a pre-run estimate never
    disagree about what a model costs.
    """
    if not is_local_model(model):
        try:
            from twomarkdown.batch.estimate import _cloud_price

            usage = result.usage()
            usd, unknown = _cloud_price(
                model, usage.input_tokens or 0, usage.output_tokens or 0
            )
            events.add_cost(usd, unknown=unknown)
        except Exception:
            # Pricing must never cost the batch a page it already transcribed.
            logger.debug("Could not price a %s call", model, exc_info=True)


def _is_out_of_quota(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT_QUOTA_MARKERS)


def _is_connection_error(exc: Exception) -> bool:
    """True when `exc` means "could not reach the model", not "it answered
    with an error". pydantic-ai wraps httpx's own connection failure inside
    `ModelAPIError`, whose message is the httpx exception's own text."""
    if isinstance(
        exc.__cause__,
        (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException),
    ):
        return True
    return "connection error" in str(exc).lower()


def _record_soft_failure_for(model: str, exc: Exception, *, what: str) -> None:
    """Turn an unreachable local model or an exhausted cloud quota into a
    warning the job carries, instead of the silent Tesseract fallback (or
    empty caption) this call's caller falls back to on any error.

    Only these two causes are recorded: anything else (a malformed response,
    a genuine 4xx) is a real per-call failure already logged by the caller,
    not something the whole file should be flagged for.
    """
    if is_local_model(model) and _is_connection_error(exc):
        message = (
            f"{what} omitido: no se pudo conectar con {model} en "
            f"{llm_config.ollama_base_url}"
        )
    elif not is_local_model(model) and _is_out_of_quota(exc):
        message = f"{what} omitido: saldo agotado en {model}"
    else:
        return
    from twomarkdown.converter import ocr as ocr_mod

    ocr_mod.record_soft_failure(message)
    events.log("warning", message)


def _is_rate_limited(exc: Exception) -> bool:
    if _is_out_of_quota(exc):
        return False
    text = str(exc)
    return "429" in text or "rate limit" in text.lower()


def _is_transient_bad_response(exc: Exception) -> bool:
    """True for a reply that is malformed rather than wrong.

    Under concurrent load Ollama answers `/v1/chat/completions` with an empty
    `role`, which pydantic-ai rejects as a validation error. It says nothing
    about the page, so it deserves a retry rather than an empty transcription
    cached and passed off as what the model read.
    """
    text = str(exc)
    return "Invalid response from" in text or (
        "validation error" in text and "ChatCompletion" in text
    )


def _unload_local_model(model: str) -> bool:
    """Ask Ollama to drop a model, so the next call rebuilds its Metal backend.

    A GPU OOM inside the image encoder leaves llama.cpp reporting "backend is in
    error state from a previous command buffer failure - recreate the backend to
    recover": every later request on that model fails identically, so one bad
    page costs the rest of the run. Unloading is the documented way to recreate
    it. Best effort — a failure here only means the retry starts no worse off.
    """
    if not is_local_model(model):
        return False
    base = llm_config.ollama_base_url.rstrip("/").removesuffix("/v1")
    try:
        import httpx

        httpx.post(
            f"{base}/api/generate",
            json={"model": normalize_model_id(model).split(":", 1)[1], "keep_alive": 0},
            timeout=llm_config.connect_timeout_sec,
        )
    except Exception as exc:
        logger.debug("Could not unload %s: %s", model, exc)
        return False
    logger.info("Unloaded %s to recreate its GPU backend", model)
    return True


def _call_with_backoff(run, *, what: str, model: str = ""):
    """Retry a model call through rate limiting, with exponential backoff.

    A hosted provider answers 429 when a batch outruns its quota. Without this a
    whole run silently loses work: 139 figure captions were dropped in one pass,
    each as nothing more than a warning line.
    """
    delay = llm_config.rate_limit_initial_delay_sec
    for attempt in range(llm_config.rate_limit_max_retries + 1):
        try:
            return run()
        except Exception as exc:
            last = attempt == llm_config.rate_limit_max_retries
            if last or not (_is_rate_limited(exc) or _is_transient_bad_response(exc)):
                raise
            logger.info(
                "%s got a transient failure, retrying in %.1fs (attempt %s/%s)",
                what,
                delay,
                attempt + 1,
                llm_config.rate_limit_max_retries,
            )
            if model and _is_transient_bad_response(exc):
                _unload_local_model(model)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def _vision_http_client() -> httpx.AsyncClient:
    """HTTP client with an explicit read timeout.

    The default client waits indefinitely. Because every vision call holds the
    single vision permit, one stalled connection strands its
    worker and blocks every other worker behind it — the batch deadlocks with the
    process at 0% CPU. A read timeout turns that into one failed page.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            llm_config.request_timeout_sec,
            connect=llm_config.connect_timeout_sec,
        ),
        # No keep-alive: run_sync may open a fresh event loop per call, and a
        # pooled connection from the previous loop stalls the next request.
        limits=httpx.Limits(max_keepalive_connections=0),
    )


DEFAULT_PROVIDER = "ollama"


def normalize_model_id(model: str) -> str:
    """Return a "<provider>:<model>" id, defaulting a bare name to Ollama.

    pydantic-ai resolves the provider from the prefix, so "openai:gpt-5.2" and
    "anthropic:claude-sonnet-4-5" work with no extra code. A bare "qwen2.5vl:7b"
    is ambiguous — it already contains a colon — so anything whose prefix is not
    a known provider is treated as an Ollama model name.

    `infer_provider_class` only ever raises `ValueError` for a prefix it does
    not recognise at all (its final `else` branch) — that is the one case
    that means "not a provider name". A *recognised* provider name (e.g.
    "groq") raises `ImportError` instead when that provider's optional SDK
    extra ("pydantic-ai-slim[groq]") is not installed; that says nothing
    about whether the prefix is a provider, only that this environment
    cannot instantiate it. Catching `ImportError` here alongside `ValueError`
    used to fold every such provider into Ollama the moment its extra was
    missing — e.g. a bare-bones Docker image built from `uv.lock` without the
    `groq`/`mistral`/`openrouter` extras — even though a fuller host venv
    (extra packages installed outside the lockfile) recognised the same
    prefix just fine. Narrowing the catch to `ValueError` makes the result
    depend only on whether pydantic-ai knows the provider name, not on which
    optional extras happen to be importable in this environment.
    """
    model = (model or "").strip()
    if not model:
        return model
    prefix = model.split(":", 1)[0]
    try:
        infer_provider_class(prefix)
    except ValueError:
        return f"{DEFAULT_PROVIDER}:{model}"
    except ImportError:
        # The prefix matched a real provider in pydantic-ai's dispatch table;
        # it only failed to import because that provider's optional SDK extra
        # (e.g. "groq") is not installed here. That is still a known provider
        # — just one this environment cannot actually call — so the prefix is
        # kept rather than folded into Ollama.
        pass
    return model


def model_provider(model: str) -> str:
    return normalize_model_id(model).split(":", 1)[0]


def is_local_model(model: str) -> bool:
    """Local models serve one request at a time; hosted APIs do not."""
    return model_provider(model) == DEFAULT_PROVIDER


def _provider_factory(provider_name: str):
    if provider_name == DEFAULT_PROVIDER:
        return OllamaProvider(
            base_url=llm_config.ollama_base_url,
            http_client=_vision_http_client(),
        )
    # Every other provider reads its own credentials from the environment.
    return infer_provider(provider_name)


def get_image_ocr_agent() -> Agent[None, str]:
    """Page-transcription agent for the calling thread."""
    agent = getattr(_local, "ocr_agent", None)
    if agent is None:
        model = infer_model(
            normalize_model_id(llm_config.vision_model),
            provider_factory=_provider_factory,
        )
        agent = Agent(
            model=model,
            system_prompt=IMAGE_OCR_PROMPT.strip(),
            output_type=str,
            model_settings=_TRANSCRIBE_SETTINGS,
        )
        _local.ocr_agent = agent
    return agent


def get_figure_agent() -> Agent[None, str]:
    """Figure-description agent for the calling thread."""
    agent = getattr(_local, "figure_agent", None)
    if agent is None:
        model = infer_model(
            normalize_model_id(effective_figure_model()),
            provider_factory=_provider_factory,
        )
        agent = Agent(
            model=model,
            system_prompt=FIGURE_DESCRIBE_PROMPT.strip(),
            output_type=str,
            model_settings=_DESCRIBE_SETTINGS,
        )
        _local.figure_agent = agent
    return agent


def describe_image_bytes_llm(
    image_bytes: bytes, *, mime_type: str = "image/png", language: str | None = None
) -> str:
    """Short figure caption using the configured Ollama vision model."""
    if not llm_config.llm_enabled:
        return ""

    prepared, mime_type = prepare_image_for_vision_llm(
        image_bytes,
        max_dimension=llm_config.llm_figure_max_dimension,
        max_bytes=llm_config.llm_ocr_max_bytes,
        jpeg_quality=llm_config.llm_ocr_jpeg_quality,
    )
    agent = get_figure_agent()
    content = BinaryContent(data=prepared, media_type=mime_type)
    try:
        with events.stage("figures"), _figure_permit():
            result = _call_with_backoff(
                lambda: agent.run_sync(
                    [
                        "Describe this figure for revision notes. "
                        + figure_language_instruction(language),
                        content,
                    ],
                ),
                what="Figure description",
                model=effective_figure_model(),
            )
        _report_cloud_cost(effective_figure_model(), result)
        return (result.output or "").strip()
    except ModelAPIError as exc:
        logger.warning("Figure description API error: %s", exc)
        _record_soft_failure_for(
            effective_figure_model(), exc, what="Descripción de figuras"
        )
        return ""
    except Exception as exc:
        logger.warning("Figure description failed: %s", exc)
        return ""


def ocr_image_bytes_llm(image_bytes: bytes, *, mime_type: str = "image/png") -> str:
    if not llm_config.llm_enabled:
        logger.warning("LLM OCR requested but llm_enabled is false")
        return ""

    prepared, mime_type = prepare_image_for_vision_llm(
        image_bytes,
        max_dimension=llm_config.llm_ocr_max_dimension,
        max_bytes=llm_config.llm_ocr_max_bytes,
        jpeg_quality=llm_config.llm_ocr_jpeg_quality,
    )

    from twomarkdown.telemetry import span

    agent = get_image_ocr_agent()
    content = BinaryContent(data=prepared, media_type=mime_type)
    try:
        with events.stage("ocr"), _page_permit(), span("ocr.ollama"):
            result = _call_with_backoff(
                lambda: agent.run_sync(
                    ["Transcribe this page to Markdown.", content],
                ),
                what="Page OCR",
                model=llm_config.vision_model,
            )
        _report_cloud_cost(llm_config.vision_model, result)
        return strip_invented_image_links(strip_wrapping_fence(result.output or ""))
    except ModelAPIError as exc:
        logger.warning("Vision OCR API error: %s", exc)
        _record_soft_failure_for(llm_config.vision_model, exc, what="OCR")
        return ""
    except Exception as exc:
        logger.warning("Vision OCR failed: %s", exc)
        return ""
