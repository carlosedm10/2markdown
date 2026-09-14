/**
 * Request/response/event shapes for the local desktop-app API server.
 *
 * Mirrors `twomarkdown/server/schemas.py` field-for-field — same names, same
 * enums. Keep the two in sync; see `docs/desktop-app.md` for the endpoint
 * list, the event schema, and how a preset's `Pipeline` maps onto CLI flags
 * and `twomarkdown/config.py` fields. The server always runs at
 * `http://127.0.0.1:8765`.
 */

// ---------------------------------------------------------------------------
// Shared vocabulary
// ---------------------------------------------------------------------------

/** "<provider>:<model>", e.g. "ollama:qwen2.5vl:7b", "openai:gpt-4o-mini". The
 * one exception is the bare string "tesseract", which names the CPU OCR
 * engine rather than an LLM. */
export type ModelId = string;

export type FileKind = "text" | "scanned" | "image" | "office" | "other";
export type StageKey = "extract" | "ocr" | "figures" | "review" | "write";
export type StageKind = "none" | "cpu" | "gpu" | "cloud";
export type Bottleneck = "gpu" | "cloud" | "cpu";
export type JobMode = "once";
export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "done"
  | "failed"
  | "cancelled";
export type FileStatus = "ok" | "warn" | "failed" | "cancelled";
export type OpenAIStatus = "ok" | "no_key" | "out_of_credit" | "invalid_key" | "unknown";

/** What a file/row IS for the desktop queue-as-file-list UI (Job.files[]) —
 * distinct from `FileKind` above (that one is /api/inspect's pre-conversion
 * classification). Picks the row's icon and whether "pages" means anything
 * for it at all. */
export type JobFileKind = "pdf" | "image" | "office" | "html" | "text" | "ebook" | "mindmap" | "other";

/** Every cloud provider the engine can drive. */
export type CloudProviderId = "openai" | "anthropic" | "google" | "groq" | "mistral" | "openrouter";

export type ModelKind = "local" | "cloud" | "cpu";
export type ResolveProblem =
  | "not_installed"
  | "no_api_key"
  | "unknown_price"
  | "unknown_provider"
  | "sdk_not_installed";

// ---------------------------------------------------------------------------
// GET /api/system
// ---------------------------------------------------------------------------

export interface TesseractInfo {
  installed: boolean;
  version: string | null;
}

export interface OllamaModelInfo {
  name: ModelId;
  size_gb: number;
  loaded: boolean;
  /** Probed from Ollama's own /api/show capabilities (see
   * twomarkdown/server/schemas.py's OllamaModelInfo). Optional only for an
   * older server that predates this field — the UI falls back to a
   * name-based guess in that case only (see Ajustes.tsx's isVisionModel). */
  vision?: boolean;
}

export interface OllamaInfo {
  running: boolean;
  version: string | null;
  models: OllamaModelInfo[];
}

export interface OpenAIInfo {
  key_present: boolean;
  /** "out_of_credit" is a real state to surface (insufficient_quota), never
   * retried silently and never shown as a generic error. */
  status: OpenAIStatus;
}

/** A cloud provider /api/system has no dedicated cheap probe for — only
 * knows whether a key is present. */
export interface ProviderCloudInfo {
  key_present: boolean;
  status: OpenAIStatus;
}

export interface CloudInfo {
  openai: OpenAIInfo;
  anthropic: ProviderCloudInfo;
  google: ProviderCloudInfo;
  groq: ProviderCloudInfo;
  mistral: ProviderCloudInfo;
  openrouter: ProviderCloudInfo;
}

export interface PricesInfo {
  source: "genai-prices";
  updated_at: string | null; // ISO 8601
  unknown_models: ModelId[];
}

export interface SystemInfo {
  ram_gb: number;
  gpu_limit_gb: number;
  cpu_cores: number;
  chip: string;
  tesseract: TesseractInfo;
  ollama: OllamaInfo;
  cloud: CloudInfo;
  prices: PricesInfo;
}

