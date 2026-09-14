/**
 * The API surface both client.ts (real server) and mockClient.ts (VITE_MOCK=1
 * fixtures) implement. Screens import `api` from ./index and never touch
 * client.ts/mockClient.ts directly, so swapping mock↔real is one flag.
 */
import type {
  CloudKeyRequest,
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
  ModelsResponse,
  OpenPathRequest,
  OpenPathResponse,
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

export type JobEventUnsubscribe = () => void;

export interface ApiClient {
  getSystem(): Promise<SystemInfo>;
  inspect(body: InspectRequest): Promise<InspectResponse>;
  estimate(body: EstimateRequest): Promise<EstimateResponse>;

  getSettings(): Promise<Settings>;
  putSettings(body: SettingsUpdateRequest): Promise<Settings>;

  getPresets(): Promise<Preset[]>;
  /** FULL replace — a preset that exists but isn't in the request body is
   * gone afterwards. Prefer `patchPresets` for saving/editing one preset;
   * this stays only for the one legitimate full-replace use case. */
  putPresets(body: PresetsUpdateRequest): Promise<Preset[]>;
  /** Merge/upsert by id — never deletes (C7). This is what every
   * save/edit-one-preset call site should use: every preset already stored
   * and not mentioned in the request survives untouched. */
  patchPresets(body: PresetsUpdateRequest): Promise<Preset[]>;
  /** Explicit removal of one user preset. 404s (surfaced as an ApiError)
   * for a builtin id or an id that was never a stored user preset. */
  deletePreset(id: string): Promise<Preset[]>;

  getModels(): Promise<ModelsResponse>;
  resolveModel(body: ResolveModelRequest): Promise<ResolveModelResponse>;

  getFolders(): Promise<FolderInfo[]>;
  forgetFolder(path: string): Promise<{ ok: boolean }>;

  createJob(body: JobCreateRequest): Promise<JobCreateResponse>;
  getJob(id: string): Promise<Job>;
  /** GET /api/jobs?limit= — every job, newest first (in-memory plus
   * persisted history). Historial's only endpoint. */
  listJobs(limit?: number): Promise<Job[]>;
  pauseJob(id: string): Promise<Job>;
  resumeJob(id: string): Promise<Job>;
  /** Stops the whole job after the file currently converting finishes its
   * current page — partial output for files already done is kept. */
  cancelJob(id: string): Promise<Job>;
  /** Stops one file without touching the rest of the job — queued: skipped
   * outright; running: stopped after the current page. */
  cancelFile(id: string, fileIndex: number): Promise<Job>;
  retryJob(id: string, body: JobRetryRequest): Promise<Job>;
  subscribeJobEvents(id: string, onEvent: (e: JobEvent) => void): JobEventUnsubscribe;

  getPage(jobId: string, fileIndex: number, page: number): Promise<PageDetail>;
  putPage(jobId: string, fileIndex: number, page: number, body: PageUpdateRequest): Promise<PageDetail>;
  /** GET /api/jobs/{id}/files/{i}/original — "ver original". */
  getOriginal(jobId: string, fileIndex: number): Promise<OriginalInfo>;

  /** POST /api/open — open a path with the OS default app. */
  openPath(body: OpenPathRequest): Promise<OpenPathResponse>;
  /** POST /api/reveal — reveal a path in Finder. */
  revealPath(body: OpenPathRequest): Promise<OpenPathResponse>;

  getSyncs(): Promise<Sync[]>;
  createSync(body: SyncCreateRequest): Promise<Sync>;
  patchSync(id: string, body: SyncUpdateRequest): Promise<Sync>;
  deleteSync(id: string): Promise<{ ok: boolean }>;

  manualPrice(body: ManualPriceRequest): Promise<ManualPriceRequest>;

  /** Not in the server contract table — a local-only convenience the
   * frontend defines so "Elegir…" can work before a native folder picker
   * exists. Falls back to a pasted path in the UI when this throws or
   * returns { path: null }. */
  pickFolder(): Promise<{ path: string | null }>;

  pullOllamaModel(model: string): Promise<{ ok: boolean; error?: string | null }>;
  /** POST /api/cloud/{provider}/key, DELETE /api/cloud/{provider}/key — every
   * provider in CloudProviderId. Never shows the env var name in the UI. */
  saveCloudKey(provider: CloudProviderId, key: string): Promise<CloudKeyResponse>;
  removeCloudKey(provider: CloudProviderId): Promise<CloudKeyResponse>;
}
