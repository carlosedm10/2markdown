"""Request/response/event models for the local desktop-app API server.

This module is the contract: `app/src/api/types.ts` mirrors it field-for-field,
same names, same enums. Nothing here talks to the engine directly — it is the
shape data takes crossing the wire between the React frontend and the FastAPI
server that wraps `twomarkdown.*`. See `docs/desktop-app.md` for the endpoint
list, the event schema, and how a preset's `pipeline` maps onto CLI flags and
`twomarkdown/config.py` fields.

Kept deliberately flat and JSON-friendly (str | int | float | bool | None,
lists, and nested models) because it round-trips through `POST`/`GET` bodies
and one JSON-line per WebSocket frame — no datetimes-as-objects, no tuples.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

# Model ids use the engine's "<provider>:<model>" convention (see
# twomarkdown/agents/image_ocr.py:normalize_model_id). "tesseract" is the one
# bare exception: it names the CPU OCR engine, not an LLM.
ModelId = str

FileKind = Literal["text", "scanned", "image", "office", "other"]
StageKey = Literal["extract", "ocr", "figures", "review", "write"]
StageKind = Literal["none", "cpu", "gpu", "cloud"]
Bottleneck = Literal["gpu", "cloud", "cpu"]
JobMode = Literal["once"]
JobStatus = Literal["queued", "running", "paused", "done", "failed", "cancelled"]
FileStatus = Literal["ok", "warn", "failed", "cancelled"]
OpenAIStatus = Literal["ok", "no_key", "out_of_credit", "invalid_key", "unknown"]

# What a file/row IS, for the desktop queue-as-file-list UI — distinct from
# `FileKind` above (that one is `/api/inspect`'s pre-conversion classification,
# where a PDF's real kind depends on its scanned ratio and is folded into
# "text"/"scanned"). This one names the *source format* plainly, `Job.files[]`
# side, so the row can pick an icon and decide whether "pages" means anything
# for it at all (see `JobFileState.pages`'s own docstring).
JobFileKind = Literal[
    "pdf", "image", "office", "html", "text", "ebook", "mindmap", "other"
]

# Every cloud provider the engine can actually drive through pydantic-ai's
# "<provider>:<model>" id (see twomarkdown/agents/image_ocr.py's
# normalize_model_id/is_local_model) *and* that genai-prices has a matching
# provider id for (twomarkdown/server/models.py:CLOUD_PROVIDER_ENV_VARS).
CloudProviderId = Literal[
    "openai", "anthropic", "google", "groq", "mistral", "openrouter"
]
ModelKind = Literal["local", "cloud", "cpu"]
ResolveProblem = Literal[
    "not_installed",
    "no_api_key",
    "unknown_price",
    "unknown_provider",
    "sdk_not_installed",
]


# ---------------------------------------------------------------------------
# GET /api/system
# ---------------------------------------------------------------------------


class TesseractInfo(BaseModel):
    installed: bool
    version: str | None = None


class OllamaModelInfo(BaseModel):
    name: ModelId
    size_gb: float
    loaded: bool
    # Whether this installed model accepts image input, per Ollama's own
    # `/api/show` (`capabilities` there, or `details.families` on an Ollama
    # too old to report that — see `server/system._probe_vision`) — not a
    # guess from the name. Lets the Team screen's kit grid (`app/src/
    # screens/Team.tsx`) say "modelo de visión local" vs "modelo de texto
    # local" correctly for a model like `gemma3:4b` that doesn't spell
    # "vision" anywhere in its id (see N21).
    vision: bool = False


class OllamaInfo(BaseModel):
    running: bool
    version: str | None = None
    models: list[OllamaModelInfo] = Field(default_factory=list)


class OpenAIInfo(BaseModel):
    key_present: bool
    # "out_of_credit" surfaces pydantic-ai's insufficient_quota as a real,
    # displayed state (see AGENTS.md) — never retried silently, never shown
    # as a generic error.
    status: OpenAIStatus


class ProviderCloudInfo(BaseModel):
    """A cloud provider `/api/system` has no dedicated cheap probe for.

    Unlike OpenAI (`OpenAIInfo`, real out-of-credit detection), these only
    know whether a key is present — `status` is `"unknown"` whenever one is,
    since no request was actually made to confirm it works, and `"no_key"`
    when it plainly is not.
    """

    key_present: bool
    status: OpenAIStatus


class CloudInfo(BaseModel):
    openai: OpenAIInfo
    anthropic: ProviderCloudInfo
    google: ProviderCloudInfo
    groq: ProviderCloudInfo
    mistral: ProviderCloudInfo
    openrouter: ProviderCloudInfo


class PricesInfo(BaseModel):
    """Mirrors genai-prices' own update state, not a copy the app maintains."""

    source: str = "genai-prices"
    updated_at: str | None = None  # ISO 8601; None if never fetched
    unknown_models: list[ModelId] = Field(default_factory=list)