// ---------------------------------------------------------------------------
// POST /api/inspect
// ---------------------------------------------------------------------------

export interface InspectRequest {
  path: string;
}

export interface InspectFile {
  path: string;
  kind: FileKind;
  pages: number;
  scanned_pages: number;
  figures: number;
  text_chars: number;
  bytes: number;
}

export interface InspectTotals {
  files: number;
  pages: number;
  scanned_pages: number;
  figures: number;
  bytes: number;
}

export interface InspectResponse {
  inspect_id: string;
  files: InspectFile[];
  totals: InspectTotals;
  /** The folder/file that was inspected. */
  root: string;
  /** The preset last used for this exact folder, from the engine's
   * per-folder memory (GET/DELETE /api/folders). `null` the first time a
   * folder is inspected. */
  last_preset_id?: string | null;
  /** Server-resolved output path for this folder/file, computed from
   * `Settings.default_output_mode`/`default_output_dir` — never re-derived
   * client-side (see Settings below and Convert.tsx). */
  default_output: string;
}

// ---------------------------------------------------------------------------
// GET/PUT /api/settings — the wizard's first (and only) edit of persistent
// app-wide defaults. PUT is a full replace, not a patch: callers must send
// every field. See docs/desktop-app.md and Wizard.tsx/Convert.tsx/Ajustes.tsx.
// ---------------------------------------------------------------------------

export type OutputMode = "sibling" | "fixed";
export type DefaultMode = "once" | "sync";

export interface Settings {
  wizard_done: boolean;
  default_output_mode: OutputMode;
  default_output_dir: string | null;
  default_preset_id: string;
  default_mode: DefaultMode;
  /** Experimental, unvalidated knob (1 or 2) mirroring
   * `twomarkdown.config.llm_config.local_gpu_permits` — the permit count for
   * `agents/image_ocr.py`'s `_page_ocr_lock`. Not part of the wizard flow;
   * only Ajustes › Avanzado exposes it (see docs/desktop-app.md's Settings
   * section and its GPU-permits note). `PUT` is a full replace, so every
   * caller must read-modify-write this field, never drop it. Default `1`. */
  local_gpu_permits: number;
}

/** PUT /api/settings body — a full replace, same shape as `Settings`. */
export type SettingsUpdateRequest = Settings;

/** Shape of a 422 `detail` from PUT /api/settings (see docs/desktop-app.md's
 * error-detail convention) — surfaced via `ApiError.detail` (api/client.ts). */
export interface SettingsValidationDetail {
  code: string;
  field: string;
  message: string;
}

// ---------------------------------------------------------------------------
// GET /api/folders, DELETE /api/folders?path= — per-folder preset memory
// ---------------------------------------------------------------------------

/** One remembered folder → preset association. Distinct from a Sync: a
 * folder entry only remembers which preset was last used there (no
 * watching, no auto-convert) — see Ajustes.tsx's "Carpetas" list. */
export interface FolderInfo {
  path: string;
  preset_id: string | null;
  last_used: string; // ISO 8601
}

// ---------------------------------------------------------------------------
// Pipeline — the shape a preset saves, and what /api/estimate and /api/jobs take
// ---------------------------------------------------------------------------

export interface Pipeline {
  ocr_model: ModelId;
  figure_model: ModelId | null;
  review_model: ModelId | null;
  workers: number;
  describe_figures: boolean;
  clean: boolean;
  tables: boolean;
  emit_chunks: boolean;
  ocr_dpi: number;
}

export interface Preset {
  id: string;
  name: string;
  builtin: boolean;
  pipeline: Pipeline;
}

export interface PresetsUpdateRequest {
  presets: Preset[];
}

// ---------------------------------------------------------------------------
// GET /api/models, POST /api/models/resolve — model catalog for ModelPicker
// (app/src/components/ModelPicker.tsx). Mirrors the engine agent's
// twomarkdown/server/schemas.py shape field-for-field; see that module's
// docstring for the source of truth.
// ---------------------------------------------------------------------------

