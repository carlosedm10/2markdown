import { create } from "zustand";
import type { InspectResponse, JobEvent, Pipeline, Preset, Settings, SystemInfo } from "../api/types";

export type OcrChoice = "fast" | "local-ai" | "cloud-ai" | "custom";

interface AppState {
  onboarded: boolean;
  setOnboarded: (v: boolean) => void;

  system: SystemInfo | null;
  setSystem: (s: SystemInfo) => void;

  /** Cached GET /api/settings response — every reader (Wizard, Convert,
   * Team) treats the server as the source of truth and refreshes this after
   * any PUT; this is a cache for cheap reads between screens, not a second
   * source of truth. `outputDefault`/`ocrChoice`/`outputPath` used to shadow
   * pieces of this on the client only, which is exactly how the wizard's
   * choices and Convertir's actual behaviour drifted apart (see the wizard
   * UX bug this store shape is fixing). */
  settings: Settings | null;
  setSettings: (s: Settings) => void;

  inputPath: string;
  setInputPath: (p: string) => void;

  inspect: InspectResponse | null;
  setInspect: (i: InspectResponse | null) => void;

  presets: Preset[];
  setPresets: (p: Preset[]) => void;
  selectedPresetId: string;
  setSelectedPresetId: (id: string) => void;

  editingPipeline: Pipeline | null;
  editingPresetId: string | null;
  startEditPreset: (id: string | null, pipeline: Pipeline) => void;
  clearEditPreset: () => void;

  outputPath: string;
  setOutputPath: (p: string) => void;

  mode: "once" | "sync";
  setMode: (m: "once" | "sync") => void;

  currentJobId: string | null;
  setCurrentJobId: (id: string | null) => void;
  jobEvents: JobEvent[];
  pushJobEvent: (e: JobEvent) => void;
  resetJobEvents: () => void;

  theme: "light" | "dark" | "system";
  setTheme: (t: "light" | "dark" | "system") => void;
}

export const useAppStore = create<AppState>((set) => ({
  // No localStorage flag: whether the wizard shows is read fresh from
  // GET /api/settings.wizard_done on every boot (see App.tsx) — a reload
  // used to lose the wizard's choices AND still skip the wizard because of
  // this flag, which is the second half of the reported bug.
  onboarded: false,
  setOnboarded: (v) => set({ onboarded: v }),

  system: null,
  setSystem: (s) => set({ system: s }),

  settings: null,
  setSettings: (s) => set({ settings: s }),

  // Persisted so a reload doesn't lose the folder the user just chose (see
  // flow-issues.md N4) — Convert.tsx's mount effect re-inspects it as soon
  // as the app comes back up, same as it already does within one session.
  inputPath: (() => {
    try {
      return localStorage.getItem("2md.lastInputPath") ?? "";
    } catch {
      return "";
    }
  })(),
  setInputPath: (p) => {
    try {
      if (p) localStorage.setItem("2md.lastInputPath", p);
      else localStorage.removeItem("2md.lastInputPath");
    } catch {
      /* ignore */
    }
    set({ inputPath: p });
  },

  inspect: null,
  setInspect: (i) => set({ inspect: i }),

  presets: [],
  setPresets: (p) => set({ presets: p }),
  selectedPresetId: "apuntes-a-mano",
  setSelectedPresetId: (id) => set({ selectedPresetId: id }),

  editingPipeline: null,
  editingPresetId: null,
  startEditPreset: (id, pipeline) => set({ editingPresetId: id, editingPipeline: pipeline }),
  clearEditPreset: () => set({ editingPresetId: null, editingPipeline: null }),

  // Per-job editable output path — seeded from `InspectResponse.
  // default_output` (server-resolved from Settings) each time Convert.tsx
  // inspects a folder, never from a client-side default (see Convert.tsx's
  // runInspect).
  outputPath: "",
  setOutputPath: (p) => set({ outputPath: p }),

  mode: "once",
  setMode: (m) => set({ mode: m }),

  currentJobId: null,
  setCurrentJobId: (id) => set({ currentJobId: id }),
  jobEvents: [],
  pushJobEvent: (e) => set((s) => ({ jobEvents: [...s.jobEvents, e] })),
  resetJobEvents: () => set({ jobEvents: [] }),

  theme: (localStorage.getItem("2md.theme") as "light" | "dark" | "system") ?? "system",
  setTheme: (t) => {
    try {
      localStorage.setItem("2md.theme", t);
    } catch {
      /* ignore */
    }
    set({ theme: t });
  },
}));
