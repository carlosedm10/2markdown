/**
 * Mock-mode data — mirrors the example values baked into
 * docs/mockups/desktop-v4.html (the "Apuntes" test folder: 8 archivos · 25
 * páginas · 22 escaneadas · ~6 figuras · 41 MB) so the UI can be reviewed
 * before twomarkdown/server exists. Used only by mockClient.ts.
 */
import type { FolderInfo, InspectFile, Job, JobFileState, ModelsResponse, PageDetail, Preset, Settings, Sync, SystemInfo } from "./types";

/** GET/PUT /api/settings fixture — mock-mode default, wizard not yet run
 * (`wizard_done: false`) so VITE_MOCK=1 boots straight into the wizard the
 * way a fresh install would. mockClient.ts owns the mutable copy; this is
 * only the shape a fresh server would answer with. */
export const FIXTURE_SETTINGS: Settings = {
  wizard_done: false,
  default_output_mode: "sibling",
  default_output_dir: null,
  default_preset_id: "apuntes-a-mano",
  default_mode: "once",
  local_gpu_permits: 1,
};

export const FIXTURE_SYSTEM: SystemInfo = {
  ram_gb: 48,
  gpu_limit_gb: 36,
  cpu_cores: 16,
  chip: "Apple M3 Max",
  tesseract: { installed: true, version: "5.3.4" },
  ollama: {
    running: true,
    version: "0.6.0",
    models: [
      { name: "ollama:qwen2.5vl:7b", size_gb: 5.6, loaded: true, vision: true },
      { name: "ollama:qwen2.5vl:32b", size_gb: 20, loaded: false, vision: true },
    ],
  },
  cloud: {
    openai: { key_present: true, status: "out_of_credit" },
    anthropic: { key_present: false, status: "no_key" },
    google: { key_present: false, status: "no_key" },
    groq: { key_present: false, status: "no_key" },
    mistral: { key_present: false, status: "no_key" },
    openrouter: { key_present: false, status: "no_key" },
  },
  prices: {
    source: "genai-prices",
    updated_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
    unknown_models: ["openai:unreleased"],
  },
};

export const FIXTURE_FILES: InspectFile[] = [
  { path: "Apuntes/Tema 1.pdf", kind: "scanned", pages: 3, scanned_pages: 3, figures: 1, text_chars: 0, bytes: 5_200_000 },
  { path: "Apuntes/Tema 2.pdf", kind: "scanned", pages: 4, scanned_pages: 4, figures: 2, text_chars: 0, bytes: 6_800_000 },
  { path: "Apuntes/Tema 3.pdf", kind: "scanned", pages: 2, scanned_pages: 2, figures: 0, text_chars: 0, bytes: 3_100_000 },
  { path: "Apuntes/Tema 4.pdf", kind: "scanned", pages: 3, scanned_pages: 3, figures: 1, text_chars: 0, bytes: 4_900_000 },
  { path: "Apuntes/Bibliografía.docx", kind: "office", pages: 1, scanned_pages: 0, figures: 0, text_chars: 4200, bytes: 120_000 },
  { path: "Apuntes/Tema 5.pdf", kind: "scanned", pages: 5, scanned_pages: 5, figures: 1, text_chars: 0, bytes: 7_400_000 },
  { path: "Apuntes/Tema 6.pdf", kind: "scanned", pages: 3, scanned_pages: 3, figures: 0, text_chars: 0, bytes: 5_000_000 },
  { path: "Apuntes/Tema 7.pdf", kind: "scanned", pages: 3, scanned_pages: 3, figures: 1, text_chars: 0, bytes: 4_600_000 },
];

export const FIXTURE_TOTALS = {
  files: FIXTURE_FILES.length,
  pages: FIXTURE_FILES.reduce((a, f) => a + f.pages, 0),
  scanned_pages: FIXTURE_FILES.reduce((a, f) => a + f.scanned_pages, 0),
  figures: FIXTURE_FILES.reduce((a, f) => a + f.figures, 0),
  bytes: FIXTURE_FILES.reduce((a, f) => a + f.bytes, 0),
};