export interface LocalModelInfo {
  id: ModelId;
  name: string;
  size_gb: number;
  loaded: boolean;
  vision: boolean;
  installed: boolean;
}

export interface CloudModelInfo {
  id: ModelId;
  provider: CloudProviderId;
  vision: boolean | null;
  price_known: boolean;
  key_present: boolean;
}

export interface ModelProviderInfo {
  id: CloudProviderId;
  key_present: boolean;
  env_var: string;
}

/** Which model ids each pipeline stage accepts — the server's own notion of
 * "valid for this stage" (e.g. only `tesseract` and vision models make
 * sense for `ocr`), not something ModelPicker guesses on its own. */
export interface ModelStages {
  ocr: ModelId[];
  figures: ModelId[];
  review: ModelId[];
}

export interface ModelsResponse {
  local: LocalModelInfo[];
  cloud: CloudModelInfo[];
  providers: ModelProviderInfo[];
  stages: ModelStages;
}

export type ModelResolveKind = ModelKind;
export type ModelProblem = ResolveProblem;

export interface ResolveModelRequest {
  id: ModelId;
}

export interface ResolveModelResponse {
  id: ModelId;
  kind: ModelResolveKind;
  installed: boolean | null;
  key_present: boolean | null;
  price_known: boolean;
  vision: boolean | null;
  problems: ModelProblem[];
}

// ---------------------------------------------------------------------------
// POST /api/estimate
// ---------------------------------------------------------------------------

export interface EstimateRequest {
  // Optional so the Pipeline editor can ask for a GPU verdict before any
  // folder has been inspected — the engine's pipeline-only estimate mode
  // (see EstimateResponse.estimate_scope). Older/current servers that don't
  // support this yet reject a missing inspect_id; callers must degrade
  // gracefully (see app/src/screens/Pipeline.tsx) rather than assume it
  // always succeeds.
  inspect_id?: string;
  pipeline: Pipeline;
}

export interface EstimateStage {
  key: StageKey;
  model: ModelId | null;
  kind: StageKind;
  seconds: number;
  /** null means "genai-prices has no table for this model" — never $0 for an
   * unknown price. */
  usd: number | null;
  unknown_price: boolean;
  /** The model this stage actually ran (may differ from the pipeline's own
   * `figure_model` when the engine silently collapsed the figures stage onto
   * OCR's model to fit the GPU — `gpu_memory.effective_figure_model()`).
   * Optional: older servers, and stages that were never substituted, omit it.
   * `null`/absent both mean "not substituted"; only present+non-null on the
   * figures stage is treated as a substitution by the UI. */
  effective_model?: ModelId | null;
  /** Human-readable explanation of the substitution above (the same string
   * `gpu_memory.effective_figure_model()` already computes server-side) —
   * e.g. "Se usará qwen2.5vl:7b: qwen2.5vl:32b no cabe junto al OCR (29 + 8 >
   * 36 GB)". Optional for the same reason as `effective_model`. */
  note?: string | null;
}

/** `POST /api/estimate` refused the pipeline: its distinct local models
 * would not all fit resident in the GPU at once (`batch/estimate.py`'s
 * `_gpu_state` — their combined `RESIDENT_FACTOR`-scaled size exceeds
 * `gpu_limit_gb`). Rendered as the red blocked state; `total_seconds`,
 * `total_usd`, `parallel_files` and `bottleneck` are not meaningful. */
export interface BlockedInfo {
  title: string;
  body: string;
}

/** The pipeline fits, but with under 3 GB of GPU headroom left
 * (`gpu_headroom_gb`) — a non-fatal heads-up rendered as an amber note, never
 * a reason to disable "Convertir". Same shape as `BlockedInfo`; the two are
 * mutually exclusive on one response. */
export type WarningInfo = BlockedInfo;

export interface EstimateAlternative {
  label: string;
  pipeline_patch: Partial<Pipeline>;
  total_seconds: number;
  total_usd: number | null;
}

