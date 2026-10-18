/**
 * Mock-mode scheduling arithmetic — a straight TypeScript port of the
 * simulator's compute() in docs/mockups/desktop-v4.html. This is a stand-in
 * for the real /api/estimate, which the server computes from
 * twomarkdown/batch/planner.py (LOCAL_SECONDS_PER_VLM_PAGE, _page_ocr_lock,
 * _remote_lock, effective_workers/effective_figure_model) and
 * twomarkdown/batch/estimate.py (`_gpu_state`, the resident-memory
 * arithmetic). Kept here, not inlined into the Pipeline screen, so
 * VITE_MOCK=1 can serve /api/estimate without a server while staying visibly
 * a stub.
 *
 * WHO READS WHAT — the split is deliberate and should stay this way:
 *
 * - Anywhere an `/api/estimate` round trip has already happened (Pipeline
 *   editor, Convertir), the screen renders the server's own
 *   `gpu_resident_gb` / `gpu_limit_gb` / `gpu_headroom_gb` / `blocked` /
 *   `warning` verbatim. Nothing in this file is consulted for those numbers:
 *   the server has Ollama's real installed sizes (`/api/tags`) and the host's
 *   real RAM; this file has neither.
 * - Only two callers run this arithmetic client-side: `mockClient.ts`
 *   (VITE_MOCK=1, no server at all) and the Wizard's "Personalizado" card,
 *   which needs an instant blocked/warning verdict on every picker change
 *   before there is any inspect_id to estimate against. Both must apply the
 *   same `RESIDENT_FACTOR` the server does — summing raw `MODELS[...].gb`
 *   file sizes is what used to show "25.6 GB residentes" for a 32b+7b pair
 *   that actually sits at ~37 GB — and should pass the host's real
 *   `system.gpu_limit_gb` when they have it, falling back to `GPU_MAX_GB`.
 */
import type {
  BlockedInfo,
  Bottleneck,
  EstimateAlternative,
  EstimateResponse,
  EstimateStage,
  Pipeline,
  StageKind,
} from "../api/types";

export interface ModelSpec {
  id: string;
  name: string;
  kind: "cpu" | "gpu" | "cloud" | "none";
  gb: number;
  secPerPage?: Record<"ocr" | "fig" | "rev", number | undefined>;
  usdPerUnit?: Record<"ocr" | "fig" | "rev", number | undefined>;
  unknownPrice?: boolean;
}

export const MODELS: Record<string, ModelSpec> = {
  tesseract: { id: "tesseract", name: "Tesseract", kind: "cpu", gb: 0, secPerPage: { ocr: 2, fig: undefined, rev: undefined } },
  "ollama:qwen2.5vl:7b": {
    id: "ollama:qwen2.5vl:7b",
    name: "qwen2.5vl:7b",
    kind: "gpu",
    gb: 5.6,
    secPerPage: { ocr: 40, fig: 20, rev: 15 },
  },
  "ollama:qwen2.5vl:32b": {
    id: "ollama:qwen2.5vl:32b",
    name: "qwen2.5vl:32b",
    kind: "gpu",
    gb: 20,
    secPerPage: { ocr: 150, fig: 40, rev: 45 },
  },
  "openai:gpt-4o-mini": {
    id: "openai:gpt-4o-mini",
    name: "gpt-4o-mini",
    kind: "cloud",
    gb: 0,
    secPerPage: { ocr: undefined, fig: 5, rev: 3 },
    usdPerUnit: { ocr: undefined, fig: 0.001, rev: 0.001 },
  },
  "openai:gpt-4o": {
    id: "openai:gpt-4o",
    name: "gpt-4o",
    kind: "cloud",
    gb: 0,
    secPerPage: { ocr: 8, fig: 5, rev: 3 },
    usdPerUnit: { ocr: 0.01, fig: 0.005, rev: 0.01 },
  },
  "openai:unreleased": {
    id: "openai:unreleased",
    name: "modelo recién salido",
    kind: "cloud",
    gb: 0,
    secPerPage: { ocr: 8, fig: 5, rev: 3 },
    unknownPrice: true,
  },
};

export const NONE_MODEL: ModelSpec = { id: "", name: "—", kind: "none", gb: 0 };

export function modelFor(id: string | null): ModelSpec {
  if (!id) return NONE_MODEL;
  return MODELS[id] ?? { id, name: id, kind: "cloud", gb: 0, unknownPrice: true };
}

export interface LoadShape {
  files: number;
  pages: number;
  scanned: number;
  figs: number;
  label: string;
}

export const LOADS: Record<string, LoadShape> = {
  apuntes: { files: 8, pages: 25, scanned: 22, figs: 6, label: "25 páginas · 22 escaneadas · ~6 figuras · 41 MB" },
  archivo: { files: 3000, pages: 12000, scanned: 9000, figs: 1200, label: "12 000 páginas · 9 000 escaneadas · ~1 200 figuras" },
};

