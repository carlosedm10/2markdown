"""GET /api/models, POST /api/models/resolve — any model, not a hardcoded list.

Before this module, "which model can I pick" meant a list the app shipped
with by hand. Two sources already know the real answer, so this module just
asks them:

* **Local** — `system.ollama_info()` already probes every installed Ollama
  model (size, whether it's loaded, and whether it accepts image input via
  `/api/show`) for `/api/system`; reused verbatim here, just reshaped.
* **Cloud** — `genai_prices.data.providers`, the pricing catalog
  `pydantic-ai` already depends on, bundled with the package and read
  entirely offline (see `system.prices_info`'s own docstring — this module
  never starts genai-prices' background updater either). Restricted to the
  providers this engine can actually drive through
  `twomarkdown.agents.image_ocr.normalize_model_id`'s `"<provider>:<model>"`
  convention: `CLOUD_PROVIDER_ENV_VARS` below is that curated list, not every
  provider genai-prices or pydantic-ai happens to know about.

genai-prices' `ModelInfo` carries no modality/capability field (no "this
model accepts images" flag) as of this writing, so every cloud model's
`vision` is `None` — unknown, never a guessed `False` — while a local
model's `vision` is a real, probed `bool` (Ollama's own `/api/show` answers
that for an installed model). `POST /api/models/resolve` lets the app check
a free-text id (the "Otro modelo…" flow) against the same two sources
before saving it into a preset.

Cached for `_CACHE_TTL` seconds like the other `/api/system` probes — a
desktop screen that polls this on every render should not re-walk the whole
genai-prices catalog and re-probe Ollama on every frame.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from twomarkdown.agents.image_ocr import DEFAULT_PROVIDER, normalize_model_id
from twomarkdown.server import system
from twomarkdown.server.schemas import (
    CloudModelOption,
    LocalModelOption,
    ModelsResponse,
    ModelStages,
    ProviderStatus,
    ResolveModelResponse,
)

logger = logging.getLogger(__name__)

# Cloud providers this app can actually drive: a pydantic-ai provider class
# exists for each (normalize_model_id's own check, via infer_provider_class)
# *and* genai-prices has a matching `Provider.id` to price it against. Adding
# a provider means adding it here (env var pydantic-ai itself reads it from —
# see each Provider class's own docstring) — nothing else needs to change,
# since `/api/system`'s `CloudInfo` (schemas.py) and `.env_template` already
# name the same six.
CLOUD_PROVIDER_ENV_VARS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

_CACHE_TTL = 60.0
_lock = threading.Lock()
_cache: tuple[float, ModelsResponse] | None = None


def _local_models() -> list[LocalModelOption]:
    info = system.ollama_info()
    return [
        LocalModelOption(
            id=f"{DEFAULT_PROVIDER}:{m.name}",
            name=m.name,
            size_gb=m.size_gb,
            loaded=m.loaded,
            vision=m.vision,
            installed=True,
        )
        for m in info.models
    ]


def _resold_from_elsewhere(
    provider: Any, model_id: str, all_providers: dict[str, Any]
) -> bool:
    """True when `model_id` sits in `provider.models` only because
    genai-prices also uses that list as a *pricing* fallback for another
    provider's own models — its own `Provider.fallback_model_providers`
    docstring: "used when one provider offers another provider's models,
    e.g. Google and AWS offer Anthropic models". Vertex AI's Claude resale
    is exactly that: it shows up inside the "google" provider entry's
    `.models`, priced identically to Anthropic's own catalog, even though
    this app's Google integration (`GOOGLE_API_KEY`, the Gemini Developer
    API, not Vertex) cannot invoke a Claude model at all.

    Scoped tightly to avoid excluding a provider's own real models: it only
    fires when `provider.fallback_model_providers` names another provider
    *and* that other provider's own `model_match` (the pattern genai-prices
    itself uses to recognise its family, e.g. anthropic's "contains
    'claude'") claims `model_id`. A provider with no fallback list (Groq,
    OpenRouter, ...) is never touched — OpenRouter's own catalog genuinely
    does proxy `claude-*`/`gemini-*` ids for real, one `OPENROUTER_API_KEY`
    away, so the same "foreign-looking id" is correct to keep there.
    """
    for other_id in getattr(provider, "fallback_model_providers", None) or []:
        other = all_providers.get(other_id)
        if (
            other is not None
            and other.model_match
            and other.model_match.is_match(model_id)
        ):
            return True
    return False


def _cloud_models() -> list[CloudModelOption]:
    try:
        from genai_prices import data as prices_data
    except Exception as exc:
        logger.debug("genai-prices catalog unavailable: %s", exc)
        return []

    all_providers = {p.id: p for p in prices_data.providers}

    options: list[CloudModelOption] = []
    for provider in prices_data.providers:
        if provider.id not in CLOUD_PROVIDER_ENV_VARS:
            continue
        key_present = system.key_present(CLOUD_PROVIDER_ENV_VARS[provider.id])
        for model in provider.models or []:
            if _resold_from_elsewhere(provider, model.id, all_providers):
                continue
            options.append(
                CloudModelOption(
                    id=f"{provider.id}:{model.id}",
                    provider=provider.id,
                    # genai-prices has no modality/capability field on
                    # ModelInfo as of this writing — unknown, not a guess.
                    vision=None,
                    price_known=model.prices is not None,
                    key_present=key_present,
                )
            )
    return options


def _provider_sdk_installed(provider_id: str) -> bool:
    """Whether pydantic-ai can actually drive `provider_id` in this
    environment — i.e. `infer_provider_class` imports cleanly.

    `CLOUD_PROVIDER_ENV_VARS` only promises pydantic-ai *recognises* the
    provider id; the provider's own SDK (e.g. `groq`, `mistralai`) is an
    optional pydantic-ai-slim extra that may not be installed (see
    `tests/test_server.py::test_cloud_providers_import` and
    `docs/desktop-app.md`). Importing the class is enough to prove the
    extra is present without instantiating a client or touching the
    network.
    """
    try:
        from pydantic_ai.providers import infer_provider_class

        infer_provider_class(provider_id)
    except Exception:
        return False
    return True


def _provider_statuses() -> list[ProviderStatus]:
    return [
        ProviderStatus(
            id=provider_id, key_present=system.key_present(env_var), env_var=env_var
        )
        for provider_id, env_var in CLOUD_PROVIDER_ENV_VARS.items()
    ]


def _stages(
    local: list[LocalModelOption], cloud: list[CloudModelOption]
) -> ModelStages:
    # `vision is not False` (not `vision`) so an unknown-vision cloud model
    # (always the case today — see module docstring) is still offered for
    # OCR/figures rather than hidden for a fact genai-prices simply doesn't
    # expose.
    vision_capable = [m.id for m in local if m.vision] + [
        m.id for m in cloud if m.vision is not False
    ]
    review_capable = [m.id for m in local] + [m.id for m in cloud]
    return ModelStages(
        ocr=["tesseract"] + vision_capable,
        figures=list(vision_capable),
        review=review_capable,
    )


def get_models_info() -> ModelsResponse:
    global _cache
    with _lock:
        cached = _cache
        if cached is not None and (time.monotonic() - cached[0]) < _CACHE_TTL:
            return cached[1]

    local = _local_models()
    cloud = _cloud_models()
    response = ModelsResponse(
        local=local,
        cloud=cloud,
        providers=_provider_statuses(),
        stages=_stages(local, cloud),
    )
    with _lock:
        _cache = (time.monotonic(), response)
    return response


def _catalog_price_known(model_id: str) -> bool:
    """Same manual-price-first, then-genai-prices rule `estimate._cloud_price`
    prices a real job with — reused rather than re-implemented so a preset's
    resolved `price_known` never disagrees with what `/api/estimate` will
    actually charge for it."""
    from twomarkdown.batch.estimate import _cloud_price

    _usd, unknown = _cloud_price(model_id, 1, 1)
    return not unknown


def resolve_model(raw: str) -> ResolveModelResponse:
    """Validate a free-text model id (the "Otro modelo…" flow) before a
    preset saves it — always 200, even for an id this app cannot drive at
    all (see `ResolveModelResponse`'s own docstring)."""
    raw = (raw or "").strip()

    if raw == "tesseract":
        installed = system.tesseract_info().installed
        return ResolveModelResponse(
            id="tesseract",
            kind="cpu",
            installed=installed,
            key_present=None,
            price_known=True,
            vision=None,
            problems=[] if installed else ["not_installed"],
        )

    normalized = normalize_model_id(raw)
    prefix = normalized.split(":", 1)[0] if normalized else ""

    if prefix in CLOUD_PROVIDER_ENV_VARS:
        problems: list[str] = []
        if not _provider_sdk_installed(prefix):
            # The catalog (CLOUD_PROVIDER_ENV_VARS) advertises this provider,
            # but pydantic-ai's own provider class won't import — its SDK
            # extra isn't installed in this environment (see this module's
            # own docstring). Report the real problem instead of pretending
            # the id is fine: no_api_key/unknown_price would both be
            # misleading here.
            return ResolveModelResponse(
                id=normalized,
                kind="cloud",
                installed=None,
                key_present=None,
                price_known=False,
                vision=None,
                problems=["sdk_not_installed"],
            )
        key_present = system.key_present(CLOUD_PROVIDER_ENV_VARS[prefix])
        price_known = _catalog_price_known(normalized)
        if not key_present:
            problems.append("no_api_key")
        if not price_known:
            problems.append("unknown_price")
        return ResolveModelResponse(
            id=normalized,
            kind="cloud",
            installed=None,
            key_present=key_present,
            price_known=price_known,
            vision=None,
            problems=problems,
        )

    if prefix == DEFAULT_PROVIDER:
        # normalize_model_id's own fallback rule: a bare name, or one whose
        # prefix pydantic-ai doesn't recognise as any provider at all, reads
        # as an Ollama model name (see its docstring) — that includes a
        # genuinely tagged local model like "qwen2.5vl:7b" (the ":7b" is a
        # tag, not a provider prefix), so this is not itself a problem.
        model_name = normalized.split(":", 1)[-1]
        match = next(
            (m for m in system.ollama_info().models if m.name == model_name), None
        )
        return ResolveModelResponse(
            id=normalized,
            kind="local",
            installed=match is not None,
            key_present=None,
            price_known=True,
            vision=(match.vision if match else None),
            problems=[] if match is not None else ["not_installed"],
        )

    # normalize_model_id only ever leaves a prefix in place, unfolded to
    # Ollama, when pydantic-ai's own infer_provider_class recognises it as a
    # real provider (e.g. "cohere", "deepseek") — this app just doesn't have
    # a genai-prices catalog id or env var wired for it above.
    return ResolveModelResponse(
        id=normalized,
        kind="cloud",
        installed=None,
        key_present=None,
        price_known=False,
        vision=None,
        problems=["unknown_provider"],
    )