export interface EstimateResponse {
  stages: EstimateStage[];
  total_seconds: number;
  total_usd: number | null;
  parallel_files: number;
  bottleneck: Bottleneck;
  blocked: BlockedInfo | null;
  warning: WarningInfo | null;
  alternatives: EstimateAlternative[];
  gpu_resident_gb: number;
  gpu_limit_gb: number;
  gpu_headroom_gb: number;
  // "pipeline_only" when the request carried no inspect_id: only the GPU
  // fields above (and `blocked`/`warning`) are meaningful, everything
  // file-count/time/cost-shaped is a placeholder. Optional/absent on a
  // server that doesn't support the pipeline-only mode yet — treat missing
  // the same as "full" (see app/src/screens/Pipeline.tsx).
  estimate_scope?: "full" | "pipeline_only";
}

// ---------------------------------------------------------------------------
// POST /api/jobs
// ---------------------------------------------------------------------------

export interface JobCreateRequest {
  inspect_id?: string | null;
  path?: string | null;
  output?: string | null;
  pipeline?: Pipeline | null;
  preset_id?: string | null;
  mode: JobMode;
}

export interface JobCreateResponse {
  job_id: string;
}

export interface JobFileState {
  index: number;
  path: string;
  status: FileStatus | "pending" | "running";
  /** `null` for every non-paginated `kind` (office/html/text/ebook/mindmap/
   * other) — never `0`. Never show "0 páginas" for a non-paginated kind;
   * show "—" instead. */
  pages: number | null;
  seconds: number | null;
  reason: string | null;
  /** Absolute .md path, once one exists. */
  output_path: string | null;
  /** Set at job creation from the source suffix. */
  kind: JobFileKind | null;
  /** This file's own `<stem>_assets/`, if any. */
  assets_dir: string | null;
  /** "tesseract" | "ollama:<model>" | "<provider>:<model>" | "none". */
  engine_used: string | null;
  review_changes_count: number;
}

export interface Job {
  job_id: string;
  status: JobStatus;
  output: string;
  pipeline: Pipeline;
  mode: JobMode;
  files: JobFileState[];
  ok: number;
  warn: number;
  failed: number;
  /** Files skipped (still queued) or stopped (mid-conversion) by a job-level
   * or per-file cancel. Distinct from `failed`. */
  cancelled: number;
  seconds: number | null;
  usd: number | null;
  /** Historial fields — present once a job persists past this session. */
  input: string | null;
  preset_id: string | null;
  started: string | null; // ISO 8601
  finished: string | null; // ISO 8601; null while still running
}

export interface JobRetryRequest {
  file_index: number;
  page?: number | null;
  pipeline_patch?: Partial<Pipeline> | null;
  /** A saved preset's whole pipeline, as an alternative to a raw
   * `pipeline_patch`. Takes priority over `pipeline_patch` when both are
   * sent. */
  preset_id?: string | null;
  /** Set once the user has accepted the "this will overwrite your saved
   * edits" warning (see docs/desktop-app.md, N10) — without it the server
   * answers 409 when the page has unsaved-on-disk edits. */
  confirm?: boolean;
}

// ---------------------------------------------------------------------------
// WS /api/jobs/{id}/events — one JSON object per line, `type` discriminates
// ---------------------------------------------------------------------------

export interface EventJobStarted {
  type: "job_started";
  job_id: string;
}

export interface EventFileStarted {
  type: "file_started";
  index: number;
  path: string;
  pages: number;
}

export interface EventPageStarted {
  type: "page_started";
  index: number;
  page: number;
  /** The engine driving this page, e.g. "tesseract", "ollama:qwen2.5vl:7b",
   * "openai:gpt-4o" — same id shape as EventPageDone.engine. null before the
   * server has picked one to report yet. */
  engine: ModelId | null;
}

export interface ReviewChange {
  line: number;
  before: string;
  after: string;
}

export interface EventPageDone {
  type: "page_done";
  index: number;
  page: number;
  engine: ModelId;
  seconds: number;
  fallback: boolean;
  review_changes: ReviewChange[];
}

