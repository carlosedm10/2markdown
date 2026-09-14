# 2markdown Desktop — the contract

The visual and behavioural spec is [`docs/mockups/desktop-v4.html`](mockups/desktop-v4.html)
(read it before touching this doc or the schemas — it is an interactive
mockup with 6 screens and a Pipeline simulator whose JS encodes the real
scheduling rules). This doc is what everyone else builds against: decisions
already made, the frontend/server/engine split, the API contract, the event
schema, and how a preset maps onto CLI flags and `twomarkdown/config.py`
fields.

Shared source of truth for shapes: [`twomarkdown/server/schemas.py`](../twomarkdown/server/schemas.py)
(pydantic v2) and [`app/src/api/types.ts`](../app/src/api/types.ts)
(TypeScript). Same field names, same enums, in both files — if you add or
rename a field, edit both in the same change.

## Decidido (carried over from the mockup, do not relitigate)

- **Mac first.** The Tauri shell compiles for Windows without much extra
  work; the expensive part is the Python+Tesseract+PyMuPDF sidecar, and "AI
  on this machine" without an NVIDIA GPU should usually just recommend the
  cloud anyway. Windows comes later, without a redesign, once the sidecar
  packaging is clean.
- **Three decisions, not seventeen flags.** What to convert, which preset,
  where to put it. Every flag still exists behind "Ajustes avanzados"; the
  advanced-config screen exposes `twomarkdown/config.py` as-is (DPI,
  language, timeouts, `emit_chunks`) with a Restablecer button — nothing
  there gets its own translated UI.
- **A preset is a saved pipeline, always editable.** Three presets ship —
  Rápido, Apuntes a mano, Archivo grande — and "Editar" opens the Pipeline
  screen with that preset loaded; "Guardar como nuevo" branches it. There is
  no "for RAG" preset: the app converts to Markdown and nothing else.
  `emit_chunks` survives only as an advanced checkbox.
- **Mode is `once` vs a synced watched folder.** The Convertir screen carries
  both: "Una vez" runs the job once; "Mantener sincronizada" turns the same
  input→output→preset triple into an entry under `/api/syncs`, run as a
  background watcher. Each sync owns its own output folder and preset — this
  contract's `JobMode` therefore only defines `"once"`; a sync is not a job
  mode, it is its own resource that creates `once` jobs as files appear.
- **Time and cost, both, before converting, broken down per stage.** Cost is
  always USD, never EUR. `/api/estimate` returns both a total and a per-stage
  breakdown (`extract`/`ocr`/`figures`/`review`/`write`), each stage naming
  its model and whether it ran on `none`/`cpu`/`gpu`/`cloud`.
- **Unknown price is a state, not a zero.** When `genai-prices` has no table
  for a model, `usd` is `null` and `unknown_price` is `true` — the UI shows
  "Precio del modelo desconocido" and offers a manual $/Mtok form
  (`POST /api/prices/manual`), never a false `$0`. The same rule holds for a
  running/finished job's own `usd` (`Job.usd`, and `job_done`'s `usd`): it is
  `0.0` when every cloud call the job made was priced and the total is
  genuinely nothing (including a job that made no cloud calls at all, e.g. an
  all-`tesseract` pipeline), and `null` only when at least one call's price
  could not be determined (`twomarkdown/batch/events.py`'s `usd_unknown()`,
  fed by `add_cost(usd, unknown=...)`). A finished job's `$0` in the UI is
  therefore always "we know it cost nothing", never "we don't know".
- **Image tokens come from pixels, text tokens from characters.** OpenAI
  vision billing: fit to 2048px, short side to 768px, then 512px tiles ×170 +
  85. qwen2.5-vl: 28px patches over the fitted image. Text (the blind
  proofreader) never sees pixels: input ≈ `chars / 4`, output estimated from
  history or a fallback average. Only output tokens are ever estimated;
  input is deterministic from the render or the text.
- **Scheduling rules are the engine's, not reinvented in the UI.** One GPU
  permit shared by local OCR and figure captioning by default
  (`_page_ocr_lock` in `twomarkdown/agents/image_ocr.py`; `llm_config.
  local_gpu_permits`/`Settings.local_gpu_permits`, 1..2, is an experimental,
  unvalidated knob that raises it — see the GPU permits note below), four
  remote permits (`_remote_lock`), OCR and figures sharing one resident
  local vision model only when they must (`effective_figure_model()`
  reuses the OCR model for figures only when the two are local, genuinely
  distinct, and would not both fit resident — see `twomarkdown/batch/
  gpu_memory.py`), and `effective_workers() == 1` whenever any stage uses a
  local vision model. Whether a *second* (or, when figures did not
  collapse, third) local model also fits resident is arithmetic, not a
  fixed "one only" rule — see the `/api/estimate` note below.
  `/api/estimate`'s `bottleneck` and `blocked` fields, and the
  `semaphores` WebSocket event, all report these same rules live — see
  the simulator's `compute()` in the mockup for the reference arithmetic
  (`LOCAL_SECONDS_PER_VLM_PAGE`, `REMOTE_SECONDS_PER_VLM_PAGE`, etc. already
  live in `twomarkdown/batch/planner.py`). `planner._rates()` (used by the CLI
  batch planner) and `batch/estimate.py`'s `_ocr_stage`/`_figures_stage` (used
  by `/api/estimate`, the only thing the desktop UI ever calls) both resolve
  the configured local vision model through the same
  `planner._local_rates()` — a small per-model table
  (`_LOCAL_SECONDS_PER_VLM_PAGE_BY_MODEL` — today just `{"qwen2.5vl:7b":
  40.0}`) before falling back to `LOCAL_SECONDS_PER_VLM_PAGE`'s conservative
  32b-measured default — so `qwen2.5vl:7b` estimates at ~40s/page everywhere,
  Convertir and the Pipeline editor included, instead of only in the CLI
  planner while the UI still priced it as if it were the 32b model (see N8,
  previously a ~3x overshoot in `/api/estimate` against the ~40-48s a single
  page actually took, even after the table itself existed — `estimate.py` had
  its own copy of the flat constant and never imported `_local_rates`).
  **Known gap:** this table is still not literally the same source as the
  wizard's copy or
  `app/src/lib/scheduling.ts`'s `MODELS` (owned by `app/`) — the two agree in
  value today because both were reconciled against the same measurements, but
  nothing stops them drifting apart again the next time either changes. A
  follow-up that has one side read the other (or both read a shared table)
  would close that for good.