export const BUILTIN_PRESETS: Preset[] = [
  {
    id: "rapido",
    name: "Rápido",
    builtin: true,
    pipeline: {
      ocr_model: "tesseract",
      figure_model: null,
      review_model: null,
      workers: 4,
      describe_figures: false,
      clean: true,
      tables: true,
      emit_chunks: false,
      ocr_dpi: 300,
    },
  },
  {
    id: "apuntes-a-mano",
    name: "Apuntes a mano",
    builtin: true,
    pipeline: {
      ocr_model: "ollama:qwen2.5vl:7b",
      figure_model: "ollama:qwen2.5vl:7b",
      review_model: "openai:gpt-4o-mini",
      workers: 1,
      describe_figures: true,
      clean: true,
      tables: true,
      emit_chunks: false,
      ocr_dpi: 300,
    },
  },
  {
    id: "archivo-grande",
    name: "Archivo grande",
    builtin: true,
    pipeline: {
      ocr_model: "tesseract",
      figure_model: null,
      review_model: null,
      workers: 4,
      describe_figures: false,
      clean: true,
      tables: true,
      emit_chunks: false,
      ocr_dpi: 300,
    },
  },
];

const OUTPUT_DIR = "~/Documentos/Apuntes_2markdown";

function jobFile(
  index: number,
  path: string,
  status: JobFileState["status"],
  pages: number | null,
  seconds: number | null,
  reason: string | null
): JobFileState {
  const base = path.split("/").pop()!.replace(/\.[^.]+$/, "");
  const done = status === "ok" || status === "warn";
  return {
    index,
    path,
    status,
    pages,
    seconds,
    reason,
    output_path: done ? `${OUTPUT_DIR}/${base}.md` : null,
    kind: path.endsWith(".pdf") ? "pdf" : path.endsWith(".docx") ? "office" : "other",
    assets_dir: done && pages ? `${OUTPUT_DIR}/${base}_assets` : null,
    engine_used: done ? "ollama:qwen2.5vl:7b" : null,
    review_changes_count: status === "ok" ? 1 : 0,
  };
}

export const FIXTURE_JOB_FILES: JobFileState[] = [
  jobFile(0, "Apuntes/Tema 1.pdf", "ok", 3, 372, null),
  jobFile(1, "Apuntes/Tema 2.pdf", "ok", 4, 520, null),
  jobFile(2, "Apuntes/Tema 3.pdf", "warn", 2, 185, "1 página leída con el motor rápido — la IA no respondió"),
  jobFile(3, "Apuntes/Tema 4.pdf", "running", 3, null, null),
  jobFile(4, "Apuntes/Bibliografía.docx", "pending", null, null, null),
  jobFile(5, "Apuntes/Tema 5.pdf", "pending", 5, null, null),
  jobFile(6, "Apuntes/Tema 6.pdf", "pending", 3, null, null),
  jobFile(7, "Apuntes/Tema 7.pdf", "pending", 3, null, null),
];

export function makeFixtureJob(jobId: string): Job {
  return {
    job_id: jobId,
    status: "running",
    output: OUTPUT_DIR,
    pipeline: BUILTIN_PRESETS[1].pipeline,
    mode: "once",
    files: FIXTURE_JOB_FILES.map((f) => ({ ...f })),
    ok: 2,
    warn: 1,
    failed: 0,
    cancelled: 0,
    seconds: null,
    usd: 0.02,
    input: "~/Documentos/Apuntes",
    preset_id: "apuntes-a-mano",
    started: new Date(Date.now() - 8 * 60_000).toISOString(),
    finished: null,
  };
}

const PAGE_MARKDOWN = `# Tema 4 – Reglas de integración

- $\\int c\\cdot f(x)\\,dx = c\\cdot\\int f(x)\\,dx$
- $\\int \\dfrac{f'(x)}{f(x)}\\,dx = \\ln|f(x)| + C$
- $\\int a^{f(x)}\\cdot f'(x)\\,dx = \\dfrac{a^{f(x)}}{\\ln a} + C$

*/d es la razón/*

- $\\int \\dfrac{-f'}{1+f^2}\\,dx = \\operatorname{arccot}(f) + C$
`;