class SystemInfo(BaseModel):
    ram_gb: float
    gpu_limit_gb: float
    cpu_cores: int
    chip: str
    tesseract: TesseractInfo
    ollama: OllamaInfo
    cloud: CloudInfo
    prices: PricesInfo


# ---------------------------------------------------------------------------
# POST /api/inspect
# ---------------------------------------------------------------------------


class InspectRequest(BaseModel):
    path: str


class InspectFile(BaseModel):
    path: str
    kind: FileKind
    pages: int = 0
    scanned_pages: int = 0
    figures: int = 0
    text_chars: int = 0
    bytes: int = 0


class InspectTotals(BaseModel):
    files: int = 0
    pages: int = 0
    scanned_pages: int = 0
    figures: int = 0
    bytes: int = 0


class InspectResponse(BaseModel):
    inspect_id: str
    # The path the caller gave `/api/inspect` (a single file's own path, or a
    # directory's own path when it was walked) — the root `server/jobs.py`
    # re-resolves into `files` when `POST /api/jobs` is given `inspect_id`
    # alone, with no `path`. NEEDS a mirror field in app/src/api/types.ts's
    # `InspectResponse` (see this module's docstring).
    root: str
    files: list[InspectFile]
    totals: InspectTotals
    # The preset last used to convert this exact root path (server/
    # folder_presets.py), so the app can preselect it on the Convertir
    # screen instead of always defaulting to the first preset. `None` when
    # this path was never converted before, or its last job used a raw
    # `Pipeline` rather than a saved preset.
    last_preset_id: str | None = None
    # Where this root would land if `POST /api/jobs` were called with no
    # explicit `output` — computed by `server/settings.py:default_output_for`
    # from the persisted `Settings`, so Convertir shows (and, if the caller
    # accepts it as-is, actually gets) the exact same path the job endpoint
    # would derive on its own, instead of re-deriving the sibling/fixed-dir
    # rule client-side and risking the two disagreeing.
    default_output: str


# ---------------------------------------------------------------------------
# Pipeline — the shape a preset saves, and what /api/estimate and /api/jobs take
# ---------------------------------------------------------------------------


class Pipeline(BaseModel):
    """One saved pipeline. A preset *is* a Pipeline plus id/name/builtin.

    Field-by-field mapping onto CLI flags / twomarkdown.config lives in
    docs/desktop-app.md ("Presets map to engine config"); this model is the
    single source of truth for the shape.
    """

    ocr_model: ModelId = "tesseract"
    figure_model: ModelId | None = None
    review_model: ModelId | None = None
    workers: int = 4
    describe_figures: bool = False
    clean: bool = True
    tables: bool = True
    emit_chunks: bool = False
    ocr_dpi: int = 300


class Preset(BaseModel):
    id: str
    name: str
    builtin: bool
    pipeline: Pipeline


class PresetsUpdateRequest(BaseModel):
    presets: list[Preset]


# ---------------------------------------------------------------------------
# GET/PUT /api/settings
#
# The wizard's persisted answers — see server/settings.py's module docstring
# for why this exists ("the wizard is the first edit of Settings", not a
# separate in-memory-only store) and for `default_output_for()`, the one
# place `default_output_mode`/`default_output_dir` turn into an actual path.
# ---------------------------------------------------------------------------