- **Cloud is allowed for any stage without an extra warning.** Picking a
  hosted model for OCR, figures, or review already says pages leave the
  machine; the only signal is the "nube" tag on that stage and its cost in
  the estimate.
- **The proofreader is blind and text-only.** `review_model` never sees the
  page image; its diff is read back from `describe_changes()` (a real diff
  of two strings), never taken on the model's word — `EventPageDone.
  review_changes` and `PageDetail.changes` both carry that computed diff.

## Architecture

```
┌─────────────────────────────┐
│ React + Vite frontend       │  app/  (scaffolded by the frontend agent;
│ (Tauri shell: later phase)  │        this doc only owns app/src/api/types.ts)
└──────────────┬──────────────┘
               │ HTTP + WebSocket, 127.0.0.1:8765
┌──────────────▼──────────────┐
│ Local FastAPI server        │  twomarkdown/server/
│ (twomarkdown.server)        │  __init__.py (routes) + schemas.py (contract)
└──────────────┬──────────────┘
               │ in-process import
┌──────────────▼──────────────┐
│ Existing engine             │  twomarkdown/{cli,config,batch,converter,
│ (unchanged)                 │  agents,prompts}.py
└──────────────────────────────┘
```

- **Frontend (`app/`).** React + Vite, TypeScript. Talks to the server only
  through the typed client built on `app/src/api/types.ts`. No direct
  filesystem or subprocess access — that is the server's job, so the frontend
  stays portable to a Tauri webview later without change.
- **Server (`twomarkdown/server/`).** A thin FastAPI process, started
  locally (`uvicorn twomarkdown.server:app` in development; the packaged app
  spawns it as a sidecar later). It owns nothing the engine doesn't already
  own — inspecting files, planning, running batches, watching sync folders —
  it just exposes that over HTTP/WS instead of a CLI + logs. `schemas.py` is
  the pydantic v2 contract; `__init__.py` wires routes to it. Handlers that
  need real engine wiring are marked `# TODO(engine): ...` pointing at the
  module that will replace the stub (`twomarkdown.batch.planner`,
  `twomarkdown.batch.processor`, etc.) — the shapes are final even though
  the bodies are not.
- **Engine (`twomarkdown/{cli,config,batch,converter,agents}.py`).**
  Unchanged. The server imports it; it does not know the server exists.
- **Tauri shell.** Later phase, not now. When it lands, it wraps the same
  `app/` build in a native window and spawns the FastAPI server (or an
  embedded sidecar) instead of a dev server — nothing in this contract
  changes for that move.

## API contract