const PAGE_ORIGINAL_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' width='420' height='560'>
    <rect width='420' height='560' fill='#fbfaf4'/>
    <g fill='none' stroke='#e2a5a0' stroke-width='1.5'><line x1='40' y1='0' x2='40' y2='560'/></g>
    <g fill='#22355b' font-family='Georgia, serif' font-size='19' font-style='italic'>
      <text x='55' y='40'>Tema 4 – Reglas de integración</text>
      <text x='55' y='80'>&#8747; c&#183;f(x) dx = c&#183;&#8747; f(x) dx</text>
      <text x='55' y='120' fill='#7a5b00'>&#8747; f'(x)/f(x) dx = ln|f(x)| + C</text>
      <text x='55' y='160'>&#8747; a^f(x)&#183;f'(x) dx = a^f(x)/ln a + C</text>
      <text x='55' y='200' font-size='14'>/d es la razón/</text>
      <text x='55' y='240'>&#8747; &#8722;f'/(1+f&#178;) dx = arccot f + C</text>
    </g>
  </svg>`);

export function makeFixturePage(): PageDetail {
  return {
    image_png_base64: null,
    image_url: PAGE_ORIGINAL_SVG,
    markdown: PAGE_MARKDOWN,
    changes: [
      { before: "f(x)", after: "f'(x)" },
      { before: "\\arccot", after: "\\operatorname{arccot}" },
    ],
    pages: 3,
  };
}

export const FIXTURE_SYNCS: Sync[] = [
  {
    id: "sync-1",
    input: "~/Escaneos",
    output: "~/Obsidian/Apuntes",
    preset_id: "apuntes-a-mano",
    enabled: true,
    last_run: new Date(Date.now() - 2 * 3600_000).toISOString(),
    files_today: 3,
  },
];

/** Per-folder preset memory (GET/DELETE /api/folders) — distinct from a Sync:
 * no watching, just "this folder was last converted with this preset". Only
 * used by mockClient.ts until the server ships the real endpoints. */
export const FIXTURE_FOLDERS: FolderInfo[] = [
  {
    path: "~/Documentos/Apuntes",
    preset_id: "apuntes-a-mano",
    last_used: new Date(Date.now() - 26 * 3600_000).toISOString(),
  },
];

/** GET /api/models fixture — mirrors the same catalog `lib/scheduling.ts`'s
 * MODELS uses for the estimate simulator, reshaped into the engine's
 * local/cloud/providers/stages split (see docs/desktop-app.md and
 * twomarkdown/server/schemas.py). `openai:unreleased` — a cloud model with no
 * known price — lives ONLY here, never in a hardcoded option list a real
 * (non-mock) build would also render: it exists so ModelPicker's
 * unknown_price / "Otro modelo…" affordances have something to show in
 * VITE_MOCK=1 without needing the user to type a custom id by hand. */
export const FIXTURE_MODELS: ModelsResponse = {
  local: [
    { id: "ollama:qwen2.5vl:7b", name: "qwen2.5vl:7b", size_gb: 5.6, loaded: true, vision: true, installed: true },
    { id: "ollama:qwen2.5vl:32b", name: "qwen2.5vl:32b", size_gb: 20, loaded: false, vision: true, installed: true },
  ],
  cloud: [
    { id: "openai:gpt-4o-mini", provider: "openai", vision: true, price_known: true, key_present: true },
    { id: "openai:gpt-4o", provider: "openai", vision: true, price_known: true, key_present: true },
    { id: "openai:unreleased", provider: "openai", vision: null, price_known: false, key_present: true },
    { id: "anthropic:claude-3-5-sonnet", provider: "anthropic", vision: true, price_known: true, key_present: false },
  ],
  providers: [
    { id: "openai", key_present: true, env_var: "OPENAI_API_KEY" },
    { id: "anthropic", key_present: false, env_var: "ANTHROPIC_API_KEY" },
    { id: "google", key_present: false, env_var: "GOOGLE_API_KEY" },
    { id: "groq", key_present: false, env_var: "GROQ_API_KEY" },
    { id: "mistral", key_present: false, env_var: "MISTRAL_API_KEY" },
    { id: "openrouter", key_present: false, env_var: "OPENROUTER_API_KEY" },
  ],
  stages: {
    ocr: ["tesseract", "ollama:qwen2.5vl:7b", "ollama:qwen2.5vl:32b", "openai:gpt-4o"],
    figures: ["ollama:qwen2.5vl:7b", "ollama:qwen2.5vl:32b", "openai:gpt-4o-mini", "openai:gpt-4o"],
    review: ["openai:gpt-4o-mini", "openai:gpt-4o", "ollama:qwen2.5vl:7b", "openai:unreleased", "anthropic:claude-3-5-sonnet"],
  },
};