export interface EventFileDone {
  type: "file_done";
  index: number;
  status: FileStatus;
  seconds: number;
  reason?: string | null;
}

export interface SemaphoreHolder {
  file: string;
  page: number | null;
  stage: StageKey;
}

export interface GpuSemState {
  permits: number;
  held_by: SemaphoreHolder[];
  waiting: number;
}

export interface CloudSemState {
  permits: number;
  in_use: number;
}

export interface CpuSemState {
  workers: number;
  active: number;
}

export interface EventSemaphores {
  type: "semaphores";
  gpu: GpuSemState;
  cloud: CloudSemState;
  cpu: CpuSemState;
}

export interface EventCost {
  type: "cost";
  usd_so_far: number;
}

export interface EventLog {
  type: "log";
  level: "debug" | "info" | "warning" | "error";
  message: string;
  ts?: string;
}

export interface EventJobDone {
  type: "job_done";
  ok: number;
  warn: number;
  failed: number;
  cancelled?: number;
  seconds: number;
  usd: number | null;
}

export type JobEvent =
  | EventJobStarted
  | EventFileStarted
  | EventPageStarted
  | EventPageDone
  | EventFileDone
  | EventSemaphores
  | EventCost
  | EventLog
  | EventJobDone;

// ---------------------------------------------------------------------------
// GET/PUT /api/jobs/{id}/files/{i}/pages/{n} — the review drawer
// ---------------------------------------------------------------------------

export interface PageChange {
  before: string;
  after: string;
}

export interface PageDetail {
  image_png_base64: string | null;
  image_url: string | null;
  markdown: string;
  changes: PageChange[];
  /** Total pages found in this file, or `null` when the file has no `##
   * Page N` markers at all — the file's *whole* markdown is still returned
   * in `markdown` in that case, not a 404. */
  pages: number | null;
}

export interface PageUpdateRequest {
  markdown: string;
}

/** `detail` of the 409 POST /api/jobs/{id}/retry answers with when the
 * targeted page has a saved edit the retry would overwrite (see N10/N22 and
 * JobRetryRequest.confirm above) — surfaced via ApiError.detail (api/client.ts). */
export interface UnsavedPageEditsDetail {
  code: "unsaved_page_edits";
  message: string;
  file_index: number;
  pages: number[];
}

// ---------------------------------------------------------------------------
// GET /api/jobs/{id}/files/{i}/original — the "ver original" panel
// ---------------------------------------------------------------------------

export interface OriginalInfo {
  kind: JobFileKind;
  previewable: boolean;
  pages: number | null;
  path: string | null;
}

// ---------------------------------------------------------------------------
// POST /api/open, POST /api/reveal
// ---------------------------------------------------------------------------

export interface OpenPathRequest {
  path: string;
}

export interface OpenPathResponse {
  ok: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// GET/POST/DELETE /api/syncs
// ---------------------------------------------------------------------------

export interface Sync {
  id: string;
  input: string;
  output: string;
  preset_id: string;
  enabled: boolean;
  last_run: string | null; // ISO 8601
  files_today: number;
}

export interface SyncCreateRequest {
  input: string;
  output: string;
  preset_id: string;
  enabled?: boolean;
}

/** PATCH /api/syncs/{id} body — currently just `enabled`, so a toggle flips
 * the sync in place instead of deleting and recreating it (which loses its
 * id, last_run and files_today; see docs/desktop-app.md). */
export interface SyncUpdateRequest {
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// POST /api/prices/manual
// ---------------------------------------------------------------------------

export interface ManualPriceRequest {
  model: ModelId;
  input_per_mtok: number;
  output_per_mtok: number;
}

// ---------------------------------------------------------------------------
// POST/DELETE /api/cloud/{provider}/key — every provider in CloudProviderId.
// The env var a provider's key lives under is a backend-only detail — never
// shown in the UI.
// ---------------------------------------------------------------------------

export interface CloudKeyRequest {
  key: string;
}

export interface CloudKeyResponse {
  provider: CloudProviderId;
  key_present: boolean;
  status: OpenAIStatus;
}