All endpoints are under `http://127.0.0.1:8765`. Every request/response body
is a model in `twomarkdown/server/schemas.py`, mirrored in
`app/src/api/types.ts`.

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/system` | — | `SystemInfo` |
| GET | `/api/models` | — | `ModelsResponse` |
| POST | `/api/models/resolve` | `ResolveModelRequest` | `ResolveModelResponse` |
| POST | `/api/inspect` | `InspectRequest` | `InspectResponse` |
| POST | `/api/estimate` | `EstimateRequest` | `EstimateResponse` |
| GET | `/api/presets` | — | `list[Preset]` |
| PUT | `/api/presets` | `PresetsUpdateRequest` | `list[Preset]` (FULL replace — see below) |
| PATCH | `/api/presets` | `PresetsUpdateRequest` | `list[Preset]` (merge/upsert — see below) |
| DELETE | `/api/presets/{id}` | — | `list[Preset]` (404 for a builtin or unknown id) |
| GET | `/api/settings` | — | `Settings` |
| PUT | `/api/settings` | `Settings` | `Settings` |
| GET | `/api/folders` | — | `list[FolderPreset]` |
| DELETE | `/api/folders?path=...` | — | `{"ok": true}` |
| POST | `/api/jobs` | `JobCreateRequest` | `JobCreateResponse` |
| GET | `/api/jobs?limit=...` | — | `list[Job]` (newest first; the Historial screen) |
| GET | `/api/jobs/{id}` | — | `Job` (live or from `jobs_history.json`) |
| POST | `/api/jobs/{id}/pause` | — | `Job` |
| POST | `/api/jobs/{id}/resume` | — | `Job` |
| POST | `/api/jobs/{id}/cancel` | — | `Job` |
| POST | `/api/jobs/{id}/files/{i}/cancel` | — | `Job` (409 if that file already finished) |
| POST | `/api/jobs/{id}/retry` | `JobRetryRequest` | `Job` (works on a historical job too) |
| WS | `/api/jobs/{id}/events` | — | stream of `JobEvent` (one JSON object per line) |
| GET | `/api/jobs/{id}/files/{i}/pages/{n}` | — | `PageDetail` |
| PUT | `/api/jobs/{id}/files/{i}/pages/{n}` | `PageUpdateRequest` | `PageDetail` |
| GET | `/api/jobs/{id}/files/{i}/assets/{name}` | — | binary (image) |
| GET | `/api/jobs/{id}/files/{i}/original` | — | `OriginalInfo`, or binary for `kind: "image"` |
| POST | `/api/open` | `OpenPathRequest` | `OpenPathResponse` (403 outside an allowed root) |
| POST | `/api/reveal` | `OpenPathRequest` | `OpenPathResponse` (same guard as `/api/open`) |
| GET | `/api/syncs` | — | `list[Sync]` |
| POST | `/api/syncs` | `SyncCreateRequest` | `Sync` |
| PATCH | `/api/syncs/{id}` | `SyncUpdateRequest` | `Sync` |
| DELETE | `/api/syncs/{id}` | — | `{"ok": true}` |
| POST | `/api/prices/manual` | `ManualPriceRequest` | `ManualPriceRequest` |
| POST | `/api/pick-folder` | — | `PickFolderResponse` |
| POST | `/api/ollama/pull` | `OllamaPullRequest` | `OllamaPullResponse` |
| POST | `/api/cloud/openai/key` | `OpenAIKeyRequest` | `OpenAIKeyResponse` (kept as a plain alias, see below) |
| POST | `/api/cloud/{provider}/key` | `CloudKeyRequest` | `CloudKeyResponse` |
| DELETE | `/api/cloud/{provider}/key` | — | `CloudKeyResponse` (`key_present: false`) |

Model ids follow the engine's `"<provider>:<model>"` convention
(`normalize_model_id` in `twomarkdown/agents/image_ocr.py`): e.g.
`"ollama:qwen2.5vl:7b"`, `"openai:gpt-4o-mini"`. The one bare exception is
`"tesseract"`, naming the CPU OCR engine rather than an LLM.

### Endpoint notes

- **`GET /api/system`**'s `ollama.models[]` (`OllamaModelInfo`) carries a
  `vision: bool` field alongside `name`/`size_gb`/`loaded`: whether that
  *installed* model accepts image input. Sourced from Ollama's own
  `/api/show` (`server/system._probe_vision`, cached per model name for the
  same TTL as the OpenAI quota probe) — the `capabilities` list a current
  Ollama reports there (`"vision" in capabilities`) when present, else
  `details.families` against the known vision-encoder family names (`clip`,
  `mllama`, `qwen2vl`/`qwen25vl`, `gemma3`) on an Ollama too old to report
  `capabilities`. This replaced a client-side name heuristic (a `vl`/
  `vision`/`-v` infix) that mislabeled any multimodal model not spelling it
  in the name — e.g. `gemma3:4b` — as text-only (see N21); the Team screen's
  kit grid (`app/src/screens/Team.tsx`) should read this field instead of
  re-deriving it from the name.
- **`GET /api/models`** replaces a hardcoded model list with the two sources
  that already know the real answer: `local` mirrors `ollama_info()`'s
  installed models (`{id: "ollama:<name>", name, size_gb, loaded, vision,
  installed: true}`, `vision` a real probed `bool`); `cloud` is the
  genai-prices catalog (`genai_prices.data.providers`, read entirely
  offline) restricted to the providers this engine can actually drive
  (`server/models.py:CLOUD_PROVIDER_ENV_VARS` — `openai`, `anthropic`,
  `google`, `groq`, `mistral`, `openrouter`; `{id: "<provider>:<model>",
  provider, vision, price_known, key_present}` — `vision` is always `null`
  here, since genai-prices exposes no modality/capability field as of this
  writing, never a guessed `false`); `providers` is `[{id, key_present,
  env_var}]` for the same six; `stages` is `{ocr, figures, review}`, each a
  list of model ids that make sense there (`ocr` = `"tesseract"` plus every
  vision-capable model, `figures` the same minus `"tesseract"`, `review`
  every model, local or cloud — the proofreader is blind and text-only, see
  above). Cached 60s like every other `/api/system` probe. Every id in
  `CLOUD_PROVIDER_ENV_VARS` is a promise this engine can actually drive
  it, not just that genai-prices/pydantic-ai recognise the name: each one
  needs a pydantic-ai-slim SDK extra installed (`pyproject.toml`'s
  `pydantic-ai[groq,mistral]` — `openai`/`anthropic`/`google` ship via
  pydantic-ai's own default extras, `openrouter` rides the `openai` SDK).
  `tests/test_server.py::TestModels::test_cloud_providers_import_through_pydantic_ai`
  asserts every id in the dict imports through
  `pydantic_ai.providers.infer_provider_class`, so the catalog can never
  again advertise a provider whose extra isn't installed.
- **`POST /api/models/resolve`** validates a free-text model id (the "Otro
  modelo…" flow in a preset's model picker) before it is saved — `{id:
  "provider:model"}` in, `{id (normalized via normalize_model_id, except
  `"tesseract"` which is never folded into `"ollama:tesseract"`), kind:
  "local"|"cloud"|"cpu", installed, key_present, price_known, vision,
  problems: []}` out. Always `200`, even for a provider this app cannot
  drive at all (`problems: ["unknown_provider"]`) — never a 4xx/5xx, so the
  form can show an inline warning instead of a failed request. `installed`/
  `key_present` are `null` when not applicable to that `kind` (a cloud
  model's `installed`, a local model's `key_present`). A `CLOUD_PROVIDER_ENV_VARS`
  provider whose pydantic-ai provider class fails to import (its SDK extra
  missing) reports `problems: ["sdk_not_installed"]` instead of silently
  falling through to `no_api_key`/`unknown_price`, which would misreport the
  actual cause.
- **`GET/PUT /api/settings`** is the wizard's persisted answers
  (`server/settings.py`), and the rule this endpoint exists to enforce is:
  **the first-run wizard is the first edit of Settings, not a separate
  store.** Before this endpoint existed, the wizard only held its answers in
  React state (`app/src/screens/Wizard.tsx`) — closing the app, or even just
  navigating away mid-wizard, silently discarded everything it asked, and
  Convertir had nothing to read back, so it re-derived its own defaults
  instead of the user's. Concretely: the wizard's "Finish" step is a `PUT
  /api/settings` with `wizard_done: true` and whatever the user picked, the
  same call the (later, non-wizard) Settings screen makes when the user
  changes their mind; Convertir must call `GET /api/settings` (or read
  `InspectResponse.default_output`, below) rather than hardcoding
  `"sibling"`/`"apuntes-a-mano"`/`"once"` anywhere in its own code — those
  are `Settings`' field defaults, meant for a fresh install that has not run
  the wizard yet, not a second copy of the user's actual choice.
  `Settings` is `{wizard_done: bool, default_output_mode: "sibling"|"fixed",
  default_output_dir: str|null, default_preset_id: str, default_mode:
  "once"|"sync", local_gpu_permits: int}`. `PUT` is a full replace, validated:
  `default_output_dir` is required and must resolve to an absolute path
  (after `~`-expansion, same rule as a job's own `output`) when
  `default_output_mode` is `"fixed"`; `default_preset_id` must name a preset
  that actually exists; `local_gpu_permits` must be `1` or `2`. A violation is
  `422` with a structured `detail: {code, field, message}` (same shape as
  `POST /api/jobs/{id}/retry`'s `unsaved_page_edits` detail above) — `code`
  is the stable, untranslated field the app switches on (`"required"`,
  `"not_absolute"`, `"not_found"`, `"out_of_range"`), `message` is for logs
  only. `default_mode` is the wizard's "¿una vez o siempre?" answer — not the
  same enum as `JobMode` (always `"once"`, see the `POST /api/jobs` note
  below); it says whether a fresh input defaults to a one-off job or to
  setting up a watched `/api/syncs` entry, a distinction the Convertir/wizard
  UI makes, not something the job endpoint itself branches on.
  `local_gpu_permits` (default `1`) is EXPERIMENTAL and not part of the
  wizard flow: it mirrors `twomarkdown.config.llm_config.local_gpu_permits`,
  the permit count for `agents/image_ocr.py`'s `_page_ocr_lock` (see the
  Scheduling-rules note above and `docs/README.md`'s "GPU permits are an
  experimental, unvalidated knob"). `server/settings.py`'s `get_settings`/
  `replace_settings` push a changed value into `llm_config` and call
  `image_ocr.apply_gpu_permits()` on every read or write of this resource, so
  it takes effect immediately — no server restart, and no separate
  lifecycle hook to remember to wire up.
  `server/settings.py:default_output_for(input_path)` is the single resolver
  both `InspectResponse.default_output` and a `POST /api/jobs` with `output`
  omitted defer to — `"sibling"` mode returns `"<input>_2markdown"` next to
  the input (file or folder alike); `"fixed"` mode returns
  `"<default_output_dir>/<input name>_2markdown"`. Persisted as
  `settings.json`, same per-user app-support directory and flat-JSON-file
  convention as `presets.json`/`folder_presets.json`.
- **`GET/DELETE /api/folders`** exposes `server/folder_presets.py`'s map of
  input path → last preset used, for "Equipo y sincronización" to show and
  forget entries (`DELETE /api/folders?path=...`, `path` a query param, not
  a body). Not the same resource as `/api/syncs`: a sync already binds
  input→output→preset for its own recurring runs; this is only "what did I
  pick last time for a one-off folder", recorded by every `POST /api/jobs`
  (`mode: "once"`) and surfaced back on the matching `POST /api/inspect` as
  `InspectResponse.last_preset_id` (`null` when this path was never
  converted before, or its last job sent a raw `Pipeline` instead of a
  `preset_id`), so the Convertir screen can preselect it. Capped at 200
  entries, oldest dropped first.
- **`POST /api/inspect`** walks a path once (`twomarkdown.batch.walker` +
  the `plan_batch()` PyMuPDF pass) and returns an `inspect_id` the rest of
  the flow reuses, so estimate and job creation don't re-walk the tree.
  `InspectResponse.default_output` is `settings.default_output_for(root)`
  computed server-side (see the `/api/settings` note above) — the app shows
  this value rather than re-deriving the sibling/fixed-dir rule itself, and
  a `POST /api/jobs` that omits `output` lands there too.
- **`POST /api/estimate`** is pure computation over an existing `inspect_id`
  and a candidate `Pipeline` — no side effects, safe to call on every
  Pipeline-screen edit (as the mockup's live simulator does). `inspect_id`
  may be **omitted** (N1): the GPU block/warning/resident/limit fields only
  ever depend on the pipeline's model ids, never on file counts
  (`batch.estimate._gpu_state`), so `estimate_pipeline_only()` computes the
  real engine verdict from the pipeline alone — `stages`' `seconds`/`usd`
  come back `0.0`/`null` (no file data to size them from) and
  `estimate_scope` reads `"pipeline_only"` instead of the default `"full"`.
  This is what the Pipeline editor should call before any folder has been
  inspected, instead of approximating GPU residency from a client-side
  model-size table (`app/src/lib/scheduling.ts`) that can disagree with the
  server's real, installed-model sizes — see docs/README.md's "Resident
  memory is arithmetic, not a count". `blocked`
  fires whenever at least two of the pipeline's *distinct* local models
  (page OCR, figures, and a local `review_model`) do not fit resident
  together — no longer only a `review_model` case: `_effective_figure_model()`/
  `effective_figure_model()` (`twomarkdown/batch/gpu_memory.py`, shared by
  both) only collapse OCR+figures onto the OCR model when that pair itself
  would not fit, so a figure model that *does* fit stays a second distinct
  local model in this sum, right alongside a local `review_model`. Whether
  the whole set is *actually* blocked is arithmetic, not a headcount
  (`batch/estimate.py`'s `_gpu_state`/`resident_gb`, `RESIDENT_FACTOR =
  1.45`, see `docs/README.md`'s "Resident memory is arithmetic, not a
  count"): each distinct local model's installed (or, if not pulled yet,
  parameter-count-estimated) file size scales into resident GB, summed and
  compared against `gpu_limit_gb`. `EstimateResponse` carries the whole
  computation — `gpu_resident_gb` (the sum), `gpu_limit_gb` (this machine's
  own limit), `gpu_headroom_gb` (the difference) — so the app never re-derives
  it. A pipeline that fits but leaves under 3 GB of headroom gets `warning`
  instead of `blocked` (fits, but close — e.g. 32b+3b at ~34 of 36 GB): still
  runnable, worth flagging before something else touches the GPU mid-run.
  When `blocked`, `total_seconds`/`total_usd`/`bottleneck` should be treated
  as not meaningful by callers, same as the simulator's `R.blocked ? "–" :
  ...`; `warning` does not change how those fields should be read. On the
  app side, `EstimateResponse` in `app/src/api/types.ts` carries all three GB
  fields and `warning` as required (the server always sends them; all `0.0`
  with no local model, `gpu_limit_gb` also `0.0` off Apple Silicon). The
  Pipeline editor's GPU bar and Convertir's estimate card render them
  verbatim — red note + disabled "Convertir" for `blocked`, an amber note
  that disables nothing for `warning`. The only client-side copy of the
  arithmetic (`app/src/lib/scheduling.ts`, same `RESIDENT_FACTOR`) serves
  `VITE_MOCK=1` and the Wizard's "Personalizado" card, which needs a verdict
  on every picker change before there is an `inspect_id` to estimate against
  and passes `/api/system`'s `gpu_limit_gb` in when it has it.
- **`POST /api/jobs`** takes either `inspect_id` (preferred — reuses the
  walk) or a raw `path` (server inspects it first), plus either an inline
  `pipeline` or a `preset_id` to resolve one. `mode` is always `"once"` in
  this contract; watched-folder conversion lives under `/api/syncs` instead
  of as a job mode, because a sync's output folder and preset are part of
  the sync's own identity, not a single job's. `output` may be omitted —
  the server then resolves it with `settings.default_output_for()` from the
  job's own input path, the same value `POST /api/inspect` already showed as
  `default_output`. When `output` is given, or once resolved, it is expanded
  (`~`/`~user`) and resolved to an absolute path by the server before
  `mkdir` — a relative path (no leading `/` or `~`) is rejected with 422
  rather than being created under the server's own working directory. The
  same rule applies to a sync's `output` and to `Settings.default_output_dir`.
  Callers (the wizard included) should still prefer sending an
  already-expanded absolute path; the server expanding `~` is a safety net,
  not licence to keep passing the literal string around client-side.
- **`Job.files[]` per-file facts** — the queue-as-file-list UI (the
  desktop restructure this contract now serves: the file list *is* the queue
  and the per-row results — cancel, open, review, re-convert with another
  model) acts on a row without a second round-trip, so each `JobFileState`
  carries, beyond `status`/`pages`/`seconds`/`reason`: `output_path` (the
  absolute `.md` path, **once one exists** — `None` for a file that has not
  produced output yet, including one skipped by a cancel before it ever
  started; never the path it *would* land at), `kind` (`"pdf"|"image"|
  "office"|"html"|"text"|"ebook"|"mindmap"|"other"`, set at job creation from
  the source suffix alone — cheap, no file open), `assets_dir` (that file's
  own `<stem>_assets/`, if any exist), `engine_used` (`"tesseract"`,
  `"ollama:<model>"`, `"<provider>:<model>"`, or `"none"` when no OCR-capable
  stage ever touched this file), and `review_changes_count`. `server/jobs.py`'s
  `_fill_file_facts()` refreshes all of these once a file finishes (first run
  or retry) from the same real state a first run and a retry both already
  produce — nothing here is re-derived client-side.
- **`GET /api/jobs?limit=...`** is the Historial screen's only endpoint:
  every job, in-memory (queued/running/just finished) or from
  `jobs_history.json`, newest first. A *finished* job (`_run_job`'s own
  `finally`, or `retry()` once it has updated one) is persisted to that file
  — same per-user app-support folder and flat-JSON-file convention as
  `presets.json`/`settings.json`/`folder_presets.json` — so `GET /api/jobs`
  and `GET /api/jobs/{id}` answer identically whether the server has
  restarted since or not. `POST /api/jobs/{id}/retry` also works on a job no
  longer in memory: `server/jobs.py`'s `_get_runtime()` rehydrates it from
  history first (approximating the original `input_root` from `Job.input`,
  since the exact walked root is not itself persisted), same as
  `GET .../pages/{n}` and `GET .../original` below.
- **`POST /api/jobs/{id}/cancel` stops the job after the *current page*, not
  the current file** — the job-wide cancel and the file in flight's own
  per-file cancel token are both set at once (`process_batch(cancel=...)`
  forwards that token into `converter/pdf_ocr.py`'s per-page OCR loop), so
  the file stops after its current page and keeps whatever it already
  transcribed, flagged with the same "conversión incompleta" banner a
  per-file timeout produces (never silently discarded) — every file still
  queued is marked `"cancelled"` outright, reason `"cancelado antes de
  empezar"`, no attempt made. **`POST /api/jobs/{id}/files/{i}/cancel`** is
  the same mechanism scoped to one file: queued → skipped the same way;
  running → stopped after its current page, same reason rules, but the rest
  of the job keeps going (the job-wide cancel is never set); already
  finished (`ok`/`warn`/`failed`/`cancelled`) → `409`, nothing left to stop.
  Either path emits a `file_done{status: "cancelled"}` event for every file
  it touches, including one skipped while still queued (which never reaches
  the engine at all — `server/jobs.py` publishes that event itself, since
  nothing else would), and `job_done.cancelled` counts every file cancelled
  either way.
