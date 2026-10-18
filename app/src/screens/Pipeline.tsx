import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAppStore } from "../store/useAppStore";
import { api } from "../api";
import { ApiError } from "../api/client";
import type { EstimateResponse, InspectResponse, Pipeline, StageKind } from "../api/types";
import { fmtNumber, fmtTime, fmtUsd, plural, stripModelProviderPrefixes } from "../lib/format";
import { GPU_MAX_GB, modelFor } from "../lib/scheduling";
import { changedPresets } from "../lib/presets";
import ModelPicker from "../components/ModelPicker";

// Exported so the Wizard's "Personalizado" onboarding card (app/src/screens/Wizard.tsx) needs a
// full Pipeline shape (workers/clean/tables/emit_chunks/ocr_dpi) to fall back
// on if "Apuntes a mano" hasn't loaded yet when it writes its custom choice.
export const DEFAULT_PIPELINE: Pipeline = {
  ocr_model: "ollama:qwen2.5vl:7b",
  figure_model: "ollama:qwen2.5vl:7b",
  review_model: "openai:gpt-4o-mini",
  workers: 4,
  describe_figures: true,
  clean: true,
  tables: true,
  emit_chunks: false,
  ocr_dpi: 300,
};

export default function PipelineScreen() {
  const navigate = useNavigate();
  const editingPipeline = useAppStore((s) => s.editingPipeline);
  const editingPresetId = useAppStore((s) => s.editingPresetId);
  const clearEditPreset = useAppStore((s) => s.clearEditPreset);
  const presets = useAppStore((s) => s.presets);
  const setPresets = useAppStore((s) => s.setPresets);
  const selectedPresetId = useAppStore((s) => s.selectedPresetId);
  const setSelectedPresetId = useAppStore((s) => s.setSelectedPresetId);
  const settings = useAppStore((s) => s.settings);
  const setSettings = useAppStore((s) => s.setSettings);
  // The Pipeline screen simulates against whatever folder was actually
  // inspected on Convertir — there is no server-side notion of a synthetic
  // "test" load (see app/src/api/mockClient.ts's note on "test:apuntes" /
  // "test:archivo" being a mock-only convention the real server 404s on).
  const inspect = useAppStore((s) => s.inspect);
  const setInspect = useAppStore((s) => s.setInspect);
  const system = useAppStore((s) => s.system);
  // The remembered root (see useAppStore's `inputPath`, persisted to
  // localStorage as '2md.lastInputPath') lets this screen re-inspect without
  // sending the user back to Convertir first (flow-issues.md C15).
  const inputPath = useAppStore((s) => s.inputPath);

  const [pipeline, setPipeline] = useState<Pipeline>(editingPipeline ?? DEFAULT_PIPELINE);
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [estimateError, setEstimateError] = useState(false);
  const [manualIn, setManualIn] = useState("");
  const [manualOut, setManualOut] = useState("");
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [reinspecting, setReinspecting] = useState(false);

  const inspectId = inspect?.inspect_id ?? null;

  // Re-POSTs /api/inspect for the remembered root so this screen's estimate
  // can catch up to the engine's real numbers instead of staying on the
  // client-side GPU approximation indefinitely (flow-issues.md C15, N1).
  async function reinspect() {
    if (!inputPath || reinspecting) return;
    setReinspecting(true);
    try {
      const r = await api.inspect({ path: inputPath });
      setInspect(r);
    } catch {
      // Leave the previous inspect (if any) in place; the user can retry.
    } finally {
      setReinspecting(false);
    }
  }

  // Always ask the engine for a GPU verdict — even with no folder inspected
  // yet — rather than falling back to a client-side approximation of GPU
  // residency (flow-issues.md N1: the client's hardcoded model-size table
  // can under-count vs. Ollama's real installed sizes and flip an actual
  // block into an apparent "cabe justo"). `inspect_id` is only included when
  // one exists; the engine's pipeline-only estimate mode (no inspect_id)
  // answers with real gpu_resident_gb/blocked/warning and placeholder
  // file/time/cost fields (`estimate_scope: "pipeline_only"`).
  //
  // A server that doesn't support the pipeline-only mode yet rejects a
  // request with no inspect_id — degrade gracefully by treating that
  // failure as "no verdict yet" rather than a real error (only a request
  // that *did* carry an inspect_id and still failed is worth telling the
  // user about).
  useEffect(() => {
    const t = setTimeout(() => {
      api
        .estimate({ pipeline, ...(inspectId ? { inspect_id: inspectId } : {}) })
        .then((r) => {
          setEstimate(r);
          setEstimateError(false);
        })
        .catch((err) => {
          setEstimate(null);
          setEstimateError(!!inspectId);
          // A stale inspect_id (e.g. the server restarted, or it just aged
          // out) 404s — re-inspect the remembered root automatically rather
          // than leaving the user stuck without a real verdict.
          if (inspectId && err instanceof ApiError && err.status === 404) reinspect();
        });
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pipeline, inspectId]);

  const unknownPriceStage = estimate?.stages.find((s) => s.unknown_price);

  function set<K extends keyof Pipeline>(key: K, value: Pipeline[K]) {
    setPipeline((p) => ({ ...p, [key]: value }));
  }

  function applyAlternative(patch: Partial<Pipeline>) {
    setPipeline((p) => ({ ...p, ...patch }));
  }

  function reset() {
    const original = presets.find((p) => p.id === editingPresetId);
    setPipeline(original?.pipeline ?? DEFAULT_PIPELINE);
  }

  async function saveInto(name: string, id: string | null) {
    const list = [...presets];
    if (id) {
      const idx = list.findIndex((p) => p.id === id);
      if (idx >= 0) list[idx] = { ...list[idx], pipeline };
    } else {
      list.push({ id: `preset-${Date.now()}`, name, builtin: false, pipeline });
    }
    // PATCH /api/presets upserts by id and never deletes (flow-issues.md
    // C7) — only the preset(s) that actually changed need to be sent.
    // `changedPresets` decides whether a PATCH is needed at all (N12); the
    // store is then refreshed from GET /api/presets, the server's own
    // source of truth, rather than trusted from this local `list`.
    const changed = changedPresets(presets, list);
    if (changed.length > 0) await api.patchPresets({ presets: changed });
    setPresets(await api.getPresets());
    clearEditPreset();
    navigate("/convertir");
  }

  const currentPresetName = presets.find((p) => p.id === editingPresetId)?.name ?? "nuevo preajuste";
  const editingPreset = presets.find((p) => p.id === editingPresetId) ?? null;
  const [removing, setRemoving] = useState(false);

  // "Quitar" (flow-issues.md C17) — only ever offered for a preset the user
  // owns; a builtin never renders this button at all (see below), so this
  // is never reachable for one. Mirrors Ajustes.tsx's removePreset: steer
  // `selectedPresetId`/`Settings.default_preset_id` away from the deleted
  // id first, then let GET /api/presets (via the DELETE response) be the
  // new source of truth.
  async function removePreset() {
    if (!editingPreset || editingPreset.builtin) return;
    if (!window.confirm(`¿Quitar el preajuste «${editingPreset.name}»? Esta acción no se puede deshacer.`)) return;
    setRemoving(true);
    try {
      const remaining = presets.filter((x) => x.id !== editingPreset.id);
      const fallbackId = remaining.find((x) => x.id === "apuntes-a-mano")?.id ?? remaining[0]?.id ?? "apuntes-a-mano";
      if (selectedPresetId === editingPreset.id) setSelectedPresetId(fallbackId);
      const fresh = await api.deletePreset(editingPreset.id);
      setPresets(fresh);
      if (settings?.default_preset_id === editingPreset.id) {
        const updated = await api.putSettings({ ...settings, default_preset_id: fallbackId });
        setSettings(updated);
      }
      clearEditPreset();
      navigate("/convertir");
    } finally {
      setRemoving(false);
    }
  }

  // The GPU bar renders the engine's own resident-memory arithmetic verbatim
  // (`gpu_resident_gb` is RESIDENT_FACTOR-scaled, not file sizes summed) —
  // no client-side approximation stands in for it any more (flow-issues.md
  // N1; `lib/scheduling.ts`'s `gpuState` stays in use only for VITE_MOCK=1
  // and the Wizard's pre-inspect "Personalizado" card, neither of which has
  // a real `/api/estimate` to ask). Before the first response lands, the
  // bar has nothing to show yet; `gpuLimit` only picks the axis denominator
  // to render meanwhile.
  const gpuLimit = estimate?.gpu_limit_gb || system?.gpu_limit_gb || GPU_MAX_GB;
  const gpuResident = estimate?.gpu_resident_gb ?? 0;
  const gpuHeadroom = estimate?.gpu_headroom_gb ?? gpuLimit;
  const blockedInfo = estimate?.blocked ?? null;
  const gpuWarning = blockedInfo ? null : estimate?.warning ?? null;

  return (
    <div>
      <div className="mainhead">
        <div>
          <div className="eyebrow">Pipeline</div>
          <h2>{editingPresetId ? `Editando «${currentPresetName}»` : "Nuevo preajuste"}</h2>
        </div>
      </div>

      <div className="simtop">
        <div>
          <div className="loadinfo">
            {inspect ? (
              <>
                Simulando con la última carpeta inspeccionada.{" "}
                {inputPath && (
                  <button type="button" className="btn ghost small" onClick={reinspect} disabled={reinspecting}>
                    {reinspecting ? "Inspeccionando…" : "Volver a inspeccionar"}
                  </button>
                )}
              </>
            ) : inputPath ? (
              <>
                Sin datos reales todavía.{" "}
                <button type="button" className="btn ghost small" onClick={reinspect} disabled={reinspecting}>
                  {reinspecting ? "Inspeccionando…" : "Volver a inspeccionar"}
                </button>{" "}
                <button type="button" className="btn ghost small" onClick={() => navigate("/convertir")}>
                  Ir a Convertir →
                </button>
              </>
            ) : (
              <>
                Elige una carpeta en Convertir para simular con datos reales.{" "}
                <button type="button" className="btn ghost small" onClick={() => navigate("/convertir")}>
                  Ir a Convertir →
                </button>
              </>
            )}
          </div>
        </div>
        <div>
          <span className="eyebrow">Archivos a la vez (workers)</span>
          <div className="slider">
            <input
              type="range"
              min={1}
              max={8}
              value={pipeline.workers}
              onChange={(e) => set("workers", Number(e.target.value))}
            />
            <span className="mono">{pipeline.workers}</span>
          </div>
          <div className="loadinfo">
            {!inspectId
              ? "Elige una carpeta en Convertir para ver cuántos avanzan a la vez"
              : estimateError
              ? "no se pudo estimar"
              : !estimate
              ? "—"
              : estimate.parallel_files < pipeline.workers
              ? `Pides ${pipeline.workers}, pero con un modelo local sólo 1 avanza a la vez`
              : `${plural(pipeline.workers, "archivo")} ${pipeline.workers === 1 ? "avanza" : "avanzan"} a la vez`}
          </div>
        </div>
      </div>

      <FlowDiagram estimate={estimate} pipeline={pipeline} set={set} inspect={inspect} />

      {estimate?.blocked && (
        <div className="flow-note bad">
          <b>{estimate.blocked.title}</b>
          <span>{estimate.blocked.body}</span>
        </div>
      )}
      {gpuWarning && (
        <div className="flow-note warn">
          <b>{gpuWarning.title}</b>
          <span>{gpuWarning.body}</span>
        </div>
      )}

      {estimateError && (
        <div className="block warnblock" style={{ marginTop: 10 }}>
          <b>No se pudo estimar</b>
          <span>El servidor no pudo calcular la estimación para esta carpeta. Vuelve a intentarlo desde Convertir.</span>
        </div>
      )}

      <div className="verdict standalone">
        <div className="n">
          {estimate?.blocked ? "–" : estimate?.parallel_files ?? "–"}
          <small>archivos en paralelo</small>
        </div>
        <div className="n">
          {estimate?.blocked ? "–" : estimate ? fmtTime(estimate.total_seconds) : "–"}
          <small>tiempo estimado</small>
        </div>
        <div className="n">
          {estimate?.blocked ? "–" : estimate?.total_usd == null ? "?" : fmtUsd(estimate.total_usd)}
          <small>coste en nube · USD</small>
        </div>
        <div className="tip">
          {estimate?.alternatives.map((a, i) => (
            <span key={i}>
              <a onClick={() => applyAlternative(a.pipeline_patch)}>{a.label}</a>: {fmtTime(a.total_seconds)} ·{" "}
              {a.total_usd == null ? "?" : fmtUsd(a.total_usd)}.{" "}
            </span>
          ))}
        </div>
      </div>

      {unknownPriceStage && (
        <div className="block warnblock">
          <b>Precio del modelo desconocido</b>
          <span>genai-prices aún no tiene tabla para «{unknownPriceStage.model}». El tiempo se estima igual; el coste no.</span>
          <div className="addprice">
            <label>
              Entrada
              <input placeholder="$ / M tokens" value={manualIn} onChange={(e) => setManualIn(e.target.value)} />
            </label>
            <label>
              Salida
              <input placeholder="$ / M tokens" value={manualOut} onChange={(e) => setManualOut(e.target.value)} />
            </label>
            <button
              className="btn small"
              onClick={async () => {
                if (!unknownPriceStage.model || !inspectId) return;
                await api.manualPrice({ model: unknownPriceStage.model, input_per_mtok: Number(manualIn) || 0, output_per_mtok: Number(manualOut) || 0 });
                try {
                  const r = await api.estimate({ inspect_id: inspectId, pipeline });
                  setEstimate(r);
                  setEstimateError(false);
                } catch {
                  setEstimate(null);
                  setEstimateError(true);
                }
              }}
            >
              Añadir precios
            </button>
          </div>
        </div>
      )}

      <div className="ram">
        <span>
          Memoria GPU: {fmtNumber(gpuResident, 1)} / {fmtNumber(gpuLimit, 1)} GB
          {" · "}
          {estimate?.blocked
            ? `${fmtNumber(Math.abs(gpuHeadroom), 1)} GB por encima del límite`
            : `${fmtNumber(Math.max(0, gpuHeadroom), 1)} GB libres`}
        </span>
        <div className="bar">
          <i
            style={{
              width: `${Math.min(100, (gpuResident / gpuLimit) * 100)}%`,
              background: estimate?.blocked ? "var(--bad)" : gpuWarning ? "var(--warn)" : "var(--ink)",
            }}
          />
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 16 }}>
        {editingPreset && !editingPreset.builtin && (
          <button className="btn ghost" onClick={removePreset} disabled={removing} style={{ marginRight: "auto" }}>
            {removing ? "Quitando…" : "Quitar"}
          </button>
        )}
        <button className="btn ghost" onClick={reset}>
          Restablecer
        </button>
        <button className="btn" onClick={() => setSaveAsOpen(true)}>
          Guardar como nuevo…
        </button>
        <button className="btn primary" onClick={() => saveInto(currentPresetName, editingPresetId)}>
          {editingPresetId ? `Guardar en «${currentPresetName}»` : "Guardar"}
        </button>
      </div>

      {saveAsOpen && (
        <div className="overlay" onClick={() => setSaveAsOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>Guardar como nuevo preajuste</h3>
            <div className="row">
              <label>Nombre</label>
              <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Mi preajuste" />
            </div>
            <div className="actions">
              <button className="btn ghost" onClick={() => setSaveAsOpen(false)}>
                Cancelar
              </button>
              <button className="btn primary" onClick={() => saveInto(newName || "Nuevo preajuste", null)}>
                Guardar
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Flow diagram — files enter on the left as "Archivos", pass through however
// many pipeline stages actually use a model, and leave on the right as
// "Archivos finales en Markdown". Parallelism and the bottleneck are shown as
// *topology*, not as a separate lane list (see the task's rejected "5 cards +
// 3 horizontal lanes" layout):
//   - a stage running on the local GPU always collapses to ONE shared node
//     (there is exactly one GPU permit — see lib/scheduling.ts's
//     localModelGroups) that every lane visibly funnels through;
//   - a cloud stage fans out to up to REMOTE_PERMITS nodes;
//   - a CPU/no-model stage fans out to one node per lane (i.e. per worker).
// The whole thing is one inline SVG (connectors) with plain HTML nodes
// absolutely positioned on top of it (so ModelPicker — a stateful combobox
// with its own popup — just works, unlike inside <foreignObject>). Both are
// addressed in the same 1200×480 coordinate space via percentages, and the
// wrapper's `aspect-ratio` keeps that space locked so they never drift apart
// as the container is resized — that's what lets the diagram scale with no
// horizontal page scroll at 1024px/1440px.
// ---------------------------------------------------------------------------

const VBW = 1200;
const VBH = 480;
const LANE_MIN = 90;
const LANE_MAX = 370;
const CENTER_Y = 230;

function laneY(n: number, i: number): number {
  if (n <= 1) return CENTER_Y;
  return LANE_MIN + (i * (LANE_MAX - LANE_MIN)) / (n - 1);
}

function pct(v: number, of: number): string {
  return `${(v / of) * 100}%`;
}

function bez(x0: number, y0: number, x1: number, y1: number): string {
  const mx = (x0 + x1) / 2;
  return `M ${x0} ${y0} C ${mx} ${y0} ${mx} ${y1} ${x1} ${y1}`;
}

interface Point {
  x: number;
  y: number;
}

function Pin({
  x,
  y,
  w,
  h,
  className,
  children,
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  className: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={className}
      style={{
        left: pct(x, VBW),
        top: pct(y, VBH),
        width: pct(w, VBW),
        // `minHeight`, not `height`: a stage card's height is only a
        // starting point — when its ModelPicker trigger wraps a long id's
        // pills to a second line (see `.combo-trigger`'s `flex-wrap` in
        // theme.css), the card must grow to fit them rather than clip them
        // or let them spill past its own border.
        minHeight: pct(h, VBH),
      }}
    >
      {children}
    </div>
  );
}

function kindLabel(kind: StageKind): string {
  return kind === "gpu" ? "GPU local" : kind === "cloud" ? "nube" : kind === "cpu" ? "CPU" : "sin modelo";
}

function annotate(seconds: number, usd: number | null, unknownPrice: boolean): string {
  return `${fmtTime(seconds)} · ${unknownPrice || usd == null ? "¿precio?" : fmtUsd(usd)}`;
}

function findStage(estimate: EstimateResponse | null, key: string) {
  return estimate?.stages.find((s) => s.key === key) ?? null;
}

/** One model-bearing column (ocr/figures/review): a single funnel node when
 * it runs on the GPU, up to `n` fanned nodes (one real, the rest decorative
 * "also running this" ghosts) otherwise. Returns the connector paths that
 * lead into it and the exit points the next column should start from. */
function modelColumn(opts: {
  keyPrefix: string;
  colX: number;
  entryX: number;
  boundaryAfterX: number;
  kind: StageKind;
  prev: Point[];
  n: number;
  title: string;
  annotation: string;
  picker: React.ReactNode;
  bottleneck: boolean;
  gpuBlocked?: boolean;
  /** e.g. "sustituido" — rendered next to `title` (flow-issues.md C18). */
  titleBadge?: React.ReactNode;
  /** The engine's own explanation for a model substitution, rendered under
   * the time/cost annotation (flow-issues.md C18). */
  note?: string | null;
}): { nodes: React.ReactNode[]; paths: { d: string; hot: boolean }[]; next: Point[] } {
  const { keyPrefix, colX, entryX, boundaryAfterX, kind, prev, n, title, annotation, picker, bottleneck, gpuBlocked, titleBadge, note } =
    opts;
  const nodes: React.ReactNode[] = [];
  const paths: { d: string; hot: boolean }[] = [];
  const next: Point[] = [];

  if (kind === "none") {
    nodes.push(
      <Pin key={`${keyPrefix}-card`} x={colX} y={CENTER_Y} w={170} h={70} className="flow-node none">
        <div className="fn-title">{title}</div>
        {picker}
        <div className="fn-sub">sin modelo</div>
      </Pin>
    );
    for (let i = 0; i < n; i++) {
      const y = laneY(n, i);
      paths.push({ d: bez(prev[i].x, prev[i].y, boundaryAfterX, y), hot: false });
      next.push({ x: boundaryAfterX, y });
    }
    return { nodes, paths, next };
  }

  if (kind === "gpu") {
    const halfW = 95,
      halfH = 46;
    nodes.push(
      <Pin
        key={`${keyPrefix}-card`}
        x={colX}
        y={CENTER_Y}
        w={halfW * 2}
        h={halfH * 2}
        className={"flow-node gpu funnel" + (bottleneck ? " bottleneck" : "") + (gpuBlocked ? " blocked" : "")}
      >
        <div className="fn-title">
          {title}
          {titleBadge}
        </div>
        {picker}
        <div className="fn-anno">{annotation}</div>
        {note && <div className="fn-note">{note}</div>}
        {bottleneck && !gpuBlocked && <div className="fn-cap">aquí se espera</div>}
      </Pin>
    );
    for (let i = 0; i < n; i++) {
      const y = laneY(n, i);
      paths.push({ d: bez(prev[i].x, prev[i].y, colX - halfW, CENTER_Y), hot: bottleneck });
      paths.push({ d: bez(colX + halfW, CENTER_Y, boundaryAfterX, y), hot: bottleneck });
      next.push({ x: boundaryAfterX, y });
    }
    return { nodes, paths, next };
  }

  // cpu or cloud: one real node (with the live picker) plus ghost nodes for
  // the other lanes, so the fan-out reads as "N of these run at once".
  for (let i = 0; i < n; i++) {
    const y = laneY(n, i);
    if (i === 0) {
      nodes.push(
        <Pin
          key={`${keyPrefix}-card`}
          x={colX}
          y={y}
          w={170}
          // Reserves a fixed row for `.fn-anno` (and, when it appears, the
          // "aquí se espera" cap) up front instead of only via `minHeight`
          // growing after the fact — at a CPU/cloud fan's tighter lane
          // spacing (up to 4 lanes sharing the same 90–370 band) that late
          // growth is exactly what let "0 s · $0" run into the next lane's
          // "también CPU" ghost (flow-issues.md N8).
          h={96}
          className={"flow-node " + kind + (bottleneck ? " bottleneck" : "")}
        >
          <div className="fn-title">
            {title}
            {titleBadge}
          </div>
          {picker}
          <div className="fn-anno">{annotation}</div>
          {note && <div className="fn-note">{note}</div>}
          <div className="fn-cap">{bottleneck ? "aquí se espera" : ""}</div>
        </Pin>
      );
      paths.push({ d: bez(prev[i].x, prev[i].y, entryX, y), hot: bottleneck });
    } else {
      nodes.push(
        <Pin key={`${keyPrefix}-ghost-${i}`} x={colX} y={y} w={110} h={52} className={"flow-node ghost " + kind}>
          <div className="fn-ghost-label">también {kindLabel(kind)}</div>
        </Pin>
      );
      paths.push({ d: bez(prev[i].x, prev[i].y, entryX, y), hot: false });
    }
    paths.push({ d: bez(entryX, y, boundaryAfterX, y), hot: bottleneck && i === 0 });
    next.push({ x: boundaryAfterX, y });
  }
  return { nodes, paths, next };
}

function FlowDiagram({
  estimate,
  pipeline,
  set,
  inspect,
}: {
  estimate: EstimateResponse | null;
  pipeline: Pipeline;
  set: <K extends keyof Pipeline>(key: K, value: Pipeline[K]) => void;
  inspect: InspectResponse | null;
}) {
  const ocrStage = findStage(estimate, "ocr");
  const figStage = findStage(estimate, "figures");
  const revStage = findStage(estimate, "review");

  // The engine silently collapses the figures stage onto OCR's model when
  // the user's own choice wouldn't fit the GPU at the same time
  // (`gpu_memory.effective_figure_model()`) — the combobox still shows what
  // the user picked, so the only way to notice is this pill + the note
  // below (flow-issues.md C18). `note` (equivalently `effective_model`,
  // which the server only ever sets alongside it) is the server's own
  // "a substitution happened" flag — schemas.py's EstimateStage says a
  // client can key off `note is not None` alone, so there's no need to
  // compare `effective_model` against the pipeline's own `figure_model`.
  const figSubstituted = !!figStage?.note;

  // Fall back to the picker's own model kind (via lib/scheduling.ts's
  // modelFor, the same table the Wizard uses) rather than assuming "cpu" —
  // an ollama:* selection is "gpu" and must funnel/merge even before an
  // estimate exists.
  const ocrKind: StageKind = ocrStage?.kind ?? modelFor(pipeline.ocr_model).kind;
  const figKind: StageKind = figStage?.kind ?? (pipeline.describe_figures ? modelFor(pipeline.figure_model).kind : "none");
  const revKind: StageKind = revStage?.kind ?? (pipeline.review_model ? modelFor(pipeline.review_model).kind : "none");

  const mergedGpu = ocrKind === "gpu" && figKind === "gpu";

  // Before an estimate exists, a local/GPU stage still only ever runs one
  // file at a time (one shared GPU permit) — showing `pipeline.workers`
  // lanes there drew e.g. 3 lanes + "y 4 más" for workers=8 with local OCR,
  // contradicting what the diagram shows the instant an estimate lands
  // (flow-issues.md N5).
  const usesLocalGpu = ocrKind === "gpu" || figKind === "gpu" || revKind === "gpu";
  const rawParallel = estimate?.blocked ? 0 : estimate ? estimate.parallel_files : usesLocalGpu ? 1 : pipeline.workers;
  const n = Math.max(1, rawParallel || 1);
  const drawnLanes = Math.min(4, n);
  const overflow = Math.max(0, n - 4);

  const bottleneckKind = estimate?.blocked ? null : estimate?.bottleneck ?? null;
  const stagesForBottleneck = [
    { key: "ocr", kind: ocrKind, seconds: ocrStage?.seconds ?? 0 },
    { key: "figures", kind: figKind, seconds: figStage?.seconds ?? 0 },
    { key: "review", kind: revKind, seconds: revStage?.seconds ?? 0 },
  ];
  function isBottleneck(key: string, kind: StageKind, seconds: number): boolean {
    if (!bottleneckKind || kind !== bottleneckKind || seconds <= 0) return false;
    const max = Math.max(...stagesForBottleneck.filter((s) => s.kind === bottleneckKind).map((s) => s.seconds));
    return seconds === max && stagesForBottleneck.find((s) => s.seconds === max)?.key === key;
  }

  const ocrPicker = <ModelPicker stage="ocr" value={pipeline.ocr_model} onChange={(v) => set("ocr_model", v as string)} />;
  const figPicker = (
    <ModelPicker
      stage="figures"
      allowOff
      offLabel="No describir"
      value={pipeline.describe_figures ? pipeline.figure_model ?? "off" : "off"}
      onChange={(v) => {
        if (v === "off") {
          set("describe_figures", false);
          set("figure_model", null);
        } else {
          set("describe_figures", true);
          set("figure_model", v);
        }
      }}
    />
  );
  const revPicker = (
    <ModelPicker
      stage="review"
      allowOff
      offLabel="Sin revisión"
      value={pipeline.review_model ?? "off"}
      onChange={(v) => set("review_model", v === "off" ? null : v)}
    />
  );

  // Column x positions. Kept generous (≥170 wide) so the ModelPicker combobox
  // — which fills its node at 100% width — stays legible once the viewBox is
  // scaled down to a 1024px rail.
  const SRC_X = 70;
  const RAIL1_X = 205;
  const OCR_X = 360;
  const FIG_X = 600;
  const REV_X = 840;
  const RAIL2_X = 1000;
  const SINK_X = 1125;

  const nodes: React.ReactNode[] = [];
  const paths: { d: string; hot: boolean }[] = [];
  const dots: { x: number; y: number }[] = [];

  // Source
  const sourceSub = inspectSourceLabel(inspect);
  nodes.push(
    <Pin key="src" x={SRC_X} y={CENTER_Y} w={120} h={72} className="flow-node source">
      <div className="fn-title">Archivos</div>
      {sourceSub && <div className="fn-sub">{sourceSub}</div>}
    </Pin>
  );
  let prev: Point[] = Array.from({ length: drawnLanes }, () => ({ x: SRC_X + 60, y: CENTER_Y }));

  // Rail 1 — "detectar y extraer" (always no-model, one dot per lane)
  {
    const next: Point[] = [];
    for (let i = 0; i < drawnLanes; i++) {
      const y = laneY(drawnLanes, i);
      paths.push({ d: bez(prev[i].x, prev[i].y, RAIL1_X, y), hot: false });
      dots.push({ x: RAIL1_X, y });
      paths.push({ d: bez(RAIL1_X, y, OCR_X - 95, y), hot: false });
      next.push({ x: OCR_X - 95, y });
    }
    prev = next;
    nodes.push(
      <Pin key="rail1-label" x={RAIL1_X} y={LANE_MIN - 45} w={190} h={30} className="flow-rail-label">
        Detectar y extraer
      </Pin>
    );
  }

  const gpuBlocked = !!estimate?.blocked;

  if (mergedGpu) {
    const cx = (OCR_X + FIG_X) / 2;
    const halfW = (FIG_X - OCR_X) / 2 + 95;
    const halfH = 92;
    const bottleneck = isBottleneck("ocr", "gpu", ocrStage?.seconds ?? 0) || isBottleneck("figures", "gpu", figStage?.seconds ?? 0);
    nodes.push(
      <Pin
        key="merged-gpu"
        x={cx}
        y={CENTER_Y}
        w={halfW * 2}
        h={halfH * 2}
        className={"flow-node gpu funnel merged" + (bottleneck ? " bottleneck" : "") + (gpuBlocked ? " blocked" : "")}
      >
        <div className="fn-merged-hd">GPU de este Mac · un permiso compartido</div>
        <div className="fn-merged-row">
          <div className="fn-merged-col">
            <div className="fn-title">2 · Leer páginas escaneadas</div>
            {ocrPicker}
            <div className="fn-anno">{annotate(ocrStage?.seconds ?? 0, ocrStage?.usd ?? 0, !!ocrStage?.unknown_price)}</div>
          </div>
          <div className="fn-merged-col">
            <div className="fn-title">
              3 · Describir figuras
              {figSubstituted && (
                <span className="pill warn" style={{ marginLeft: 6 }}>
                  sustituido
                </span>
              )}
            </div>
            {figPicker}
            <div className="fn-anno">{annotate(figStage?.seconds ?? 0, figStage?.usd ?? 0, !!figStage?.unknown_price)}</div>
            {figStage?.note && <div className="fn-note">{stripModelProviderPrefixes(figStage.note)}</div>}
          </div>
        </div>
        {bottleneck && !gpuBlocked && <div className="fn-cap">aquí se espera</div>}
      </Pin>
    );
    const next: Point[] = [];
    for (let i = 0; i < drawnLanes; i++) {
      const y = laneY(drawnLanes, i);
      paths.push({ d: bez(prev[i].x, prev[i].y, cx - halfW, CENTER_Y), hot: bottleneck });
      paths.push({ d: bez(cx + halfW, CENTER_Y, REV_X - 95, y), hot: bottleneck });
      next.push({ x: REV_X - 95, y });
    }
    prev = next;
  } else {
    const ocrCol = modelColumn({
      keyPrefix: "ocr",
      colX: OCR_X,
      entryX: OCR_X - 85,
      boundaryAfterX: FIG_X - 95,
      kind: ocrKind,
      prev,
      n: drawnLanes,
      title: "2 · Leer páginas escaneadas",
      annotation: annotate(ocrStage?.seconds ?? 0, ocrStage?.usd ?? 0, !!ocrStage?.unknown_price),
      picker: ocrPicker,
      bottleneck: isBottleneck("ocr", ocrKind, ocrStage?.seconds ?? 0),
      gpuBlocked,
    });
    nodes.push(...ocrCol.nodes);
    paths.push(...ocrCol.paths);
    prev = ocrCol.next;

    const figCol = modelColumn({
      keyPrefix: "fig",
      colX: FIG_X,
      entryX: FIG_X - 85,
      boundaryAfterX: REV_X - 95,
      kind: figKind,
      prev,
      n: drawnLanes,
      title: "3 · Describir figuras",
      annotation: annotate(figStage?.seconds ?? 0, figStage?.usd ?? 0, !!figStage?.unknown_price),
      picker: figPicker,
      bottleneck: isBottleneck("figures", figKind, figStage?.seconds ?? 0),
      gpuBlocked,
      titleBadge: figSubstituted && (
        <span className="pill warn" style={{ marginLeft: 6 }}>
          sustituido
        </span>
      ),
      note: figStage?.note,
    });
    nodes.push(...figCol.nodes);
    paths.push(...figCol.paths);
    prev = figCol.next;
  }

  const revCol = modelColumn({
    keyPrefix: "rev",
    colX: REV_X,
    entryX: REV_X - 85,
    boundaryAfterX: RAIL2_X,
    kind: revKind,
    prev,
    n: drawnLanes,
    title: "4 · Revisar erratas",
    annotation: annotate(revStage?.seconds ?? 0, revStage?.usd ?? 0, !!revStage?.unknown_price),
    picker: revPicker,
    bottleneck: isBottleneck("review", revKind, revStage?.seconds ?? 0),
    gpuBlocked,
  });
  nodes.push(...revCol.nodes);
  paths.push(...revCol.paths);
  prev = revCol.next;

  nodes.push(
    <Pin key="rail2-label" x={RAIL2_X} y={LANE_MIN - 45} w={190} h={30} className="flow-rail-label">
      Limpiar y escribir
    </Pin>
  );
  {
    const next: Point[] = [];
    for (let i = 0; i < drawnLanes; i++) {
      const y = laneY(drawnLanes, i);
      dots.push({ x: RAIL2_X, y });
      paths.push({ d: bez(prev[i].x, prev[i].y, SINK_X - 70, CENTER_Y), hot: false });
      next.push({ x: SINK_X - 70, y: CENTER_Y });
    }
    prev = next;
  }

  nodes.push(
    <Pin key="sink" x={SINK_X} y={CENTER_Y} w={140} h={80} className="flow-node sink">
      <div className="fn-title">Archivos finales en Markdown</div>
    </Pin>
  );

  return (
    <div className="flow-wrap" role="img" aria-label="Diagrama de flujo del pipeline de conversión">
      <svg className="flow-svg" viewBox={`0 0 ${VBW} ${VBH}`} preserveAspectRatio="xMidYMid meet">
        {paths.map((p, i) => (
          <path key={i} d={p.d} className={p.hot ? "hot" : ""} />
        ))}
        {dots.map((d, i) => (
          <circle key={i} cx={d.x} cy={d.y} r={5} className="dot" />
        ))}
      </svg>
      {nodes}
      {overflow > 0 && (
        <Pin x={SRC_X} y={CENTER_Y + 90} w={160} h={24} className="flow-lane-overflow">
          …y {overflow} más
        </Pin>
      )}
    </div>
  );
}

/** The source node's subtitle: the real inspected folder's name + how many
 * files it holds (e.g. "Trabajo · 554"), or nothing at all before a folder
 * has been inspected — the quiet line above the diagram already covers that
 * case, so the node itself doesn't need a placeholder like "carpeta de
 * prueba" that never named anything real. */
function inspectSourceLabel(inspect: InspectResponse | null): string | null {
  if (!inspect) return null;
  const name = inspect.root?.split("/").filter(Boolean).pop();
  return name ? `${name} · ${inspect.totals.files}` : `${inspect.totals.files} archivos`;
}