OutputMode = Literal["sibling", "fixed"]
SettingsMode = Literal["once", "sync"]


class Settings(BaseModel):
    wizard_done: bool = False
    # "sibling": "<input>_2markdown" next to the input (file or folder).
    # "fixed": under one folder the user chose once — requires
    # `default_output_dir` (validated by `PUT /api/settings`, not here: the
    # cross-field rule needs both values at once, see `settings._validate`).
    default_output_mode: OutputMode = "sibling"
    # Absolute, `~`-expanded. Required when `default_output_mode == "fixed"`;
    # otherwise optional/unused.
    default_output_dir: str | None = None
    default_preset_id: str = "apuntes-a-mano"
    # Whether a fresh input defaults to a one-off `POST /api/jobs` or to
    # setting up a watched `/api/syncs` entry — the wizard's "¿una vez o
    # siempre?" question, not the same enum as `JobMode` (which is always
    # `"once"`; see docs/desktop-app.md's `POST /api/jobs` note on why
    # watched-folder conversion is a `Sync`, not a job mode).
    default_mode: SettingsMode = "once"
    # EXPERIMENTAL. Mirrors `twomarkdown.config.llm_config.local_gpu_permits`
    # — how many local (Ollama) calls `agents.image_ocr._page_ocr_lock` lets
    # run at once. Validated to 1 or 2 by `settings._validate` (anything
    # higher is unmeasured — see that field's own docstring in config.py);
    # `server.settings.get_settings`/`replace_settings` push a changed value
    # into `llm_config` and resize the semaphore whenever this resource is
    # read or replaced, so no server restart is needed to see it take
    # effect.
    local_gpu_permits: int = 1


# ---------------------------------------------------------------------------
# POST /api/estimate
# ---------------------------------------------------------------------------


EstimateScope = Literal["full", "pipeline_only"]


class EstimateRequest(BaseModel):
    # Omitted (N1) → the Pipeline editor's own use case: no folder has been
    # inspected yet (or the app just wants the GPU verdict for a pipeline
    # being edited), so `post_estimate` computes only what needs no file
    # data at all — the GPU block/warning/resident/limit fields — instead of
    # 404ing or falling back to a client-side approximation that can
    # disagree with the engine (see N1).
    inspect_id: str | None = None
    pipeline: Pipeline


class EstimateStage(BaseModel):
    key: StageKey
    model: ModelId | None = None
    kind: StageKind
    seconds: float
    # None means "genai-prices has no table for this model" — never show $0
    # for an unknown price (see docs/desktop-app.md, Round 3 decisions).
    usd: float | None = None
    unknown_price: bool = False
    # Figures-only (C18): when the figure model the pipeline picked could not
    # fit alongside OCR (and any resident review model) and
    # `gpu_memory.effective_figure_model` collapsed it onto the OCR model,
    # `effective_model` names what will actually run — the same value
    # `model` above already carries, since `seconds`/`usd`/the GPU residency
    # this stage feeds into are computed for the model that really runs, not
    # the one the user selected — and `note` is that function's ready-made
    # Spanish explanation of the substitution. Both are `None` whenever
    # nothing was substituted (including every non-"figures" stage), so a
    # client can key its UI off `note is not None` alone.
    effective_model: ModelId | None = None
    note: str | None = None


class BlockedInfo(BaseModel):
    """The pipeline's distinct local models would not all fit resident in the
    GPU at once (`batch.estimate._gpu_state`: their combined
    `RESIDENT_FACTOR`-scaled size exceeds `gpu_limit_gb`) — not merely "two of
    them", which `effective_figure_model()` already resolves for OCR+figures
    before this is ever computed."""

    title: str
    body: str


class WarningInfo(BaseModel):
    """The pipeline's local models fit, but with little headroom left on the
    GPU (`batch.estimate._gpu_state`: `gpu_headroom_gb` under 3 GB) — worth
    surfacing before a nearly-full run tips over, without refusing it."""

    title: str
    body: str


class EstimateAlternative(BaseModel):
    label: str
    pipeline_patch: dict[str, object]
    total_seconds: float
    total_usd: float | None = None