- **`GET /api/jobs/{id}/files/{i}/original`** is "ver original": `kind ==
  "pdf"` answers `{kind: "pdf", previewable: true, pages}` (a fresh page
  count, not whatever `JobFileState.pages` currently holds — this must work
  even before the file has been converted at all) so the app pages through
  it with the existing `GET .../pages/{n}` page-image endpoint; `kind ==
  "image"` answers with the raw image bytes directly (`FileResponse`, not
  `OriginalInfo`); everything else answers `{kind, previewable: false, path}`
  so the app can offer "abrir con la app del sistema" (`POST /api/open`)
  instead of a preview it has no renderer for.
- **`POST /api/open`/`POST /api/reveal`** run the macOS `open`/`open -R`
  commands on a path — never an arbitrary one: `server/jobs.py:
  is_path_allowed()` requires it to sit under the user's own home directory
  or a root some known job (live or historical) actually converted from/to,
  and a path outside every one of those is refused with `403` before any
  subprocess runs, not merely reported as `{ok: false}`. Both endpoints are
  macOS-only; every other platform reports that plainly in `error` rather
  than guessing at `xdg-open`/`explorer`.
- **`POST`/`DELETE /api/cloud/{provider}/key`** — every provider in
  `server/models.py:CLOUD_PROVIDER_ENV_VARS` (`openai`, `anthropic`,
  `google`, `groq`, `mistral`, `openrouter`), not just OpenAI. The UI only
  ever sends/receives `provider`, `key_present`, `status` — never the env
  var name a key is stored under (`server/host.py` resolves that internally
  and writes/replaces only that one line in `.env`, same as
  `save_openai_key` always did; the raw key is never logged or echoed back).
  `POST` also runs one best-effort, provider-specific validation call
  (`server/host.py:probe_cloud_key`, ≤5s, off the request thread) —
  `"ok"`/`"invalid_key"`/`"out_of_credit"`/`"unknown"` (`unknown` for a
  provider with no such call yet, a network failure, or a timeout — never a
  guess), and *caches* that verdict (`system.cache_probe_verdict`, C6) keyed
  by provider id, both in-process and persisted to a small JSON file under
  the same per-user app-support directory as `presets.json`
  (`system._PROBE_CACHE_FILE`) so it also survives a server restart, not
  just a page reload. `provider_cloud_info()` (backing `GET /api/system`'s
  `cloud` block) serves that cached verdict — instead of unconditionally
  recomputing a guessed `"unknown"` from key-presence alone — while it is
  under an hour old, and re-probes lazily once it ages out or a key changes
  (`DELETE` clears the cached verdict for that provider outright, so a
  removed key never keeps reporting its last status). Both routes also
  invalidate `/api/system`'s and `/api/models`'s other cached probes so the
  change is reflected on the very next call, not up to 60s later. `POST
  /api/cloud/openai/key` (the original, OpenAI-only endpoint) keeps working
  unchanged as a plain alias for `provider="openai"` — it is declared first
  in `server/app.py` so its exact path wins over the `{provider}` pattern.