/** Fallback GPU ceiling for a client that has not yet learned the host's
 * real `system.gpu_limit_gb` (a 48 GB Apple Silicon Mac, ~75% of RAM). Every
 * caller that has `SystemInfo` in hand should pass its `gpu_limit_gb`
 * instead. */
export const GPU_MAX_GB = 36;
export const REMOTE_PERMITS = 4;
export const CPU_SECONDS_PER_PAGE = 0.6;

/** Mirrors `twomarkdown/batch/estimate.py`'s `RESIDENT_FACTOR`: Ollama keeps
 * substantially more than a model's file size resident once it is actually
 * loaded (KV cache, context buffers, the runtime's own working memory) — a
 * model's `MODELS[...].gb` above is its *file* size, so every resident-memory
 * computation must scale it by this factor before summing or comparing
 * against `GPU_MAX_GB`. Do not sum raw `.gb` values directly — that under-
 * counts residency and both misses real blocks and, if ever used to render a
 * "GB residentes" figure, misleads (see the task that added this constant:
 * the client was showing "25.6 GB residentes" for two models that actually
 * resident at ~37 GB). */
export const RESIDENT_FACTOR = 1.45;

/** Below this much spare headroom (post-`RESIDENT_FACTOR`), whatever runs
 * next on the GPU risks tipping a pipeline that technically "fits" into an
 * OOM — surfaced as `warning`, distinct from `blocked` (which only fires once
 * two distinct local models' combined resident size actually exceeds the
 * limit). Mirrors `estimate.py`'s `_WARNING_HEADROOM_GB`. */
const WARNING_HEADROOM_GB = 3.0;

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Mirrors `estimate.py`'s `_effective_figure_model`: OCR and figures never
 * contribute two separate resident-memory entries — when both are local, the
 * OCR model wins GPU residency (the engine reuses it for figures too)
 * regardless of what the figures picker still shows. */
function effectiveFigureModel(ocrModel: string | null, figureModel: string | null): string | null {
  if (!figureModel) return null;
  if (modelFor(figureModel).kind === "gpu" && modelFor(ocrModel).kind === "gpu") return ocrModel;
  return figureModel;
}

/** Distinct local (GPU) models `{ocrModel, figureModel, reviewModel}` would
 * keep resident at once, after the OCR/figures collapse above — at most two
 * entries ever come out of this (the page model, and a local review model
 * that disagrees with it), deduplicated by model name. */
function distinctLocalModels(ocrModel: string | null, figureModel: string | null, reviewModel: string | null): ModelSpec[] {
  const effFig = effectiveFigureModel(ocrModel, figureModel);
  const seen = new Map<string, ModelSpec>();
  for (const id of [ocrModel, effFig, reviewModel]) {
    if (!id) continue;
    const m = modelFor(id);
    if (m.kind === "gpu" && !seen.has(m.name)) seen.set(m.name, m);
  }
  return [...seen.values()];
}

export interface GpuState {
  blocked: BlockedInfo | null;
  warning: BlockedInfo | null;
  gpu_resident_gb: number;
  gpu_limit_gb: number;
  gpu_headroom_gb: number;
}

/** Count-aware title/body (flow-issues.md C14): "Dos modelos…"/"Con ambos…"
 * only reads right for exactly two distinct local models — a pipeline with
 * three (OCR + figures + review all landing on different local models) must
 * say "Los modelos…"/"Con todos…" instead. Mirrors
 * twomarkdown/batch/estimate.py's count-aware wording; keep both in sync. */
function blockedTitle(count: number): string {
  return count > 2 ? "Los modelos locales no caben en la GPU a la vez" : "Dos modelos locales no caben en la GPU a la vez";
}

function blockedBody(residents: { name: string; gb: number }[], total: number, limit: number): string {
  const parts = residents.map((r) => `${r.name} ≈ ${Math.round(r.gb)} GB`).join(" + ");
  // Mirrors twomarkdown/batch/estimate.py's `_blocked_body` loaded_clause:
  // "el segundo" is only a concrete, correct claim with exactly two models.
  const loadedClause =
    residents.length === 2 ? "Con ambos cargados el segundo se queda sin memoria" : "Con todos cargados, alguno se queda sin memoria";
  return (
    `${parts} = ${Math.round(total)} GB residentes > ${Math.round(limit)} GB que macOS deja a la GPU. ` +
    `${loadedClause} al codificar la imagen y las llamadas siguientes ` +
    "fallan. Usa el mismo modelo en ambas etapas, uno más pequeño, o pasa una etapa a la nube."
  );
}