class EstimateResponse(BaseModel):
    stages: list[EstimateStage]
    total_seconds: float
    total_usd: float | None = None
    parallel_files: int
    bottleneck: Bottleneck
    blocked: BlockedInfo | None = None
    warning: WarningInfo | None = None
    alternatives: list[EstimateAlternative] = Field(default_factory=list)
    # Resident-memory arithmetic (`batch.estimate._gpu_state`), not file size:
    # the pipeline's distinct local models (post OCR/figures collapse) at
    # `RESIDENT_FACTOR`-scaled size, this machine's own `gpu_limit_gb`
    # (`system.gpu_limit_gb`, ~75% of RAM on Apple Silicon, 0 elsewhere), and
    # the difference. All 0.0 when the pipeline uses no local model.
    gpu_resident_gb: float = 0.0
    gpu_limit_gb: float = 0.0
    gpu_headroom_gb: float = 0.0
    # "full" (default) when `inspect_id` was given and every field reflects
    # real file counts; "pipeline_only" (N1) when it was omitted — `stages`'
    # seconds/usd are then 0.0/null (no file data to size them from) but
    # `blocked`/`warning`/`gpu_resident_gb`/`gpu_limit_gb`/`gpu_headroom_gb`
    # are the real engine verdict for this pipeline's models, computed the
    # same way `batch.estimate._gpu_state` always does — so the Pipeline
    # editor never has to approximate GPU residency client-side.
    estimate_scope: EstimateScope = "full"


# ---------------------------------------------------------------------------
# POST /api/jobs
# ---------------------------------------------------------------------------


class JobCreateRequest(BaseModel):
    inspect_id: str | None = None
    path: str | None = None
    # Omitted → `server/settings.py:default_output_for()` resolves it from
    # the persisted `Settings` and the job's input path, the same rule
    # `InspectResponse.default_output` already showed the caller.
    output: str | None = None
    pipeline: Pipeline | None = None
    preset_id: str | None = None
    mode: JobMode = "once"


class JobCreateResponse(BaseModel):
    job_id: str


class JobFileState(BaseModel):
    index: int
    path: str
    status: FileStatus | Literal["pending", "running"]
    # `None` for every non-paginated `kind` (office/html/text/ebook/mindmap/
    # other) — never `0`, which used to mean both "not a paginated file" and
    # "a PDF whose real page count just hasn't arrived yet" indistinguishably,
    # and which the desktop queue's row list (`app/src/screens/Review.tsx`)
    # took as "nothing to show", hiding every non-PDF file from Revisar
    # entirely. Filled in for `kind == "pdf"` once the engine's own
    # `file_started` event reports it (see `server/jobs.py`'s sink).
    pages: int | None = None
    seconds: float | None = None
    reason: str | None = None
    # The facts a queue-as-file-list row needs to act on this file without a
    # second round-trip (cancel/open/review/re-convert) — see
    # docs/desktop-app.md's "Job.files[] facts" note.
    output_path: str | None = None  # absolute .md path, once one exists
    kind: JobFileKind | None = None  # set at job creation from the source suffix
    assets_dir: str | None = None  # this file's own `<stem>_assets/`, if any
    # "tesseract" | "ollama:<model>" | "<provider>:<model>" | "none" (no
    # OCR-capable stage ever ran for this file — a native-text PDF/office
    # document, or a kind OCR never touches).
    engine_used: str | None = None
    review_changes_count: int = 0


class Job(BaseModel):
    job_id: str
    status: JobStatus
    output: str
    pipeline: Pipeline
    mode: JobMode
    files: list[JobFileState] = Field(default_factory=list)
    ok: int = 0
    warn: int = 0
    failed: int = 0
    # Files skipped (still queued) or stopped (mid-conversion) by a job-level
    # or per-file cancel — see `POST /api/jobs/{id}/cancel` and
    # `POST /api/jobs/{id}/files/{i}/cancel`. Distinct from `failed`: a
    # cancelled file is not a conversion error.
    cancelled: int = 0
    seconds: float | None = None
    usd: float | None = None
    # The rest of this model persists a *finished* job into
    # `jobs_history.json` (see `server/jobs.py`'s history module docstring)
    # so a Historial screen survives a server restart — `GET /api/jobs/{id}`
    # returns the exact same shape whether the job is still in memory or was
    # loaded back from that file.
    input: str | None = None  # the path this job was created with
    preset_id: str | None = None  # None: an ad-hoc Pipeline, not a saved preset
    started: str | None = None  # ISO 8601
    finished: str | None = None  # ISO 8601; None while still running


