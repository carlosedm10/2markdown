/**
 * Real API client — thin fetch/WebSocket wrapper over the contract in
 * app/src/api/types.ts and docs/desktop-app.md. Every method matches an
 * endpoint in that doc's table. No mock data lives here; see mockClient.ts
 * for the VITE_MOCK=1 stand-in and jobEvents.ts for the shared JobEvent
 * subscription type both implementations satisfy.
 */
import type {
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
import type { ApiClient, JobEventUnsubscribe } from "./contract";

const BASE = "/api";

/** Thrown by `req` for any non-2xx response. Carries the HTTP status and,
 * when the body parsed as JSON, its `detail` field verbatim (e.g. FastAPI's
 * `{code, message, ...}` shape for the retry-page 409 — see
 * docs/desktop-app.md, N10/N22) — callers that need to branch on a specific
 * error need this instead of pattern-matching `Error.message`. */
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    let detail: unknown;
    try {
      detail = JSON.parse(body)?.detail;
    } catch {
      /* not JSON — leave detail undefined, message below still carries the raw body */
    }
    throw new ApiError(res.status, `${init?.method ?? "GET"} ${path} → ${res.status}: ${body}`, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const realClient: ApiClient = {
  getSystem: () => req<SystemInfo>("/system"),

  inspect: (body: InspectRequest) => req<InspectResponse>("/inspect", { method: "POST", body: JSON.stringify(body) }),

  estimate: (body: EstimateRequest) => req<EstimateResponse>("/estimate", { method: "POST", body: JSON.stringify(body) }),

  getSettings: () => req<Settings>("/settings"),
  putSettings: (body: SettingsUpdateRequest) => req<Settings>("/settings", { method: "PUT", body: JSON.stringify(body) }),

  getPresets: () => req<Preset[]>("/presets"),
  putPresets: (body: PresetsUpdateRequest) => req<Preset[]>("/presets", { method: "PUT", body: JSON.stringify(body) }),
  patchPresets: (body: PresetsUpdateRequest) => req<Preset[]>("/presets", { method: "PATCH", body: JSON.stringify(body) }),
  deletePreset: (id: string) => req<Preset[]>(`/presets/${encodeURIComponent(id)}`, { method: "DELETE" }),

  getModels: () => req<ModelsResponse>("/models"),
  resolveModel: (body: ResolveModelRequest) => req<ResolveModelResponse>("/models/resolve", { method: "POST", body: JSON.stringify(body) }),

  getFolders: () => req<FolderInfo[]>("/folders"),
  forgetFolder: (path: string) => req<{ ok: boolean }>(`/folders?path=${encodeURIComponent(path)}`, { method: "DELETE" }),

  createJob: (body: JobCreateRequest) => req<JobCreateResponse>("/jobs", { method: "POST", body: JSON.stringify(body) }),
  getJob: (id: string) => req<Job>(`/jobs/${id}`),
  listJobs: (limit?: number) => req<Job[]>(`/jobs${limit != null ? `?limit=${limit}` : ""}`),
  pauseJob: (id: string) => req<Job>(`/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => req<Job>(`/jobs/${id}/resume`, { method: "POST" }),
  cancelJob: (id: string) => req<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  cancelFile: (id: string, fileIndex: number) => req<Job>(`/jobs/${id}/files/${fileIndex}/cancel`, { method: "POST" }),
  retryJob: (id: string, body: JobRetryRequest) => req<Job>(`/jobs/${id}/retry`, { method: "POST", body: JSON.stringify(body) }),

  subscribeJobEvents: (id: string, onEvent: (e: JobEvent) => void): JobEventUnsubscribe => {
    // A dropped connection must not silently freeze the live queue/semaphore
    // view: reconnect with backoff and say so, rather than leaving the UI
    // stuck on the last event it ever received.
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/api/jobs/${id}/events`;
    let closedByCaller = false;
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      ws = new WebSocket(url);
      ws.onmessage = (msg) => {
        try {
          onEvent(JSON.parse(msg.data));
        } catch {
          /* ignore malformed line */
        }
      };
      ws.onopen = () => {
        if (attempt > 0) {
          onEvent({ type: "log", level: "info", message: "Reconectado." });
        }
        attempt = 0;
      };
      ws.onclose = () => {
        if (closedByCaller) return;
        attempt += 1;
        const delay = Math.min(1000 * 2 ** (attempt - 1), 15000);
        onEvent({ type: "log", level: "warning", message: "Conexión perdida, reconectando…" });
        retryTimer = setTimeout(connect, delay);
      };
      ws.onerror = () => {
        // onclose always follows onerror for a WebSocket; the retry is
        // scheduled there, not duplicated here.
        ws?.close();
      };
    }

    connect();
    return () => {
      closedByCaller = true;
      if (retryTimer !== null) clearTimeout(retryTimer);
      ws?.close();
    };
  },

  getPage: (jobId: string, fileIndex: number, page: number) => req<PageDetail>(`/jobs/${jobId}/files/${fileIndex}/pages/${page}`),
  putPage: (jobId: string, fileIndex: number, page: number, body: PageUpdateRequest) =>
    req<PageDetail>(`/jobs/${jobId}/files/${fileIndex}/pages/${page}`, { method: "PUT", body: JSON.stringify(body) }),
  getOriginal: (jobId: string, fileIndex: number) => req<OriginalInfo>(`/jobs/${jobId}/files/${fileIndex}/original`),

  openPath: (body: OpenPathRequest) => req(`/open`, { method: "POST", body: JSON.stringify(body) }),
  revealPath: (body: OpenPathRequest) => req(`/reveal`, { method: "POST", body: JSON.stringify(body) }),

  getSyncs: () => req<Sync[]>("/syncs"),
  createSync: (body: SyncCreateRequest) => req<Sync>("/syncs", { method: "POST", body: JSON.stringify(body) }),
  patchSync: (id: string, body: SyncUpdateRequest) => req<Sync>(`/syncs/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSync: (id: string) => req<{ ok: boolean }>(`/syncs/${id}`, { method: "DELETE" }),

  manualPrice: (body: ManualPriceRequest) => req<ManualPriceRequest>("/prices/manual", { method: "POST", body: JSON.stringify(body) }),

  pickFolder: async () => {
    try {
      return await req<{ path: string | null }>("/pick-folder", { method: "POST" });
    } catch {
      return { path: null };
    }
  },

  pullOllamaModel: (model: string) =>
    req<{ ok: boolean; error?: string | null }>("/ollama/pull", { method: "POST", body: JSON.stringify({ model }) }),

  saveCloudKey: (provider: CloudProviderId, key: string) =>
    req(`/cloud/${provider}/key`, { method: "POST", body: JSON.stringify({ key }) }),
  removeCloudKey: (provider: CloudProviderId) => req(`/cloud/${provider}/key`, { method: "DELETE" }),
};