- **`PATCH /api/syncs/{id}`** flips fields on an existing sync in place —
  today just `{enabled}` — preserving its `id`, `last_run` and
  `files_today`. Toggling a sync's enabled state used to mean
  delete-then-recreate (losing that identity on every flip, with no undo if
  the recreate failed); this route is the fix. The server must implement it
  (`twomarkdown/server` + `schemas.py`); `app/src/api/client.ts` already
  calls it.
- **`GET/PUT /api/jobs/{id}/files/{i}/pages/{n}`** backs the Revisar screen:
  GET returns the rendered page image alongside its markdown and the
  reviewer's diff; PUT writes an edited markdown back. `image_png_base64` is
  for small/ephemeral pages, `image_url` for anything the server would
  rather serve as a static file — exactly one should be set. `PageDetail.pages`
  is the file's total page count (`## Page N` markers found), or `None` when
  it has none at all — a non-paginated file (anything that isn't a scanned
  PDF) still returns its *whole* markdown in `markdown`, never a 404: before
  this, a page count that could not tell "no pagination" apart from "0/1
  total" read as "nothing to show" to the frontend, so Revisar only ever
  worked for scanned PDFs.
- **`POST /api/jobs/{id}/retry` refuses to silently drop a saved page
  edit.** A page-level (or whole-file) retry re-runs OCR/review and
  regenerates that page's Markdown from scratch — anything a user typed and
  saved with `PUT .../pages/{n}` since that page's last conversion would
  otherwise vanish with no warning and no undo (see N10). The server tracks,
  per job, which `(file_index, page)` pairs have such an unsaved-since-retry
  edit (`server/jobs.py`'s `_Runtime.edited_pages`) and responds `409` with
  `server.jobs.UnsavedPageEditError`'s message (naming the file and pages at
  risk) instead of retrying, unless `JobRetryRequest.confirm` is `true`. A
  whole-file retry (`page: null`) checks every page of that file, since it
  regenerates all of them. Revisar's "Volver a leer con la nube" (`app/src/
  screens/Review.tsx`, owned by `app/`) treats that 409 as "warn, then let
  the user decide" — "Se volverá a leer la página con la nube; se perderán
  los cambios que hayas escrito y guardado en ella. ¿Continuar?" — and only
  resends with `confirm: true` on an explicit yes; a 409 that still gets
  back through (e.g. the confirmed retry loses a race with another edit) is
  shown as a translated Spanish message, never the raw exception.
  `_retry_single_page` only ever regenerates the page's `### OCR` section
  body — everything else in the page's current slice is spliced back
  unchanged (`server/jobs.py`'s `_existing_page_context()`): whatever came
  before the `### OCR` heading (the `## Page N` heading itself, a native-text
  chunk, or a line a user typed there), and whatever comes after the next
  structural heading following it (a figure block, a table block, or another
  section a saved edit added). Only text a user typed *inside* the OCR body
  itself, with no heading of its own to anchor it, is genuinely lost — that
  region is what a fresh OCR pass regenerates by design. One gap remains:
  the warning still fires for every retry, including a page nobody ever
  edited (`edited_pages` is set whenever a page is saved, not compared
  against whether the save actually changed anything, and the server has no
  way to know a saved edit landed only in a region that would survive
  anyway) — a page whose draft still matches its last conversion could in
  principle retry without asking, but doing that safely needs the server to
  track content, not just the fact of a save.
- **`GET /api/jobs/{id}/files/{i}/assets/{name}`** — serves a figure
  extracted alongside a converted file back to the desktop app, which runs
  from its own origin with no static route onto a job's output tree. A
  page's Markdown can reference an extracted figure with a path relative to
  that file's own output folder (`twomarkdown/converter/figures.py` writes
  `<name>_assets/<name>-fig-p<n>-<k>.png` next to the `.md`, e.g.
  `"Tema 1_assets/Tema 1-fig-p1-1.png"`); `name` is that same path,
  percent-encoded segment by segment (the segments contain spaces — the
  folder name is literally the source file's own name), matching what
  `app/src/screens/Review.tsx`'s `assetSrc()` builds. `server/jobs.py`'s
  `resolve_asset_path()` resolves `name` against the file's output folder
  (not the assets folder directly, since `name` already starts with the
  `<stem>_assets/` segment) and then requires the result to still be inside
  `<stem>_assets/` — a `name` carrying `../` segments 404s instead of
  reading another file the server process can see. A missing job, file
  index, or asset all 404 the same way; `<FigureImage>` in Review.tsx
  already treats any non-200 as "figure unavailable" rather than leaking
  raw Markdown.

## Event schema (`WS /api/jobs/{id}/events`)

One JSON object per line, discriminated by `type`:

- `job_started` — `{job_id}`
- `file_started` — `{index, path, pages}`
- `page_started` — `{index, page, engine}` — `engine` is the job's own
  `pipeline.ocr_model` ("tesseract", `"ollama:<model>"`, or
  `"<provider>:<model>"`), stamped on by `server/jobs.py`'s sink (N10) so the
  UI can say "Leyendo con Tesseract…" for a Tesseract-only run instead of
  always "Leyendo con IA…" (a copy bug the UX tester saw — the app itself
  owns that string, `app/src/screens/Convert.tsx`'s `FileRow`, which is
  outside this repo's `twomarkdown/server/`+`tests/`+`Makefile`+`docs/`
  ownership for this change; this field is what it needs to fix it).
- `page_done` — `{index, page, engine, seconds, fallback, review_changes:
  [{line, before, after}]}` — `fallback: true` means the page dropped to
  Tesseract because the vision model declined (`ocr_fallback_pages` today).
- `file_done` — `{index, status: "ok"|"warn"|"failed"|"cancelled", seconds,
  reason?}` — `"cancelled"` for a file stopped by `POST /api/jobs/{id}/cancel`
  or `POST /api/jobs/{id}/files/{i}/cancel`, whether it was still queued
  (skipped outright, `reason: "cancelado antes de empezar"`) or stopped
  mid-file after its current page (partial output kept, same "conversión
  incompleta" banner a timeout produces).
- `semaphores` — `{gpu: {permits, held_by: [{file, page, stage}], waiting},
  cloud: {permits, in_use}, cpu: {workers, active}}` — the live view of the
  same three lanes (GPU / cloud / no-model) the Pipeline screen shows
  statically; pushed on every state change, not polled.
- `cost` — `{usd_so_far}`
- `log` — `{level, message, ts?}` — the existing verbose log, folded/collapsed
  by default in the UI (the mockup's "Detalles técnicos"). `ts` (ISO 8601) is
  when the server produced the line, not when the client received it — a
  WebSocket reconnect replays the whole backlog at once, and without a
  server-assigned `ts` every replayed line gets re-stamped with the reconnect
  time. The server does not emit `ts` yet; until it does, the client falls
  back to receipt time (see `app/src/screens/Queue.tsx`).
- `job_done` — `{ok, warn, failed, cancelled, seconds, usd}`

Order within a job: one `job_started`; then, per file, one `file_started`,
interleaved `page_started`/`page_done` pairs (only for pages that needed
OCR), one `file_done`; `semaphores`/`cost`/`log` can arrive at any point
between those; exactly one `job_done` at the end.

## Presets → engine config

A preset is `{id, name, builtin, pipeline}`; `pipeline` is a `Pipeline`
object. Field-by-field mapping onto `twomarkdown.cli.convert`'s flags and
`twomarkdown/config.py`:

**Three write endpoints, three distinct contracts (C7 — see
`server/presets.py`'s module docstring and each function's own):**

- **`PUT /api/presets`** is a FULL replace: the request is the entire
  desired state. An entry whose `id` matches one of the three builtins
  (`presets.BUILTIN_PRESETS`) is stored as an *override* (`builtin: true`
  stays true, only its `pipeline` is remembered — dropped back to the
  hardcoded default, i.e. "Restablecer", the moment the sent pipeline
  matches it again, normalized per `_normalized_pipeline`); any other `id`
  is a user preset, `builtin: false`, stored and returned verbatim. A
  builtin or user preset that existed before this call and is **not** in
  the request is gone afterwards. `presets.replace_all` logs a warning
  naming every user preset a PUT is about to drop this way, so a caller
  bug of exactly this shape (C7's original report: every app-side save
  sent only the changed preset to what was then the only write endpoint,
  silently deleting every other saved preset) stays visible in the
  technical log even though the drop itself is correct per this contract.
- **`PATCH /api/presets`** is merge/upsert by id (`presets.merge_upsert`):
  every preset in the request is created/updated in place; anything already
  stored and not mentioned survives untouched. This is what saving or
  editing a single preset should call — it is the endpoint that makes C7's
  regression structurally impossible rather than merely logged.
- **`DELETE /api/presets/{id}`** removes one user preset outright — the one
  thing `PATCH` cannot express. 404s for a builtin id (nothing to delete;
  reset it by sending its default pipeline back through `PUT`/`PATCH`
  instead) or an id that was never a stored user preset.

There is no separate "create preset" endpoint: the wizard (or the Presets
screen) creating a preset for the first time is just a `PATCH` (or `PUT`,
if it's also willing to resend everything) whose list includes the new
entry, same as editing one is a `PATCH` whose list has only that entry with
a changed `pipeline`.
A write that changes a builtin's `pipeline` is a deliberate, explicit edit —
the same "Restablecer" mechanism above makes it reversible, so this is fine
as-is and needs no extra guard. What must *not* happen is the wizard silently
overwriting a builtin as a side effect of some other flow (e.g. writing
wizard answers into `presets.json` under a builtin's id instead of into
`settings.json` — see `GET/PUT /api/settings` above, which exists so the
wizard has its own resource and never needs to touch `/api/presets` at all
unless the user is actually editing a preset). That would be an app-level
bug in whatever call site did it, not something these endpoints themselves
can distinguish from a real edit — the request shape is identical either
way, and `presets.py` correctly cannot tell "the user meant this" from
"some other code path meant this by accident".

| `Pipeline` field | CLI flag | Config field | Notes |
|---|---|---|---|
| `ocr_model` | `--ocr-backend` (+ `--ollama` shortcut) plus `llm_config.vision_model` | `conversion_config.ocr_backend`, `llm_config.vision_model` | `"tesseract"` → `ocr_backend="tesseract"`; any `"ollama:…"`/`"openai:…"` id → `ocr_backend="ollama"`, `llm_enabled=True`, `vision_model=<id>` (`resolve_ocr_backend()` already encodes "ollama implies LLM"). |
| `figure_model` | `--figure-model` (+ `--describe-figures`) | `llm_config.figure_model`, `conversion_config.describe_figures`, `figure_config.describe_figures_llm` | `null` → `describe_figures=False` (the "No describir" option); a model id sets both the model and turns captioning on. |
| `review_model` | `--review-model` | `llm_config.review_model` | `null`/empty → review pass off (`review_enabled()` is false). |
| `workers` | `--workers` | `conversion_config.parallel_workers` | The *requested* value; `effective_workers()` still collapses it to 1 whenever `ocr_model` or `figure_model` is a local vision model — the estimate and the live semaphores reflect the effective number, not the requested one. |
| `describe_figures` | `--describe-figures/--no-describe-figures` | `conversion_config.describe_figures`, `figure_config.describe_figures_llm` | Kept as its own field (not derived only from `figure_model`) so "figures detected but not captioned" stays expressible. |
| `clean` | `--clean/--no-clean` | `conversion_config.clean_markdown` | |
| `tables` | `--tables/--no-tables` | `conversion_config.extract_tables` | |
| `emit_chunks` | `--emit-chunks/--no-emit-chunks` | `conversion_config.emit_chunks` | Advanced-only checkbox; never surfaced as a preset dimension. |
| `ocr_dpi` | — (not a CLI flag today) | `pdf_ocr_config.pdf_ocr_dpi` | New knob for the desktop app's Pipeline screen; the server sets `pdf_ocr_config.pdf_ocr_dpi` directly rather than through a flag. |

Fields deliberately **not** in `Pipeline` because they stay engine-internal
or advanced-only: `skip_existing`/`force`, `fetch_remote_images`, `report`,
`pdf_ocr` on/off, and everything in `PdfOcrConfig`/`FigureConfig` beyond DPI
(the fragment-detection and scramble-ratio tunables). Those remain reachable
only from "Ajustes avanzados", editing `twomarkdown/config.py`'s defaults
in place, per the "la máquina no traduce cada campo" decision.

The three built-in presets, as shipped in
`twomarkdown/server/presets.py:BUILTIN_PRESETS`:

- **Rápido** — `ocr_model="tesseract"`, no figures, no review, `workers=4`,
  `emit_chunks=False`, `ocr_dpi=300`.
- **Apuntes a mano** — `ocr_model="ollama:qwen2.5vl:7b"`,
  `figure_model="ollama:qwen2.5vl:7b"` (same model, so
  `effective_figure_model()` reuses the already-loaded one),
  `review_model="openai:gpt-4o-mini"`, `workers=1`.
- **Archivo grande** — same "sin IA" text-only pipeline as Rápido
  (`ocr_model="tesseract"`, no figures, no review) but tuned for throughput
  over a big batch rather than accuracy or model choice: `workers=8` (more
  files in flight), `ocr_dpi=200` (cheaper render per page, still legible for
  printed text), `emit_chunks=True` (pre-split output, useful once a run is
  large enough that a downstream pipeline wants it chunked already). Until
  this was given its own pipeline it was byte-identical to Rápido — same
  card blurb and all (see N18); the fields that now differ are exactly the
  ones a large-batch preset should trade differently from a quick one.

## Where the details live

- Contract code — `twomarkdown/server/schemas.py`, `twomarkdown/server/__init__.py`.
- Frontend mirror — `app/src/api/types.ts`.
- Engine this wraps — `twomarkdown/cli.py`, `twomarkdown/config.py`,
  `twomarkdown/batch/{planner,processor,manifest,walker}.py`,
  `twomarkdown/agents/{image_ocr,page_review}.py`.
- Visual/behavioural spec — `docs/mockups/desktop-v4.html`.
- Product taxonomy and engine decisions — `docs/README.md`.
