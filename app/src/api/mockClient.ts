/**
 * VITE_MOCK=1 stand-in for the server, so every screen in this app can be
 * reviewed before twomarkdown/server exists. Fixtures mirror the example
 * values in docs/mockups/desktop-v4.html (see fixtures.ts); the scheduling
 * math mirrors the mockup's simulator (see lib/scheduling.ts). Also drives a
 * fake event stream for Convertir's running-queue state instead of a real
 * WebSocket.
 */
import type { ApiClient, JobEventUnsubscribe } from "./contract";
import type {
  CloudKeyResponse,
  CloudProviderId,
  EstimateRequest,
  EstimateResponse,
  FolderInfo,
  InspectRequest,
  InspectResponse,
  Job,
  JobCreateRequest,
  JobCreateResponse,
  JobEvent,
  JobRetryRequest,
  ManualPriceRequest,
  ModelProblem,
  ModelsResponse,
  OpenPathRequest,
  OriginalInfo,
  PageDetail,
  PageUpdateRequest,
  Preset,
  PresetsUpdateRequest,
  ResolveModelRequest,
  ResolveModelResponse,
  Settings,
  SettingsUpdateRequest,
  Sync,
  SyncCreateRequest,
  SyncUpdateRequest,
  SystemInfo,
} from "./types";
import {
  BUILTIN_PRESETS,
  FIXTURE_FILES,
  FIXTURE_FOLDERS,
  FIXTURE_MODELS,
  FIXTURE_SETTINGS,
  FIXTURE_SYNCS,
  FIXTURE_SYSTEM,
  FIXTURE_TOTALS,
  makeFixtureJob,
  makeFixturePage,
} from "./fixtures";
import { computeEstimate, LOADS } from "../lib/scheduling";

function delay<T>(v: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(v), ms));
}

let presets: Preset[] = BUILTIN_PRESETS.map((p) => ({ ...p, pipeline: { ...p.pipeline } }));
let syncs: Sync[] = [...FIXTURE_SYNCS];
let folders: FolderInfo[] = [...FIXTURE_FOLDERS];
let settings: Settings = { ...FIXTURE_SETTINGS };

/** Mirrors the server-side resolution `InspectResponse.default_output` will
 * do from `Settings.default_output_mode`/`default_output_dir` — a sibling
 * folder suffixed `_2markdown`, or always the fixed configured folder. Kept
 * here (not re-derived by Convert.tsx) so the mock behaves like the real
 * server: the client only ever reads this field, never recomputes it. */
function resolveDefaultOutput(inputPath: string): string {
  if (settings.default_output_mode === "fixed" && settings.default_output_dir) {
    return settings.default_output_dir;
  }
  return `${inputPath.replace(/\/+$/, "")}_2markdown`;
}
const manualPrices: Record<string, { input: number; output: number }> = {};
let openaiStatus: SystemInfo["cloud"]["openai"]["status"] = FIXTURE_SYSTEM.cloud.openai.status;
const cloudKeyPresent: Record<CloudProviderId, boolean> = {
  openai: FIXTURE_SYSTEM.cloud.openai.key_present,
  anthropic: FIXTURE_SYSTEM.cloud.anthropic.key_present,
  google: FIXTURE_SYSTEM.cloud.google.key_present,
  groq: FIXTURE_SYSTEM.cloud.groq.key_present,
  mistral: FIXTURE_SYSTEM.cloud.mistral.key_present,
  openrouter: FIXTURE_SYSTEM.cloud.openrouter.key_present,
};

/** Mock stand-in for the server's POST /api/models/resolve — looks a free
 * `proveedor:modelo` id up against FIXTURE_MODELS/providers instead of really
 * calling Ollama/genai-prices. Mirrors the shape docs/desktop-app.md and
 * twomarkdown/server/schemas.py's ResolveProblem describe. */