class JobRetryRequest(BaseModel):
    file_index: int
    page: int | None = None
    pipeline_patch: dict[str, object] | None = None
    # A saved preset's whole pipeline, as an alternative to a raw
    # `pipeline_patch` — the "re-convert this one file with another model"
    # flow picks a preset/model, not a hand-built patch dict. Takes priority
    # over `pipeline_patch` when both are sent.
    preset_id: str | None = None
    # Required once `PUT .../pages/{n}` has saved an edit to this file/page
    # since its last conversion — a re-read regenerates that page's Markdown
    # from a fresh OCR pass and would otherwise silently drop the saved edit
    # (see N10). The server responds 409 (`server.jobs.UnsavedPageEditError`)
    # instead of retrying when this is left `False` on such a page; the
    # caller re-sends the same request with `confirm=True` to proceed anyway.
    # `page=None` (a whole-file retry) checks every page of that file.
    confirm: bool = False


# ---------------------------------------------------------------------------
# WS /api/jobs/{id}/events — one JSON object per line, `type` discriminates
# ---------------------------------------------------------------------------


class EventJobStarted(BaseModel):
    type: Literal["job_started"] = "job_started"
    job_id: str


class EventFileStarted(BaseModel):
    type: Literal["file_started"] = "file_started"
    index: int
    path: str
    pages: int


class EventPageStarted(BaseModel):
    type: Literal["page_started"] = "page_started"
    index: int
    page: int
    # The pipeline's own `ocr_model` for this run ("tesseract",
    # "ollama:<model>", or "<provider>:<model>") — added (N10) so the UI can
    # say "Leyendo con Tesseract…" for a Tesseract-only run instead of always
    # "Leyendo con IA…", which used to be wrong whenever no model was ever
    # involved. `server/jobs.py`'s `_make_sink` stamps this onto the event
    # from `Job.pipeline.ocr_model` before it reaches any subscriber, so
    # every emitter of `page_started` gets it for free. `None` only for an
    # event built before that stamping existed (never in practice — kept
    # optional so an old buffered/replayed event still validates).
    engine: ModelId | None = None


class ReviewChange(BaseModel):
    line: int
    before: str
    after: str


class EventPageDone(BaseModel):
    type: Literal["page_done"] = "page_done"
    index: int
    page: int
    engine: ModelId
    seconds: float
    fallback: bool = False
    review_changes: list[ReviewChange] = Field(default_factory=list)


class EventFileDone(BaseModel):
    type: Literal["file_done"] = "file_done"
    index: int
    status: FileStatus
    seconds: float
    reason: str | None = None


class SemaphoreHolder(BaseModel):
    file: str
    page: int | None = None
    stage: StageKey


class GpuSemState(BaseModel):
    permits: int
    held_by: list[SemaphoreHolder] = Field(default_factory=list)
    waiting: int = 0


class CloudSemState(BaseModel):
    permits: int
    in_use: int = 0


class CpuSemState(BaseModel):
    workers: int
    active: int = 0


class EventSemaphores(BaseModel):
    type: Literal["semaphores"] = "semaphores"
    gpu: GpuSemState
    cloud: CloudSemState
    cpu: CpuSemState


class EventCost(BaseModel):
    type: Literal["cost"] = "cost"
    usd_so_far: float


class EventLog(BaseModel):
    type: Literal["log"] = "log"
    level: Literal["debug", "info", "warning", "error"]
    message: str


class EventJobDone(BaseModel):
    type: Literal["job_done"] = "job_done"
    ok: int
    warn: int
    failed: int
    cancelled: int = 0
    seconds: float
    usd: float | None = None