/** Resident-memory arithmetic for one pipeline's local models — the mock-mode
 * stand-in for the engine's `estimate._gpu_state`, using the same
 * `RESIDENT_FACTOR`/`WARNING_HEADROOM_GB` and the same two-tier
 * blocked/warning split. `limit` defaults to `GPU_MAX_GB` (this client has no
 * notion of the host's real RAM); pass the server's own `gpu_limit_gb` when
 * you have one so the arithmetic and the "GB de N" the UI shows agree. */
export function gpuState(
  ocrModel: string | null,
  figureModel: string | null,
  reviewModel: string | null,
  limit: number = GPU_MAX_GB,
  uncertaintyMarginGb = 0
): GpuState {
  const models = distinctLocalModels(ocrModel, figureModel, reviewModel);
  if (models.length === 0) {
    return { blocked: null, warning: null, gpu_resident_gb: 0, gpu_limit_gb: limit, gpu_headroom_gb: round2(limit) };
  }
  const residents = models.map((m) => ({ name: m.name, gb: round2(m.gb * RESIDENT_FACTOR) }));
  const total = round2(residents.reduce((a, r) => a + r.gb, 0));
  const headroom = round2(limit - total);
  // `MODELS[...].gb` is a hardcoded guess at each model's installed file
  // size, not the host's actual Ollama pull — the server instead reads real
  // installed sizes from `/api/tags`, and those can run meaningfully larger
  // (a measured case: this table said 37.1 GB resident for a 7b+32b pair the
  // engine's real numbers put at 39.3 GB, flipping an actual block into an
  // apparent "cabe justo" here — see flow-issues.md N1). `uncertaintyMarginGb`
  // lets a caller that has no `/api/estimate` round trip yet (so is stuck on
  // this approximation) pad the blocked/warning comparison so it errs toward
  // over-warning rather than under-warning; it never touches the displayed
  // `gpu_resident_gb`/`gpu_headroom_gb`, which stay this function's honest
  // best guess.
  const totalForVerdict = round2(total + uncertaintyMarginGb);
  const headroomForVerdict = round2(headroom - uncertaintyMarginGb);

  if (models.length > 1 && limit > 0 && totalForVerdict > limit) {
    return {
      blocked: { title: blockedTitle(residents.length), body: blockedBody(residents, total, limit) },
      warning: null,
      gpu_resident_gb: total,
      gpu_limit_gb: limit,
      gpu_headroom_gb: headroom,
    };
  }
  if (limit > 0 && headroomForVerdict < WARNING_HEADROOM_GB) {
    return {
      blocked: null,
      warning: {
        title: "Memoria GPU ajustada",
        body: `Cabe justo: ${Math.round(total)} de ${Math.round(limit)} GB. Cierra otras apps que usen la GPU.`,
      },
      gpu_resident_gb: total,
      gpu_limit_gb: limit,
      gpu_headroom_gb: headroom,
    };
  }
  return { blocked: null, warning: null, gpu_resident_gb: total, gpu_limit_gb: limit, gpu_headroom_gb: headroom };
}

/** Convenience wrapper over `gpuState` for callers (like the Wizard's
 * "Personalizado" onboarding card) that only need the blocked/warning verdict
 * + message ahead of a full `/api/estimate` round trip, not the whole
 * resident-memory breakdown. Pass the host's real `system.gpu_limit_gb` when
 * you have it so the verdict matches what the server will later say. */
export function blockedForLocalModels(
  ocrModel: string | null,
  figureModel: string | null,
  reviewModel: string | null,
  limit: number = GPU_MAX_GB
): Pick<GpuState, "blocked" | "warning"> {
  const { blocked, warning } = gpuState(ocrModel, figureModel, reviewModel, limit);
  return { blocked, warning };
}

export interface ComputeResult extends EstimateResponse {
  t_gpu: number;
  t_cloud: number;
  t_cpu: number;
  gpuRaw: number;
  cloudRaw: number;
  cpuRaw: number;
  costUnknown: boolean;
  locals: string[];
}

export function computeEstimate(load: LoadShape, pipeline: Pipeline, workers: number): ComputeResult {
  const core = computeCore(load, pipeline, workers);
  const alternatives: EstimateAlternative[] = [];
  if (core.bottleneck === "cloud" || core.bottleneck === "gpu") {
    const cloudAlt: Partial<Pipeline> = { ocr_model: "openai:gpt-4o" };
    // One level deep only — computeCore never recurses into alternatives,
    // so this can't blow the stack even when the alternative pipeline is
    // itself cloud/gpu-bound.
    const altCore = computeCore(load, { ...pipeline, ...cloudAlt }, workers);
    alternatives.push({
      label: "Leer también en la nube (gpt-4o)",
      pipeline_patch: cloudAlt,
      total_seconds: altCore.total_seconds,
      total_usd: altCore.total_usd,
    });
  }
  return { ...core, alternatives };
}