function mockResolveModel(id: string): ResolveModelResponse {
  if (id === "tesseract") {
    return { id, kind: "cpu", installed: true, key_present: true, price_known: true, vision: false, problems: [] };
  }
  const local = FIXTURE_MODELS.local.find((m) => m.id === id);
  if (local) {
    const problems: ModelProblem[] = local.installed ? [] : ["not_installed"];
    return { id, kind: "local", installed: local.installed, key_present: true, price_known: true, vision: local.vision, problems };
  }
  const cloud = FIXTURE_MODELS.cloud.find((m) => m.id === id);
  if (cloud) {
    const problems: ModelProblem[] = [];
    if (!cloud.key_present) problems.push("no_api_key");
    if (!cloud.price_known) problems.push("unknown_price");
    return { id, kind: "cloud", installed: true, key_present: cloud.key_present, price_known: cloud.price_known, vision: cloud.vision, problems };
  }
  // Not a known local or cloud model — is it at least a known cloud provider?
  const [providerId, ...rest] = id.split(":");
  const provider = FIXTURE_MODELS.providers.find((p) => p.id === providerId);
  if (provider && rest.length > 0) {
    return {
      id,
      kind: "cloud",
      installed: true,
      key_present: provider.key_present,
      price_known: false,
      vision: null,
      problems: [...(provider.key_present ? [] : (["no_api_key"] as ModelProblem[])), "unknown_price"],
    };
  }
  // Looks local (bare "ollama:xyz" style) but isn't in the fixture catalog —
  // treat as a not-yet-installed local model rather than an unknown provider.
  if (providerId === "ollama" && rest.length > 0) {
    return { id, kind: "local", installed: false, key_present: true, price_known: true, vision: false, problems: ["not_installed"] };
  }
  return { id, kind: "cloud", installed: false, key_present: false, price_known: false, vision: null, problems: ["unknown_provider"] };
}

function buildScript(jobId: string): JobEvent[] {
  // A fixed script reproducing the mockup's "3 de 8 archivos" moment,
  // replayed once per subscription so the Queue screen has something live
  // to show without a real engine.
  const events: JobEvent[] = [
    { type: "job_started", job_id: jobId },
    { type: "file_started", index: 0, path: "Apuntes/Tema 1.pdf", pages: 3 },
    { type: "semaphores", gpu: { permits: 1, held_by: [{ file: "Tema 1.pdf", page: 1, stage: "ocr" }], waiting: 0 }, cloud: { permits: 4, in_use: 0 }, cpu: { workers: 1, active: 1 } },
    { type: "page_done", index: 0, page: 1, engine: "ollama:qwen2.5vl:7b", seconds: 41.2, fallback: false, review_changes: [] },
    { type: "page_done", index: 0, page: 2, engine: "ollama:qwen2.5vl:7b", seconds: 38.9, fallback: false, review_changes: [{ line: 3, before: "f(x)", after: "f'(x)" }] },
    { type: "page_done", index: 0, page: 3, engine: "ollama:qwen2.5vl:7b", seconds: 40.1, fallback: false, review_changes: [] },
    { type: "file_done", index: 0, status: "ok", seconds: 372 },
    { type: "cost", usd_so_far: 0.01 },
    { type: "file_started", index: 1, path: "Apuntes/Tema 2.pdf", pages: 4 },
    { type: "log", level: "info", message: "Tema 2.pdf p.4  ollama:qwen2.5vl:7b  ok 39.8s · review gpt-4o-mini 0 changes" },
    { type: "file_done", index: 1, status: "ok", seconds: 520 },
    { type: "cost", usd_so_far: 0.02 },
    { type: "file_started", index: 2, path: "Apuntes/Tema 3.pdf", pages: 2 },
    { type: "log", level: "warning", message: "Tema 3.pdf p.2  ollama:qwen2.5vl:7b  Invalid response (role '') → reload model → fallback tesseract" },
    { type: "file_done", index: 2, status: "warn", seconds: 185, reason: "1 página leída con el motor rápido — la IA no respondió" },
    { type: "file_started", index: 3, path: "Apuntes/Tema 4.pdf", pages: 3 },
    { type: "page_started", index: 3, page: 1, engine: "ollama:qwen2.5vl:7b" },
    {
      type: "semaphores",
      gpu: { permits: 1, held_by: [{ file: "Tema 4.pdf", page: 2, stage: "ocr" }], waiting: 3 },
      cloud: { permits: 4, in_use: 1 },
      cpu: { workers: 1, active: 0 },
    },
    { type: "page_done", index: 3, page: 1, engine: "ollama:qwen2.5vl:7b", seconds: 41.2, fallback: false, review_changes: [] },
    { type: "page_started", index: 3, page: 2, engine: "ollama:qwen2.5vl:7b" },
  ];
  return events;
}