JobEvent = (
    EventJobStarted
    | EventFileStarted
    | EventPageStarted
    | EventPageDone
    | EventFileDone
    | EventSemaphores
    | EventCost
    | EventLog
    | EventJobDone
)


# ---------------------------------------------------------------------------
# GET/PUT /api/jobs/{id}/files/{i}/pages/{n} — the Revisar screen
# ---------------------------------------------------------------------------


class PageChange(BaseModel):
    """One diff line from describe_changes() — the real diff, not a claim."""

    before: str
    after: str


class PageDetail(BaseModel):
    image_png_base64: str | None = None
    image_url: str | None = None
    markdown: str
    changes: list[PageChange] = Field(default_factory=list)
    # Total pages found in this file (`_PAGE_HEADING_RE` matches), or `None`
    # when the file has no `## Page N` markers at all — a non-paginated
    # file's *whole* markdown is still returned in `markdown` in that case,
    # not a 404 (see N-review-scope: Revisar only ever showed scanned PDFs
    # before this, since the frontend read a page count of 0/1 as "nothing
    # to page through" indistinguishably from "this file has no pages").
    pages: int | None = None


class PageUpdateRequest(BaseModel):
    markdown: str


# ---------------------------------------------------------------------------
# GET/POST/DELETE /api/syncs
# ---------------------------------------------------------------------------


class Sync(BaseModel):
    id: str
    input: str
    output: str
    preset_id: str
    enabled: bool = True
    last_run: str | None = None  # ISO 8601
    files_today: int = 0


class SyncCreateRequest(BaseModel):
    input: str
    output: str
    preset_id: str
    enabled: bool = True


class SyncUpdateRequest(BaseModel):
    """Fields a `PATCH /api/syncs/{id}` may flip in place — today just
    `enabled` (see docs/desktop-app.md's endpoint notes, M8)."""

    enabled: bool | None = None


# ---------------------------------------------------------------------------
# POST /api/prices/manual
# ---------------------------------------------------------------------------


class ManualPriceRequest(BaseModel):
    model: ModelId
    input_per_mtok: float
    output_per_mtok: float


# ---------------------------------------------------------------------------
# POST /api/pick-folder, POST /api/ollama/pull, POST /api/cloud/openai/key
#
# Not in docs/desktop-app.md's endpoint table (frontend-only conveniences
# named in app/src/api/contract.ts) — added so those calls resolve instead of
# 404ing. See twomarkdown/server/host.py.
# ---------------------------------------------------------------------------


class PickFolderResponse(BaseModel):
    path: str | None


class OllamaPullRequest(BaseModel):
    model: str


class OllamaPullResponse(BaseModel):
    ok: bool
    error: str | None = None


class OpenAIKeyRequest(BaseModel):
    key: str


class OpenAIKeyResponse(BaseModel):
    ok: bool


# ---------------------------------------------------------------------------
# POST/DELETE /api/cloud/{provider}/key — every provider in
# `server/models.py:CLOUD_PROVIDER_ENV_VARS`, not just OpenAI (that endpoint
# above is kept working as a plain alias for `provider="openai"`). The env
# var name each provider's key is stored under is a backend-only detail —
# the UI never sees or asks for it, only `provider`/`key_present`/`status`.
# ---------------------------------------------------------------------------


class CloudKeyRequest(BaseModel):
    key: str


class CloudKeyResponse(BaseModel):
    provider: CloudProviderId
    key_present: bool
    # Cheap validation is best-effort and provider-specific (see
    # `server/host.py:probe_cloud_key`) — "unknown" whenever no free
    # validation call exists yet, the call failed to connect, or it timed
    # out, same "never a guess" rule as `ResolveModelResponse.vision`.
    status: OpenAIStatus


# ---------------------------------------------------------------------------
# GET /api/models, POST /api/models/resolve
#
# Any model, not a hardcoded list: `local` mirrors `ollama_info()`'s already-
# installed models, `cloud` is the genai-prices catalog restricted to the
# providers this engine can actually drive, and `providers`/`stages` are what
# the app needs to group and gray out choices. See
# twomarkdown/server/models.py.
# ---------------------------------------------------------------------------


