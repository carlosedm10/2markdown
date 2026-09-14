import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAppStore } from "../store/useAppStore";
import { api } from "../api";
import type { EstimateResponse, Job, JobEvent, JobFileState, Pipeline, Preset } from "../api/types";
import { engineLabel, fmtBytes, fmtTime, fmtUsd, kindLabelEs, plural, reasonLabel, stripModelProviderPrefixes } from "../lib/format";
import { handleRadioGroupKeyDown } from "../lib/a11y";
import { changedPresets } from "../lib/presets";
import SettingsSummary from "../components/SettingsSummary";
import ReviewDrawer from "../components/ReviewDrawer";
import RetryPopover from "../components/RetryPopover";

const STAGE_LABEL: Record<string, string> = {
  extract: "Extraer texto, tablas",
  ocr: "Leer páginas escaneadas",
  figures: "Describir figuras",
  review: "Revisar erratas",
  write: "Limpiar y escribir",
};

function modelLabel(model: string | null, kind: string): string {
  if (kind === "none" || !model) return "sin modelo";
  const name = model.split(":").slice(1).join(":") || model;
  return `${name} · ${kind === "cloud" ? "nube" : kind === "gpu" ? "local" : "CPU"}`;
}

function fileName(path: string): string {
  return path.split("/").pop() || path;
}

function pagesLabel(pages: number | null): string {
  // "—" for both "not paginated" (null) and the pre-convert inspector's "0
  // pages found yet" — never show "0 páginas" for either (shared by the
  // inspect list below and the running/done queue rows' <small>).
  return pages == null || pages === 0 ? "—" : plural(pages, "página");
}