export const mockClient: ApiClient = {
  getSystem: () =>
    delay<SystemInfo>({
      ...FIXTURE_SYSTEM,
      cloud: {
        openai: { key_present: cloudKeyPresent.openai, status: openaiStatus },
        anthropic: { key_present: cloudKeyPresent.anthropic, status: cloudKeyPresent.anthropic ? "unknown" : "no_key" },
        google: { key_present: cloudKeyPresent.google, status: cloudKeyPresent.google ? "unknown" : "no_key" },
        groq: { key_present: cloudKeyPresent.groq, status: cloudKeyPresent.groq ? "unknown" : "no_key" },
        mistral: { key_present: cloudKeyPresent.mistral, status: cloudKeyPresent.mistral ? "unknown" : "no_key" },
        openrouter: { key_present: cloudKeyPresent.openrouter, status: cloudKeyPresent.openrouter ? "unknown" : "no_key" },
      },
    }),

  inspect: (body: InspectRequest) =>
    delay<InspectResponse>({
      inspect_id: "inspect-fixture-1",
      files: FIXTURE_FILES,
      totals: FIXTURE_TOTALS,
      root: body.path,
      last_preset_id: folders.find((f) => f.path === body.path)?.preset_id ?? null,
      default_output: resolveDefaultOutput(body.path),
    }),

  getSettings: () => delay({ ...settings }),
  putSettings: (body: SettingsUpdateRequest) => {
    settings = { ...body };
    return delay({ ...settings });
  },

  estimate: (body: EstimateRequest) => {
    // "test:apuntes" / "test:archivo" is a mock-only convention the Pipeline
    // screen's load selector uses to preview the simulator's two example
    // loads without a real inspect_id; any other inspect_id uses the
    // Apuntes fixture (the only one this mock server actually inspects).
    const load = body.inspect_id === "test:archivo" ? LOADS.archivo : LOADS.apuntes;
    const r = computeEstimate(load, body.pipeline, body.pipeline.workers);
    // apply any manually-added prices the user typed into the warn block
    r.stages.forEach((s) => {
      if (s.unknown_price && s.model && manualPrices[s.model]) {
        s.usd = 0; // manual price applied — treat as known from now on
        s.unknown_price = false;
      }
    });
    const resp: EstimateResponse = {
      stages: r.stages,
      total_seconds: r.total_seconds,
      total_usd: r.total_usd,
      parallel_files: r.parallel_files,
      bottleneck: r.bottleneck,
      blocked: r.blocked,
      alternatives: r.alternatives,
      gpu_resident_gb: r.gpu_resident_gb,
      gpu_limit_gb: r.gpu_limit_gb,
      gpu_headroom_gb: r.gpu_headroom_gb,
      warning: r.warning,
    };
    return delay(resp, 160);
  },

  getPresets: () => delay(presets.map((p) => ({ ...p }))),
  // FULL replace, mirroring the real server's PUT /api/presets (C7): a
  // preset that exists but isn't in the request is gone afterwards. Every
  // app-side call site uses `patchPresets` below instead; this stays only
  // so the mock satisfies the same contract the real client does.
  putPresets: (body: PresetsUpdateRequest) => {
    presets = body.presets.map((p) => ({ ...p }));
    return delay(presets.map((p) => ({ ...p })));
  },
  // Upsert by id, never deletes — mirrors the real PATCH /api/presets
  // (C7). This is what every save/edit-one-preset call site actually uses.
  patchPresets: (body: PresetsUpdateRequest) => {
    const byId = new Map(presets.map((p) => [p.id, p]));
    for (const p of body.presets) byId.set(p.id, { ...p });
    presets = [...byId.values()];
    return delay(presets.map((p) => ({ ...p })));
  },
  deletePreset: (id: string) => {
    presets = presets.filter((p) => p.id !== id);
    return delay(presets.map((p) => ({ ...p })));
  },

  getModels: () => delay<ModelsResponse>(FIXTURE_MODELS),
  resolveModel: (body: ResolveModelRequest) => delay(mockResolveModel(body.id), 260),

  getFolders: () => delay(folders.map((f) => ({ ...f }))),
  forgetFolder: (path: string) => {
    folders = folders.filter((f) => f.path !== path);
    return delay({ ok: true });
  },

  createJob: (_body: JobCreateRequest) => delay<JobCreateResponse>({ job_id: "job-fixture-1" }),
  getJob: (id: string) => delay(makeFixtureJob(id)),
  listJobs: (_limit?: number) => delay([makeFixtureJob("job-fixture-1")]),
  pauseJob: (id: string) => delay({ ...makeFixtureJob(id), status: "paused" as const }),
  resumeJob: (id: string) => delay({ ...makeFixtureJob(id), status: "running" as const }),
  cancelJob: (id: string) => delay({ ...makeFixtureJob(id), status: "cancelled" as const }),
  cancelFile: (id: string, fileIndex: number) => {
    const job = makeFixtureJob(id);
    job.files = job.files.map((f) => (f.index === fileIndex ? { ...f, status: "cancelled" as const } : f));
    return delay(job);
  },
  retryJob: (id: string, _body: JobRetryRequest) => delay(makeFixtureJob(id)),

  subscribeJobEvents: (id: string, onEvent: (e: JobEvent) => void): JobEventUnsubscribe => {
    const script = buildScript(id);
    let i = 0;
    let stopped = false;
    function tick() {
      if (stopped || i >= script.length) return;
      onEvent(script[i]);
      i++;
      setTimeout(tick, 900 + Math.random() * 500);
    }
    const first = setTimeout(tick, 300);
    return () => {
      stopped = true;
      clearTimeout(first);
    };
  },

  getPage: (_jobId: string, _fileIndex: number, _page: number) => delay(makeFixturePage()),
  putPage: (_jobId: string, _fileIndex: number, _page: number, body: PageUpdateRequest) =>
    delay({ ...makeFixturePage(), markdown: body.markdown }),
  getOriginal: (_jobId: string, _fileIndex: number) => delay<OriginalInfo>({ kind: "pdf", previewable: true, pages: 3, path: null }),

  openPath: (_body: OpenPathRequest) => delay({ ok: true, error: null }),
  revealPath: (_body: OpenPathRequest) => delay({ ok: true, error: null }),

  getSyncs: () => delay(syncs.map((s) => ({ ...s }))),
  createSync: (body: SyncCreateRequest) => {
    const s: Sync = { id: `sync-${syncs.length + 1}`, enabled: true, last_run: null, files_today: 0, ...body };
    syncs = [...syncs, s];
    return delay(s);
  },
  patchSync: (id: string, body: SyncUpdateRequest) => {
    syncs = syncs.map((s) => (s.id === id ? { ...s, ...body } : s));
    const s = syncs.find((x) => x.id === id);
    return delay({ ...(s as Sync) });
  },
  deleteSync: (id: string) => {
    syncs = syncs.filter((s) => s.id !== id);
    return delay({ ok: true });
  },

  manualPrice: (body: ManualPriceRequest) => {
    manualPrices[body.model] = { input: body.input_per_mtok, output: body.output_per_mtok };
    return delay(body);
  },

  pickFolder: () => delay({ path: null }),

  pullOllamaModel: (_model: string) => delay({ ok: true }, 600),

  saveCloudKey: (provider: CloudProviderId, _key: string) => {
    cloudKeyPresent[provider] = true;
    if (provider === "openai") openaiStatus = "ok";
    return delay<CloudKeyResponse>({ provider, key_present: true, status: "ok" }, 400);
  },
  removeCloudKey: (provider: CloudProviderId) => {
    cloudKeyPresent[provider] = false;
    if (provider === "openai") openaiStatus = "no_key";
    return delay<CloudKeyResponse>({ provider, key_present: false, status: "no_key" }, 200);
  },
};