class LocalModelOption(BaseModel):
    id: ModelId  # "ollama:<name>"
    name: str
    size_gb: float
    loaded: bool
    vision: bool
    installed: bool = True


class CloudModelOption(BaseModel):
    id: ModelId  # "<provider>:<model>"
    provider: CloudProviderId
    # None means genai-prices exposes no modality/capability field for this
    # model — unknown, never a guessed False (see docstring on
    # ResolveModelResponse.vision below for the same rule).
    vision: bool | None = None
    price_known: bool
    key_present: bool


class ProviderStatus(BaseModel):
    id: CloudProviderId
    key_present: bool
    env_var: str


class ModelStages(BaseModel):
    """Which model ids make sense per pipeline stage.

    `ocr` = "tesseract" plus every vision-capable model (local or cloud;
    `vision is not False`, so an unknown-vision cloud model is offered, not
    hidden). `figures` is the same minus "tesseract" (there is no CPU figure
    describer). `review` is every model, local or cloud — the proofreader
    never sees pixels, so a text-only model is enough.
    """

    ocr: list[ModelId] = Field(default_factory=list)
    figures: list[ModelId] = Field(default_factory=list)
    review: list[ModelId] = Field(default_factory=list)


class ModelsResponse(BaseModel):
    local: list[LocalModelOption] = Field(default_factory=list)
    cloud: list[CloudModelOption] = Field(default_factory=list)
    providers: list[ProviderStatus] = Field(default_factory=list)
    stages: ModelStages


class ResolveModelRequest(BaseModel):
    id: ModelId


class ResolveModelResponse(BaseModel):
    """Validates a free-text model id before it is saved into a preset.

    Always 200 — even an id this app cannot drive at all comes back with
    `problems: ["unknown_provider"]` rather than a 4xx, so the "Otro
    modelo…" form can show an inline warning instead of a failed request.
    A recognised `CLOUD_PROVIDER_ENV_VARS` provider whose pydantic-ai
    provider class fails to import (its SDK extra isn't installed) comes
    back with `problems: ["sdk_not_installed"]` instead — the catalog
    advertised it, but this engine cannot actually drive it.
    """

    id: ModelId  # normalized via normalize_model_id (except "tesseract")
    kind: ModelKind
    installed: bool | None = None  # None: unknown/not applicable (cloud)
    key_present: bool | None = None  # None: not applicable (local/cpu)
    price_known: bool
    vision: bool | None = None
    problems: list[ResolveProblem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# GET/DELETE /api/folders — which preset a folder was last converted with
#
# Populated from POST /api/jobs (mode "once"): server/folder_presets.py.
# ---------------------------------------------------------------------------


class FolderPreset(BaseModel):
    path: str
    preset_id: str | None  # None when the job used a raw Pipeline, not a preset
    last_used: str  # ISO 8601


# ---------------------------------------------------------------------------
# GET /api/jobs/{id}/files/{i}/original — the "ver original" panel
#
# Three shapes in one model, picked by `kind`:
# * `kind == "pdf"`: `pages` is set, `previewable` stays `True` — the app then
#   pages through the existing `GET .../pages/{n}` page-image endpoint itself.
# * `kind == "image"`: never reaches this model — `GET .../original` answers
#   with the raw image bytes (`FileResponse`) instead.
# * everything else: `previewable=False` plus `path`, so the app can offer
#   "abrir con la app del sistema" (`POST /api/open`) instead of an inline
#   preview it has no renderer for.
# ---------------------------------------------------------------------------


class OriginalInfo(BaseModel):
    kind: JobFileKind
    previewable: bool = True
    pages: int | None = None
    path: str | None = None


# ---------------------------------------------------------------------------
# POST /api/open, POST /api/reveal — open/reveal a path with the OS, guarded
# to a job's own input/output roots or the user's home (see server/host.py).
# ---------------------------------------------------------------------------


class OpenPathRequest(BaseModel):
    path: str


class OpenPathResponse(BaseModel):
    ok: bool
    error: str | None = None