function computeCore(load: LoadShape, pipeline: Pipeline, workers: number): ComputeResult {
  const ocr = modelFor(pipeline.ocr_model);
  const fig = pipeline.describe_figures ? modelFor(pipeline.figure_model) : NONE_MODEL;
  const rev = modelFor(pipeline.review_model);

  const gpu = gpuState(pipeline.ocr_model, pipeline.describe_figures ? pipeline.figure_model : null, pipeline.review_model);
  const { blocked } = gpu;
  const localNames = distinctLocalModels(
    pipeline.ocr_model,
    pipeline.describe_figures ? pipeline.figure_model : null,
    pipeline.review_model
  ).map((m) => m.name);

  let gpuRaw = 0;
  let cloudRaw = 0;
  const cpuRaw = load.pages * CPU_SECONDS_PER_PAGE;
  let cost = 0;
  let costUnknown = false;
  const stages: Record<string, StageKind> = {};

  function add(m: ModelSpec, secs: number, usd: number | undefined, key: string) {
    if (m.kind === "gpu") gpuRaw += secs;
    else if (m.kind === "cloud") {
      cloudRaw += secs;
      if (m.unknownPrice) costUnknown = true;
      else cost += usd ?? 0;
    } else if (m.kind === "cpu") {
      // tesseract counted in cpu lane
    }
    stages[key] = m.kind === "cpu" ? "cpu" : m.kind;
  }

  add(ocr, load.scanned * (ocr.secPerPage?.ocr ?? 0), load.scanned * (ocr.usdPerUnit?.ocr ?? 0), "ocr");
  if (fig.kind !== "none") add(fig, load.figs * (fig.secPerPage?.fig ?? 0), load.figs * (fig.usdPerUnit?.fig ?? 0), "figures");
  else stages["figures"] = "none";
  if (rev.kind !== "none") add(rev, load.pages * (rev.secPerPage?.rev ?? 0), load.pages * (rev.usdPerUnit?.rev ?? 0), "review");
  else stages["review"] = "none";

  const usesGpu = gpuRaw > 0;
  const par = usesGpu ? 1 : workers;
  const cloudPar = Math.min(REMOTE_PERMITS, workers);
  const t_gpu = gpuRaw;
  const t_cloud = cloudRaw / (cloudPar || 1);
  const t_cpu = (cpuRaw + (ocr.kind === "cpu" ? load.scanned * (ocr.secPerPage?.ocr ?? 0) : 0)) / workers;
  const total = Math.max(t_gpu, t_cloud, t_cpu);
  const bottleneck: Bottleneck = total === t_gpu && gpuRaw > 0 ? "gpu" : total === t_cloud && cloudRaw > 0 ? "cloud" : "cpu";

  const stageList: EstimateStage[] = [
    { key: "extract", model: null, kind: "none", seconds: 0.2, usd: 0, unknown_price: false },
    {
      key: "ocr",
      model: pipeline.ocr_model,
      kind: stages["ocr"],
      seconds: load.scanned * (ocr.secPerPage?.ocr ?? 0),
      usd: ocr.unknownPrice ? null : load.scanned * (ocr.usdPerUnit?.ocr ?? 0),
      unknown_price: !!ocr.unknownPrice,
    },
    {
      key: "figures",
      model: pipeline.describe_figures ? pipeline.figure_model : null,
      kind: stages["figures"],
      seconds: fig.kind === "none" ? 0 : load.figs * (fig.secPerPage?.fig ?? 0),
      usd: fig.kind === "none" ? 0 : fig.unknownPrice ? null : load.figs * (fig.usdPerUnit?.fig ?? 0),
      unknown_price: fig.kind !== "none" && !!fig.unknownPrice,
    },
    {
      key: "review",
      model: pipeline.review_model,
      kind: stages["review"],
      seconds: rev.kind === "none" ? 0 : load.pages * (rev.secPerPage?.rev ?? 0),
      usd: rev.kind === "none" ? 0 : rev.unknownPrice ? null : load.pages * (rev.usdPerUnit?.rev ?? 0),
      unknown_price: rev.kind !== "none" && !!rev.unknownPrice,
    },
    { key: "write", model: null, kind: "none", seconds: 0.3, usd: 0, unknown_price: false },
  ];

  return {
    stages: stageList,
    total_seconds: blocked ? 0 : total,
    total_usd: blocked ? null : costUnknown ? null : cost,
    parallel_files: blocked ? 0 : par,
    bottleneck,
    blocked,
    alternatives: [],
    gpu_resident_gb: gpu.gpu_resident_gb,
    gpu_limit_gb: gpu.gpu_limit_gb,
    gpu_headroom_gb: gpu.gpu_headroom_gb,
    warning: gpu.warning,
    t_gpu,
    t_cloud,
    t_cpu,
    gpuRaw,
    cloudRaw,
    cpuRaw,
    costUnknown,
    locals: localNames,
  };
}