function fmtSeconds(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

type Screen = "empty" | "inspected" | "running" | "done";

export default function Convert() {
  const navigate = useNavigate();
  const params = useParams<{ jobId?: string; fileIndex?: string }>();
  const mode = useAppStore((s) => s.mode);
  const setMode = useAppStore((s) => s.setMode);
  const inputPath = useAppStore((s) => s.inputPath);
  const setInputPath = useAppStore((s) => s.setInputPath);
  const inspect = useAppStore((s) => s.inspect);
  const setInspect = useAppStore((s) => s.setInspect);
  const presets = useAppStore((s) => s.presets);
  const selectedPresetId = useAppStore((s) => s.selectedPresetId);
  const setSelectedPresetId = useAppStore((s) => s.setSelectedPresetId);
  const startEditPreset = useAppStore((s) => s.startEditPreset);
  const outputPath = useAppStore((s) => s.outputPath);
  const setOutputPath = useAppStore((s) => s.setOutputPath);
  const currentJobId = useAppStore((s) => s.currentJobId);
  const setCurrentJobId = useAppStore((s) => s.setCurrentJobId);
  const resetJobEvents = useAppStore((s) => s.resetJobEvents);
  const settings = useAppStore((s) => s.settings);

  const jobId = params.jobId ?? currentJobId ?? undefined;

  const [dragOver, setDragOver] = useState(false);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [manualIn, setManualIn] = useState("");
  const [manualOut, setManualOut] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [creating, setCreating] = useState(false);

  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [gpu, setGpu] = useState<{ busy: boolean; who: string; waiting: number }>({ busy: false, who: "", waiting: 0 });
  const [cloud, setCloud] = useState<{ permits: number; inUse: number }>({ permits: 4, inUse: 0 });
  const [costSoFar, setCostSoFar] = useState(0);
  const [paused, setPaused] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [currentPageByFile, setCurrentPageByFile] = useState<Record<number, number>>({});
  const [currentEngineByFile, setCurrentEngineByFile] = useState<Record<number, string | null>>({});
  const [retryPopoverIndex, setRetryPopoverIndex] = useState<number | null>(null);
  const [cancelling, setCancelling] = useState<Set<number>>(new Set());
  const [cancelAllOpen, setCancelAllOpen] = useState(false);
  // The server marks queued files cancelled only once the in-flight page
  // finishes (~40-80s), so `job` stays fully "running" for a while after the
  // confirm — this is purely an optimistic client-side flag so the footer
  // and rows acknowledge the click instead of looking unchanged in the
  // meantime (flow-issues.md C9). Cleared once job_done actually lands.
  const [cancellingAll, setCancellingAll] = useState(false);
  const startedAt = useRef(Date.now());
  // N6: the manual-path box and the "Elegir otra carpeta…" button that
  // replaces it once `inspect` resolves used to sit at the same tree
  // position (both the first interactive control inside `.drop .head`),
  // and runInspect() had no in-flight guard — a click that landed on
  // whichever control was there when the async inspect() resolved (e.g. a
  // stray/duplicate event during the empty→inspected DOM swap) could fire
  // "Elegir otra carpeta…"'s onClick and immediately setInspect(null),
  // wiping the just-fetched result before it was ever shown, with no
  // visible error and no way to retry (focus was gone, so a second Enter
  // reached nothing). `inspecting` disables the box while a request is in
  // flight so the box can't be resubmitted, `inspectError` surfaces a
  // rejected promise that the old bare `e.key === "Enter" && runInspect(...)`
  // silently swallowed, and the two branches below now carry distinct
  // `key`s so React always fully unmounts the old control instead of
  // patching a new one in at the same DOM position.
  const [inspecting, setInspecting] = useState(false);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const inspectInFlight = useRef(false);

  const reviewFileIndex = params.fileIndex != null ? Number(params.fileIndex) : null;

  const selectedPreset = presets.find((p) => p.id === selectedPresetId) ?? presets[0];
  const pipeline = selectedPreset?.pipeline;

  async function runInspect(path: string) {
    if (!path || inspectInFlight.current) return;
    inspectInFlight.current = true;
    setInspecting(true);
    setInspectError(null);
    try {
      const r = await api.inspect({ path });
      setInspect(r);
      setOutputPath(r.default_output ?? `${path.replace(/\/+$/, "")}_2markdown`);
      if (r.last_preset_id && presets.some((p) => p.id === r.last_preset_id)) {
        setSelectedPresetId(r.last_preset_id);
      } else if (settings && presets.some((p) => p.id === settings.default_preset_id)) {
        setSelectedPresetId(settings.default_preset_id);
      }
    } catch (err) {
      setInspectError(err instanceof Error ? err.message : "No se pudo leer la carpeta.");
    } finally {
      inspectInFlight.current = false;
      setInspecting(false);
    }
  }

  // Single submit path for both the "o pega una ruta" box's Enter key and
  // (were one ever added) a submit button — see the N6 note above.
  function submitInputPath(e?: React.FormEvent) {
    e?.preventDefault();
    void runInspect(inputPath);
  }

  useEffect(() => {
    if (!jobId && inputPath && !inspect) {
      runInspect(inputPath);
    } else if (!jobId && inspect) {
      // N16: this screen can remount with `inspect` already sitting in the
      // store (e.g. back from Pipeline after editing/creating a preset)
      // while `selectedPresetId` drifted to whatever that other screen last
      // set — the folder-memory badge ("usado la última vez con esta
      // carpeta") still points at `inspect.last_preset_id`, so re-sync the
      // selection to match on mount, exactly like `runInspect` does for a
      // fresh inspect.
      if (inspect.last_preset_id && presets.some((p) => p.id === inspect.last_preset_id)) {
        setSelectedPresetId(inspect.last_preset_id);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!inspect || !pipeline || jobId) return;
    const t = setTimeout(() => {
      api.estimate({ inspect_id: inspect.inspect_id, pipeline }).then(setEstimate);
    }, 250);
    return () => clearTimeout(t);
  }, [inspect, pipeline, jobId]);

  // --- Job polling + live events -----------------------------------------
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    api.getJob(jobId).then(setJob);
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    const unsub = api.subscribeJobEvents(jobId, (e) => applyEvent(e));
    return unsub;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // WS events drive progress live, but a few facts only exist server-side
  // once a file/job actually finishes (output_path, pages, kind, engine_used,
  // review_changes_count, a non-token reason) — file_started/page_done never
  // carry them. Re-fetch the job and merge those fields in by index so
  // "[Abrir .md]"/"[Mostrar en Finder]" and "0 páginas" don't wait for a full
  // reload; server facts win over anything the WS stream guessed locally.
  async function mergeFromServer(id: string) {
    const fresh = await api.getJob(id);
    setJob((prev) => {
      if (!prev) return fresh;
      const byIndex = new Map(fresh.files.map((f) => [f.index, f]));
      return {
        ...prev,
        ...fresh,
        files: prev.files.map((f) => {
          const s = byIndex.get(f.index);
          if (!s) return f;
          return {
            ...f,
            output_path: s.output_path,
            pages: s.pages,
            kind: s.kind,
            engine_used: s.engine_used,
            review_changes_count: s.review_changes_count,
            reason: s.reason,
          };
        }),
      };
    });
  }

  function applyEvent(e: JobEvent) {
    switch (e.type) {
      case "file_started":
        setJob((prev) =>
          prev
            ? {
                ...prev,
                files: prev.files.map((f) => (f.index === e.index ? { ...f, status: "running", pages: e.pages } : f)),
              }
            : prev
        );
        setCurrentPageByFile((prev) => ({ ...prev, [e.index]: 1 }));
        setCurrentEngineByFile((prev) => ({ ...prev, [e.index]: null }));
        break;
      case "page_started":
        setCurrentPageByFile((prev) => ({ ...prev, [e.index]: e.page }));
        setCurrentEngineByFile((prev) => ({ ...prev, [e.index]: e.engine }));
        break;
      case "page_done":
        setCurrentPageByFile((prev) => ({ ...prev, [e.index]: e.page }));
        setJob((prev) =>
          prev
            ? {
                ...prev,
                files: prev.files.map((f) =>
                  f.index === e.index ? { ...f, review_changes_count: f.review_changes_count + e.review_changes.length } : f
                ),
              }
            : prev
        );
        break;
      case "file_done":
        setJob((prev) =>
          prev
            ? { ...prev, files: prev.files.map((f) => (f.index === e.index ? { ...f, status: e.status, seconds: e.seconds, reason: e.reason ?? null } : f)) }
            : prev
        );
        if (jobId) void mergeFromServer(jobId);
        setCancelling((prev) => {
          if (!prev.has(e.index)) return prev;
          const next = new Set(prev);
          next.delete(e.index);
          return next;
        });
        break;
      case "semaphores":
        setGpu({
          busy: e.gpu.held_by.length > 0,
          who: e.gpu.held_by[0] ? `${e.gpu.held_by[0].file} · pág. ${e.gpu.held_by[0].page ?? "-"} · ${e.gpu.held_by[0].stage}` : "",
          waiting: e.gpu.waiting,
        });
        setCloud({ permits: e.cloud.permits, inUse: e.cloud.in_use });
        break;
      case "cost":
        setCostSoFar(e.usd_so_far);
        break;
      case "log":
        setLogs((prev) => [...prev, `${(e.ts ? new Date(e.ts) : new Date()).toLocaleTimeString()} ${e.message}`]);
        break;
      case "job_done":
        setCostSoFar(e.usd ?? 0);
        setJob((prev) =>
          prev ? { ...prev, status: "done", ok: e.ok, warn: e.warn, failed: e.failed, cancelled: e.cancelled ?? 0, seconds: e.seconds, usd: e.usd } : prev
        );
        if (jobId) void mergeFromServer(jobId);
        setCancellingAll(false);
        break;
      default:
        break;
    }
  }

  const unknownPriceStage = estimate?.stages.find((s) => s.unknown_price);
  const cloudAlt = useMemo(() => estimate?.alternatives?.[0], [estimate]);

  async function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0] as (File & { path?: string }) | undefined;
    const path = file?.path || file?.name;
    if (path) {
      setInputPath(path);
      await runInspect(path);
    }
  }

  async function pickFolder() {
    const r = await api.pickFolder();
    if (r.path) {
      setInputPath(r.path);
      await runInspect(r.path);
    }
  }

  function editPreset() {
    if (!pipeline) return;
    startEditPreset(selectedPresetId, pipeline);
    navigate("/pipeline");
  }

  function newPreset() {
    if (!pipeline) return;
    startEditPreset(null, pipeline);
    navigate("/pipeline");
  }

  async function addManualPrice() {
    if (!unknownPriceStage?.model) return;
    await api.manualPrice({
      model: unknownPriceStage.model,
      input_per_mtok: Number(manualIn) || 0,
      output_per_mtok: Number(manualOut) || 0,
    });
    if (inspect && pipeline) {
      const r = await api.estimate({ inspect_id: inspect.inspect_id, pipeline });
      setEstimate(r);
    }
  }

  async function startConversion() {
    if (!pipeline) return;
    setCreating(true);
    try {
      if (mode === "sync") {
        await api.createSync({ input: inputPath, output: outputPath, preset_id: selectedPresetId, enabled: true });
        navigate("/ajustes");
        return;
      }
      const created = await api.createJob({
        inspect_id: inspect?.inspect_id,
        output: outputPath,
        preset_id: selectedPresetId,
        mode: "once",
      });
      resetJobEvents();
      setLogs([]);
      startedAt.current = Date.now();
      setCurrentJobId(created.job_id);
      navigate(`/convertir/${created.job_id}`);
    } finally {
      setCreating(false);
    }
  }

  function convertAnotherFolder() {
    setInspect(null);
    setInputPath("");
    setCurrentJobId(null);
    setJob(null);
    setEstimate(null);
    setInspecting(false);
    setInspectError(null);
    inspectInFlight.current = false;
    resetJobEvents();
    navigate("/convertir");
  }

  async function retryFile(index: number) {
    if (!jobId) return;
    const updated = await api.retryJob(jobId, { file_index: index, pipeline_patch: null });
    setJob(updated);
  }

  async function removeQueuedFile(index: number) {
    if (!jobId) return;
    setCancelling((prev) => new Set(prev).add(index));
    try {
      const updated = await api.cancelFile(jobId, index);
      setJob(updated);
      // A queued file is dropped immediately, but a running one only stops
      // after the page it's mid-read on finishes (~40-80s) — the request
      // resolving just means the server accepted the ask. Clearing
      // `cancelling` here for a still-running file undoes the "cancelando
      // tras esta página" indicator the instant the click's own round-trip
      // finishes, well before the file actually stops (flow-issues.md C8).
      // Only clear it now if the server already reports it not running;
      // otherwise applyEvent's `file_done` clears it once it truly stops.
      const stillRunning = updated.files.find((f) => f.index === index)?.status === "running";
      if (!stillRunning) {
        setCancelling((prev) => {
          const next = new Set(prev);
          next.delete(index);
          return next;
        });
      }
    } catch (err) {
      setCancelling((prev) => {
        const next = new Set(prev);
        next.delete(index);
        return next;
      });
      throw err;
    }
  }

  async function togglePause() {
    if (!jobId) return;
    if (paused) await api.resumeJob(jobId);
    else await api.pauseJob(jobId);
    setPaused((p) => !p);
  }

  async function cancelAll() {
    if (!jobId) return;
    setCancelAllOpen(false);
    setCancellingAll(true);
    const updated = await api.cancelJob(jobId);
    setJob(updated);
    // If the server already reflects the cancel (nothing left running),
    // clear the optimistic flag now; otherwise leave it — applyEvent's
    // job_done clears it once the in-flight page actually finishes.
    if (updated.status !== "running") setCancellingAll(false);
  }

  async function openOutputFolder() {
    if (job?.output) await api.openPath({ path: job.output });
  }

  async function openFileOutput(f: JobFileState) {
    if (f.output_path) await api.openPath({ path: f.output_path });
  }

  async function revealFile(f: JobFileState) {
    if (f.output_path) await api.revealPath({ path: f.output_path });
  }

  // --- Screen selection ---------------------------------------------------
  const screen: Screen = jobId
    ? job && (job.status === "done" || job.status === "failed" || job.status === "cancelled")
      ? "done"
      : "running"
    : inspect
    ? "inspected"
    : "empty";

  if (screen === "running" || screen === "done") {
    return (
      <>
        <RunningOrDone
          job={job}
          screen={screen}
          gpu={gpu}
          cloud={cloud}
          costSoFar={costSoFar}
          paused={paused}
          logs={logs}
          logsOpen={logsOpen}
          setLogsOpen={setLogsOpen}
          currentPageByFile={currentPageByFile}
          currentEngineByFile={currentEngineByFile}
          startedAt={startedAt.current}
          cancelling={cancelling}
          cancellingAll={cancellingAll}
          cancelAllOpen={cancelAllOpen}
          setCancelAllOpen={setCancelAllOpen}
          onTogglePause={togglePause}
          onCancelAll={cancelAll}
          onRemoveQueued={removeQueuedFile}
          onCancelRunning={removeQueuedFile}
          onRetry={retryFile}
          onOpenOutputFolder={openOutputFolder}
          onOpenFile={openFileOutput}
          onRevealFile={revealFile}
          onReview={(i) => jobId && navigate(`/convertir/${jobId}/${i}`)}
          onRetryPopover={setRetryPopoverIndex}
          onConvertAnother={convertAnotherFolder}
        />
        {/* Mounted alongside the running/done queue (not only in the
            empty/inspected branch below) so "Volver a convertir con… ▾" works
            once a job exists — see flow-issues.md B1. */}
        {retryPopoverIndex != null && jobId && (
          <RetryPopover jobId={jobId} fileIndex={retryPopoverIndex} onClose={() => setRetryPopoverIndex(null)} onDone={(j) => setJob(j)} />
        )}
        {/* Overlay drawer over the running/done file list, not a full-screen
            replace — see flow-issues.md M1. ReviewDrawer renders its own
            .drawer-overlay/.drawer, so the queue underneath stays mounted. */}
        {reviewFileIndex != null && jobId && (
          <ReviewDrawer jobId={jobId} fileIndex={reviewFileIndex} onClose={() => navigate(`/convertir/${jobId}`)} />
        )}
      </>
    );
  }

  return (
    <div>
      <div className="mainhead">
        <div>
          <div className="eyebrow">Convertir</div>
          <h2>{inspect ? fileName(inputPath) || "Carpeta" : "Elige qué convertir"}</h2>
        </div>
      </div>

      {settings && <SettingsSummary settings={settings} presets={presets} activePresetId={selectedPresetId} />}

      <div className="conv">
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div className="mode">
              <button className={mode === "once" ? "on" : ""} onClick={() => setMode("once")}>
                Una vez
              </button>
              <button className={mode === "sync" ? "on" : ""} onClick={() => setMode("sync")}>
                Mantener sincronizada
              </button>
            </div>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {mode === "sync" ? "Sincronizar vigila la carpeta y convierte lo nuevo" : ""}
            </span>
          </div>

          <div
            className={"drop" + (dragOver ? " drag" : "")}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            {inspect ? (
              // N6: distinct `key` so this never patches over the empty
              // branch's DOM in place — always a full unmount/mount across
              // the empty↔inspected swap (see the guard note above).
              <div key="inspected" style={{ display: "contents" }}>
                <div className="head">
                  <div>
                    <b>{fileName(inputPath)}</b>
                    <span style={{ marginLeft: 8 }}>
                      {plural(inspect.totals.files, "archivo")} · {plural(inspect.totals.pages, "página")} ·{" "}
                      {plural(inspect.totals.scanned_pages, "escaneada")} · {fmtBytes(inspect.totals.bytes)}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="btn ghost small"
                    onClick={(e) => {
                      e.stopPropagation();
                      setInspect(null);
                      setInspectError(null);
                    }}
                  >
                    Elegir otra carpeta…
                  </button>
                </div>
                <div className="files">
                  {inspect.files.slice(0, 6).map((f) => (
                    <div className="f" key={f.path}>
                      <i className="ico" />
                      <span>{fileName(f.path)}</span>
                      <span className="meta">
                        {f.kind === "office" ? "texto · sin OCR" : `${pagesLabel(f.pages)} · ${kindLabelEs(f.kind)}`}
                      </span>
                    </div>
                  ))}
                </div>
                {inspect.files.length > 6 && (
                  <div className="more">y {inspect.files.length - 6} más · suelta más archivos o carpetas aquí</div>
                )}
              </div>
            ) : (
              <div key="empty" style={{ display: "contents" }}>
                <div className="head">
                  <b>Suelta una carpeta aquí</b>
                  <span>o pega una ruta</span>
                </div>
                <form
                  className="pickrow"
                  onSubmit={submitInputPath}
                  style={{ display: "flex", gap: 8 }}
                >
                  <input
                    type="text"
                    placeholder="/ruta/a/tu/carpeta"
                    value={inputPath}
                    disabled={inspecting}
                    onChange={(e) => {
                      setInputPath(e.target.value);
                      if (inspectError) setInspectError(null);
                    }}
                  />
                  <button type="button" className="btn" onClick={pickFolder} disabled={inspecting}>
                    Elegir…
                  </button>
                </form>
                {inspecting && <div className="more">Inspeccionando…</div>}
                {inspectError && (
                  <div className="more" style={{ color: "var(--bad, #c0392b)" }}>
                    {inspectError}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="out">
            <div style={{ flex: 1 }}>
              <span className="k">Salida</span>
              <input
                type="text"
                className="path"
                style={{ width: "100%", border: "none", background: "transparent", font: "inherit" }}
                value={outputPath}
                onChange={(e) => setOutputPath(e.target.value)}
                placeholder="/ruta/de/salida"
              />
            </div>
            <button
              className="btn ghost small"
              onClick={async () => {
                const r = await api.pickFolder();
                if (r.path) setOutputPath(r.path);
              }}
            >
              Cambiar…
            </button>
          </div>
        </div>

        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
            <span className="eyebrow">Preajuste</span>
            <button
              type="button"
              className="linklike"
              style={{ fontSize: 12, color: "var(--muted)", cursor: "pointer", background: "none", border: "none", padding: 0 }}
              onClick={editPreset}
            >
              Editar ▸
            </button>
          </div>
          <div className="presets" role="radiogroup" aria-label="Preajuste">
            {presets.map((p) => (
              <div
                key={p.id}
                role="radio"
                aria-checked={p.id === selectedPresetId}
                data-value={p.id}
                tabIndex={p.id === selectedPresetId ? 0 : -1}
                className={"preset" + (p.id === selectedPresetId ? " sel" : "")}
                onClick={() => setSelectedPresetId(p.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedPresetId(p.id);
                    return;
                  }
                  handleRadioGroupKeyDown(e, setSelectedPresetId);
                }}
              >
                <i className="radio" />
                <div>
                  <b>{p.name}</b>
                  <span>
                    {presetBlurb(p.pipeline)}
                    {inspect?.last_preset_id === p.id && (
                      <span className="pill mute" style={{ marginLeft: 6 }}>
                        usado la última vez con esta carpeta
                      </span>
                    )}
                  </span>
                </div>
                <button
                  type="button"
                  className="edit"
                  style={{ background: "none", border: "none" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedPresetId(p.id);
                    startEditPreset(p.id, p.pipeline);
                    navigate("/pipeline");
                  }}
                >
                  Editar
                </button>
              </div>
            ))}
            <button type="button" className="preset" style={{ borderStyle: "dashed", textAlign: "left" }} onClick={newPreset}>
              <i className="radio" style={{ borderStyle: "dashed" }} />
              <div>
                <b style={{ color: "var(--muted)" }}>Nuevo preajuste…</b>
                <span>Parte de uno existente y cámbiale los modelos</span>
              </div>
              <span />
            </button>
          </div>

          <div className="est">
            <div className="hd">
              <span>Antes de convertir</span>
              <span>estimación</span>
            </div>
            {estimate?.blocked ? (
              <div className="block" style={{ margin: 12 }}>
                <b>{estimate.blocked.title}</b>
                <span>{estimate.blocked.body}</span>
              </div>
            ) : (
              <>
                {estimate?.warning && (
                  <div className="block warnblock" style={{ margin: 12 }}>
                    <b>{estimate.warning.title}</b>
                    <span>{estimate.warning.body}</span>
                  </div>
                )}
                <div className="big">
                  <div>
                    <div className="n">{estimate ? fmtTime(estimate.total_seconds) : "…"}</div>
                    <div className="l">
                      {estimate ? `${estimate.parallel_files} archivo${estimate.parallel_files === 1 ? "" : "s"} a la vez` : ""}
                    </div>
                  </div>
                  <div>
                    <div className="n">{estimate ? (estimate.total_usd == null ? "?" : fmtUsd(estimate.total_usd)) : "…"}</div>
                    <div className="l">coste en nube</div>
                  </div>
                </div>
                <div className="rows">
                  {estimate?.stages.map((s) => (
                    <div className="r-wrap" key={s.key}>
                      <div className="r">
                        <span>
                          {STAGE_LABEL[s.key]}
                          {s.key === "figures" && !!s.note && (
                            <span className="pill warn" style={{ marginLeft: 6 }}>
                              sustituido
                            </span>
                          )}
                        </span>
                        <span className="m">{modelLabel(s.model, s.kind)}</span>
                        <span>
                          {fmtTime(s.seconds)} · {s.unknown_price ? "?" : fmtUsd(s.usd ?? 0)}
                        </span>
                      </div>
                      {s.note && (
                        <div className="fn-note" style={{ padding: "0 12px 8px" }}>
                          {stripModelProviderPrefixes(s.note)}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
                {cloudAlt && pipeline && (
                  <button
                    type="button"
                    className="alt"
                    style={{ background: "none", border: "none", textAlign: "left", width: "100%", cursor: "pointer" }}
                    onClick={() => {
                      startEditPreset(selectedPresetId, { ...pipeline, ...cloudAlt.pipeline_patch });
                      navigate("/pipeline");
                    }}
                  >
                    {cloudAlt.label}: <b>{fmtTime(cloudAlt.total_seconds)} · {cloudAlt.total_usd == null ? "?" : fmtUsd(cloudAlt.total_usd)}</b>
                  </button>
                )}
                <div className="alt" style={{ borderTop: "1px solid var(--line-soft)" }}>
                  Precios en USD de <code>genai-prices</code> · tokens por página estimados por píxeles y texto
                </div>
              </>
            )}
          </div>

          {unknownPriceStage && (
            <div className="block warnblock">
              <b>Precio del modelo desconocido</b>
              <span>
                genai-prices aún no tiene tabla para «{unknownPriceStage.model}». El tiempo se estima igual; el coste no. Si
                conoces el precio, añádelo y la app lo usará hasta que llegue el oficial.
              </span>
              <div className="addprice">
                <label>
                  Entrada
                  <input placeholder="$ / M tokens" value={manualIn} onChange={(e) => setManualIn(e.target.value)} />
                </label>
                <label>
                  Salida
                  <input placeholder="$ / M tokens" value={manualOut} onChange={(e) => setManualOut(e.target.value)} />
                </label>
                <button className="btn small" onClick={addManualPrice}>
                  Añadir precios
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="cta">
          <button type="button" className="adv" style={{ background: "none", border: "none" }} onClick={() => setShowAdvanced((v) => !v)}>
            Ajustes avanzados ▾
          </button>
          <button className="btn hl" disabled={!inspect || !!estimate?.blocked || creating} onClick={startConversion}>
            {creating
              ? "…"
              : mode === "sync"
              ? "Empezar a vigilar →"
              : inspect
              ? `Convertir ${plural(inspect.totals.files, "archivo")} →`
              : "Convertir →"}
          </button>
        </div>

        {showAdvanced && pipeline && (
          <div className="card" style={{ gridColumn: "1/-1" }}>
            <h4 style={{ marginBottom: 10 }}>Ajustes avanzados</h4>
            <AdvancedFields pipeline={pipeline} presetId={selectedPresetId} presets={presets} />
          </div>
        )}
      </div>

      {retryPopoverIndex != null && jobId && (
        <RetryPopover jobId={jobId} fileIndex={retryPopoverIndex} onClose={() => setRetryPopoverIndex(null)} onDone={(j) => setJob(j)} />
      )}
    </div>
  );
}

function presetBlurb(pipeline: Pipeline): string {
  if (pipeline.ocr_model === "tesseract" && !pipeline.describe_figures && !pipeline.review_model) {
    return `Texto impreso. Sin IA, ${pipeline.workers} a la vez.`;
  }
  const readWith = pipeline.ocr_model.startsWith("ollama:")
    ? "IA local para leer"
    : pipeline.ocr_model.startsWith("openai:")
    ? "IA en la nube para leer"
    : "Tesseract para leer";
  const reviewWith = !pipeline.review_model
    ? "sin revisión de erratas"
    : pipeline.review_model.startsWith("openai:")
    ? "nube para las erratas"
    : "IA local para las erratas";
  return `${readWith}, ${reviewWith}.`;
}

function AdvancedFields({
  pipeline,
  presetId,
  presets,
}: {
  pipeline: Pipeline;
  presetId: string;
  presets: Preset[];
}) {
  const setPresets = useAppStore((s) => s.setPresets);
  const [emitChunks, setEmitChunks] = useState(pipeline.emit_chunks);
  const [dpi, setDpi] = useState(pipeline.ocr_dpi);

  async function save() {
    const next = presets.map((p) => (p.id === presetId ? { ...p, pipeline: { ...p.pipeline, emit_chunks: emitChunks, ocr_dpi: dpi } } : p));
    // PATCH /api/presets upserts by id and never deletes (flow-issues.md
    // C7) — only the preset that actually changed needs to be sent.
    // `changedPresets` decides whether a PATCH is needed at all (N12). The
    // store is then refreshed from GET /api/presets.
    const changed = changedPresets(presets, next);
    if (changed.length === 0) return;
    await api.patchPresets({ presets: changed });
    setPresets(await api.getPresets());
  }

  return (
    <div style={{ display: "grid", gap: 10, fontSize: 13 }}>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input type="checkbox" checked={emitChunks} onChange={(e) => setEmitChunks(e.target.checked)} />
        Emitir <code>.chunks.json</code> (avanzado; no es un preajuste)
      </label>
      <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
        DPI de OCR
        <input type="number" value={dpi} onChange={(e) => setDpi(Number(e.target.value))} style={{ width: 90 }} />
      </label>
      <div>
        <button className="btn small" onClick={save}>
          Guardar
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// States (c) running and (d) done — the same file list becomes the queue,
// then the results, without ever navigating away from /convertir.
// ---------------------------------------------------------------------------

function RunningOrDone({
  job,
  screen,
  gpu,
  cloud,
  costSoFar,
  paused,
  logs,
  logsOpen,
  setLogsOpen,
  currentPageByFile,
  currentEngineByFile,
  startedAt,
  cancelling,
  cancellingAll,
  cancelAllOpen,
  setCancelAllOpen,
  onTogglePause,
  onCancelAll,
  onRemoveQueued,
  onCancelRunning,
  onRetry,
  onOpenOutputFolder,
  onOpenFile,
  onRevealFile,
  onReview,
  onRetryPopover,
  onConvertAnother,
}: {
  job: Job | null;
  screen: "running" | "done";
  gpu: { busy: boolean; who: string; waiting: number };
  cloud: { permits: number; inUse: number };
  costSoFar: number;
  paused: boolean;
  logs: string[];
  logsOpen: boolean;
  setLogsOpen: (v: boolean | ((p: boolean) => boolean)) => void;
  currentPageByFile: Record<number, number>;
  currentEngineByFile: Record<number, string | null>;
  startedAt: number;
  cancelling: Set<number>;
  cancellingAll: boolean;
  cancelAllOpen: boolean;
  setCancelAllOpen: (v: boolean) => void;
  onTogglePause: () => void;
  onCancelAll: () => void;
  onRemoveQueued: (i: number) => void;
  onCancelRunning: (i: number) => void;
  onRetry: (i: number) => void;
  onOpenOutputFolder: () => void;
  onOpenFile: (f: JobFileState) => void;
  onRevealFile: (f: JobFileState) => void;
  onReview: (i: number) => void;
  onRetryPopover: (i: number) => void;
  onConvertAnother: () => void;
}) {
  // The done-state CTA renders at almost exactly the y-coordinate a queue
  // row's own "✕ Quitar"/"✕ Cancelar" occupied a moment earlier — a click
  // aimed at the row, timed across the running→done transition, used to
  // land on this button instead and silently leave the results screen with
  // no undo (flow-issues.md N4). Keeping it inert for ~600 ms after that
  // transition means an in-flight click from the old layout can't be
  // captured by it; the visual separator below (see the "done" render)
  // keeps it from reading as another row's action in the first place.
  const [ctaInert, setCtaInert] = useState(true);
  useEffect(() => {
    if (screen !== "done") {
      setCtaInert(true);
      return;
    }
    const t = setTimeout(() => setCtaInert(false), 600);
    return () => clearTimeout(t);
  }, [screen]);

  if (!job) {
    return (
      <div>
        <div className="mainhead">
          <div>
            <div className="eyebrow">Convertir</div>
            <h2>Cargando…</h2>
          </div>
        </div>
      </div>
    );
  }

  const files = job.files;
  const total = files.length;
  const finished = files.filter((f) => f.status === "ok" || f.status === "warn" || f.status === "failed" || f.status === "cancelled").length;
  const pct = total ? Math.round((finished / total) * 100) : 0;
  const elapsedMin = Math.round((Date.now() - startedAt) / 60000);
  const finishedSeconds = files.filter((f) => f.seconds != null).map((f) => f.seconds as number);
  const avgSecondsPerFile = finishedSeconds.length ? finishedSeconds.reduce((a, b) => a + b, 0) / finishedSeconds.length : null;
  const remaining = Math.max(0, total - finished);
  const etaMin = avgSecondsPerFile != null ? Math.max(1, Math.round((avgSecondsPerFile * remaining) / 60)) : null;

  return (
    <div>
      <div className="mainhead">
        <div>
          <div className="eyebrow">Convertir</div>
          <h2>{fileName(job.output)}</h2>
        </div>
      </div>

      {screen === "running" ? (
        <>
          <div className="qhead">
            <div className="big">
              {finished} de {total} archivos
            </div>
            <div className="eta">
              {remaining === 0
                ? `lleva ${elapsedMin} min`
                : etaMin != null
                ? `~${etaMin} min restantes · lleva ${elapsedMin} min`
                : `calculando… · lleva ${elapsedMin} min`}
            </div>
          </div>
          <div className="qbar">
            <i style={{ width: pct + "%" }} />
          </div>

          <div className="live">
            <div className={"lv" + (gpu.busy ? " hot" : " idle")}>
              <span className="who">GPU</span>
              <span className="sem">
                <i className={gpu.busy ? "busy" : "free"} />
              </span>
              <span className="what">{gpu.busy ? gpu.who : "sin uso"}</span>
              <span className="wait">{gpu.waiting > 0 ? `${gpu.waiting} archivos esperan` : ""}</span>
            </div>
            <div className={"lv" + (cloud.inUse > 0 ? " hot" : " idle")}>
              <span className="who">Nube</span>
              <span className="sem">
                {Array.from({ length: cloud.permits }).map((_, i) => (
                  <i key={i} className={i < cloud.inUse ? "busy" : "free"} />
                ))}
              </span>
              <span className="what">{cloud.inUse > 0 ? "revisando / describiendo" : "sin uso"}</span>
              <span className="wait">{fmtUsd(costSoFar)} hasta ahora</span>
            </div>
            <div className="lv idle">
              <span className="who">Sin modelo</span>
              <span className="sem">
                <i className="free" />
              </span>
              <span className="what">extraer, limpiar</span>
              <span className="wait" />
            </div>
          </div>

          <div className="qfoot" style={{ marginBottom: 8 }}>
            <span>{cancellingAll ? "Cancelando… se detiene tras la página en curso" : ""}</span>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn ghost" onClick={onTogglePause} disabled={cancellingAll}>
                {paused ? "Reanudar" : "Pausar"}
              </button>
              <button className="btn ghost" onClick={() => setCancelAllOpen(true)} disabled={cancellingAll}>
                {cancellingAll ? "Cancelando…" : "Cancelar todo"}
              </button>
              <button className="btn" onClick={onOpenOutputFolder}>
                Abrir carpeta
              </button>
            </div>
          </div>
        </>
      ) : (
        <div className="qhead" style={{ marginBottom: 12 }}>
          <div className="big">
            {job.ok} ✓ · {job.warn} ! · {job.failed} ✕{job.cancelled ? ` · ${job.cancelled} cancelado(s)` : ""}
          </div>
          <div className="eta">
            {job.seconds != null ? fmtSeconds(job.seconds) : ""} · {fmtUsd(job.usd ?? 0)}
          </div>
        </div>
      )}

      <div className="queue">
        {files.map((f) => (
          <FileRow
            key={f.index}
            file={f}
            screen={screen}
            currentPage={currentPageByFile[f.index] ?? null}
            currentEngine={currentEngineByFile[f.index] ?? null}
            cancelling={cancelling.has(f.index) || (cancellingAll && (f.status === "running" || f.status === "pending"))}
            onRemoveQueued={() => onRemoveQueued(f.index)}
            onCancelRunning={() => onCancelRunning(f.index)}
            onRetry={() => onRetry(f.index)}
            onOpenFile={() => onOpenFile(f)}
            onRevealFile={() => onRevealFile(f)}
            onReview={() => onReview(f.index)}
            onRetryPopover={() => onRetryPopover(f.index)}
          />
        ))}
      </div>

      {/* N7: running and done render the SAME reserved footer band (the
          "qfoot-reserve" min-height below), and the done-state CTA sits at
          the LEFT edge — the queue rows' own actions are right-aligned
          (`.q .actions { justify-content: flex-end }`), so this button can
          never land on the x-coordinates a "✕ Quitar"/"✕ Cancelar" occupied
          a moment earlier, whichever y the transition happens to catch it
          at. The 600ms `ctaInert` window (above) stays as a second layer
          for an in-flight click from the instant of the transition itself. */}
      {screen === "running" && (
        <div className="qfoot qfoot-reserve">
          <div>
            <button
              type="button"
              className="linklike"
              aria-expanded={logsOpen}
              style={{ background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }}
              onClick={() => setLogsOpen((v) => !v)}
            >
              {logsOpen ? "▾" : "▸"} Detalles técnicos ({logs.length})
            </button>
            {logsOpen && <pre>{logs.join("\n") || "Sin eventos todavía."}</pre>}
          </div>
        </div>
      )}

      {screen === "done" && (
        <div className="qfoot qfoot-reserve cta-sep" style={{ justifyContent: "flex-start" }}>
          <button className="btn hl" disabled={ctaInert} onClick={onConvertAnother}>
            Convertir otra carpeta
          </button>
        </div>
      )}

      {cancelAllOpen && (
        <div className="overlay" onClick={() => setCancelAllOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>¿Cancelar todo el trabajo?</h3>
            <p style={{ fontSize: 13, color: "var(--ink-2)" }}>
              Se detiene tras la página que esté leyendo ahora mismo. Lo que ya se ha convertido se conserva; no se pierde
              nada de lo que ya está listo.
            </p>
            <div className="actions">
              <button className="btn ghost" onClick={() => setCancelAllOpen(false)}>
                Seguir convirtiendo
              </button>
              <button className="btn primary" onClick={onCancelAll}>
                Cancelar todo
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FileRow({
  file,
  screen,
  currentPage,
  currentEngine,
  cancelling,
  onRemoveQueued,
  onCancelRunning,
  onRetry,
  onOpenFile,
  onRevealFile,
  onReview,
  onRetryPopover,
}: {
  file: JobFileState;
  screen: "running" | "done";
  currentPage: number | null;
  currentEngine: string | null;
  cancelling: boolean;
  onRemoveQueued: () => void;
  onCancelRunning: () => void;
  onRetry: () => void;
  onOpenFile: () => void;
  onRevealFile: () => void;
  onReview: () => void;
  onRetryPopover: () => void;
}) {
  const status = file.status;
  return (
    <div className="q">
      <span className={"st " + statusClass(status)}>{statusGlyph(status)}</span>
      <div className="name">
        {fileName(file.path)}
        <small>
          {pagesLabel(file.pages)}
          {file.review_changes_count > 0 ? ` · ${file.review_changes_count} corrección del revisor` : ""}
          {file.reason ? (
            <>
              {" · "}
              <b>{reasonLabel(file.reason)}</b>
            </>
          ) : null}
        </small>
      </div>
      <div className="actions">
        {status === "running" && (
          <span className="t" style={{ marginRight: 4 }}>
            {cancelling ? "cancelando tras esta página" : engineLabel(currentEngine)} {currentPage && file.pages ? `· pág. ${currentPage} de ${file.pages}` : ""}
          </span>
        )}
        {status === "cancelled" && (
          <span className="t" style={{ marginRight: 4 }}>
            Cancelado{file.output_path ? " · salida parcial conservada" : ""}
          </span>
        )}
        {status === "pending" && (
          <button className="btn ghost small" disabled={cancelling} onClick={onRemoveQueued}>
            {cancelling ? "cancelando…" : "✕ Quitar"}
          </button>
        )}
        {status === "running" && (
          <button className="btn ghost small" disabled={cancelling} onClick={onCancelRunning}>
            ✕ Cancelar
          </button>
        )}
        {(status === "warn" || status === "failed") && (
          <button className="pill warn" style={{ cursor: "pointer", border: "none" }} onClick={onRetry}>
            Reintentar con IA
          </button>
        )}
        {(status === "ok" || status === "warn") && (
          <>
            {file.output_path && (
              <button className="btn ghost small" onClick={onOpenFile}>
                Abrir .md
              </button>
            )}
            <button className="btn ghost small" onClick={onReview}>
              Revisar
            </button>
            <button className="btn ghost small" onClick={onRetryPopover}>
              Volver a convertir con… ▾
            </button>
            {file.output_path && (
              <button className="btn ghost small" onClick={onRevealFile}>
                Mostrar en Finder
              </button>
            )}
          </>
        )}
        {status === "cancelled" && (
          <button className="btn ghost small" onClick={onRetryPopover}>
            Volver a convertir
          </button>
        )}
      </div>
      {/* No per-file estimate exists server-side (EstimateResponse is
          per-stage, not per-file) — a hardcoded "pages*2 min" contradicted
          the real estimate (see flow-issues.md M6), so this shows the file's
          actual elapsed time once known and nothing before that. */}
      <span className="tm">{file.seconds != null ? fmtSeconds(file.seconds) : ""}</span>
    </div>
  );
}

function statusClass(s: JobFileState["status"]): string {
  if (s === "ok") return "ok";
  if (s === "running") return "run";
  if (s === "warn") return "warn";
  if (s === "failed") return "bad";
  if (s === "cancelled") return "bad";
  return "wait";
}
function statusGlyph(s: JobFileState["status"]): string {
  if (s === "ok") return "✓";
  if (s === "warn") return "!";
  if (s === "failed") return "✕";
  if (s === "cancelled") return "⊘";
  return "";
}
