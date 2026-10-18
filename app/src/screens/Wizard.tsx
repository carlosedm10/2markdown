import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAppStore } from "../store/useAppStore";
import { api } from "../api";
import type { CloudProviderId, ModelId, Pipeline, Settings } from "../api/types";
import { fmtNumber } from "../lib/format";
import { handleRadioGroupKeyDown } from "../lib/a11y";
import { blockedForLocalModels, MODELS } from "../lib/scheduling";
import { changedPresets } from "../lib/presets";
import { DEFAULT_PIPELINE } from "./Pipeline";
import ModelPicker from "../components/ModelPicker";
import SettingsSummary from "../components/SettingsSummary";
import { CLOUD_PROVIDERS, PROVIDER_BLURBS } from "./Ajustes";

const APUNTES_PRESET_ID = "apuntes-a-mano";
const PERSONALIZADO_PRESET_ID = "personalizado";

/** `base-2`, `base-3`, … — the first suffix not already taken in `list`. */
function uniquePresetId(base: string, list: { id: string }[]): string {
  const taken = new Set(list.map((p) => p.id));
  let n = 2;
  while (taken.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}

const PROVIDER_LABELS: Record<CloudProviderId, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  groq: "Groq",
  mistral: "Mistral",
  openrouter: "OpenRouter",
};

type Step = 1 | 2 | 3 | 4;
type OutputMode = "sibling" | "fixed";

/** THE WIZARD IS THE FIRST EDIT OF SETTINGS: every screen here writes
 * straight into GET/PUT /api/settings (and, when a card says so, into a
 * `Preset` via PUT /api/presets) instead of keeping a client-side shadow
 * copy that Convertir then had to guess at separately — that mismatch (the
 * wizard promising one output folder/preset, Convertir silently using
 * another) was the reported bug. Nothing here is "done" until it round-trips
 * through the server at least once, which is also why step 3 reads its
 * summary back from a fresh GET /api/settings rather than from what this
 * component thinks it just saved. */
export default function Wizard() {
  const [step, setStep] = useState<Step>(1);
  const navigate = useNavigate();
  const system = useAppStore((s) => s.system);
  const setOnboarded = useAppStore((s) => s.setOnboarded);
  const setSystem = useAppStore((s) => s.setSystem);
  const presets = useAppStore((s) => s.presets);
  const setPresets = useAppStore((s) => s.setPresets);
  const settingsCache = useAppStore((s) => s.settings);
  const setSettingsCache = useAppStore((s) => s.setSettings);
  const setSelectedPresetId = useAppStore((s) => s.setSelectedPresetId);
  const setMode = useAppStore((s) => s.setMode);

  // --- Step 1: dónde guardar --------------------------------------------
  const [outputMode, setOutputMode] = useState<OutputMode>(settingsCache?.default_output_mode ?? "sibling");
  const [outputDir, setOutputDir] = useState<string>(settingsCache?.default_output_dir ?? "~/Documentos/2markdown");

  // --- Step 2: cómo convertir --------------------------------------------
  const [ocrChoice, setOcrChoice] = useState<"fast" | "local-ai" | "cloud-ai" | "custom">("local-ai");
  const [downloading, setDownloading] = useState(false);
  const [downloadDone, setDownloadDone] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [keyChecking, setKeyChecking] = useState(false);
  const [keyOk, setKeyOk] = useState<null | boolean>(null);
  const [localModel, setLocalModel] = useState<ModelId>("ollama:qwen2.5vl:7b");

  const apuntesPreset = presets.find((p) => p.id === APUNTES_PRESET_ID);
  const [customOcr, setCustomOcr] = useState<ModelId>(apuntesPreset?.pipeline.ocr_model ?? DEFAULT_PIPELINE.ocr_model);
  const [customFig, setCustomFig] = useState<string>(
    apuntesPreset?.pipeline.describe_figures ? apuntesPreset.pipeline.figure_model ?? "off" : "off"
  );
  const [customRev, setCustomRev] = useState<string>(apuntesPreset?.pipeline.review_model ?? "off");
  const [customName, setCustomName] = useState("Personalizado");
  const [saving, setSaving] = useState(false);
  const finishingRef = useRef(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // The id `savePersonalizadoPreset` minted the first time this wizard RUN
  // saved the "Personalizado" card — remembered so that going back to step 2
  // (e.g. to fix the output folder on step 1) and re-confirming PATCHes the
  // very same preset instead of minting a fresh id every pass (flow-issues.md
  // C17: re-confirming used to leave orphaned duplicate "Wizard R3" presets).
  // A ref, not state, because it must survive re-renders without itself
  // triggering one, and because it must NOT persist across separate wizard
  // runs (a fresh mount always starts at `null`).
  const mintedPersonalizadoIdRef = useRef<string | null>(null);

  // --- Step 3: resumen, read back from the server ------------------------
  const [effectiveSettings, setEffectiveSettings] = useState<Settings | null>(null);

  // Instant client-side verdict (no inspect_id yet, so no /api/estimate to
  // ask) — same RESIDENT_FACTOR arithmetic as the server, against this
  // machine's real GPU limit once /api/system has answered.
  const { blocked: customBlocked, warning: customWarning } = blockedForLocalModels(
    customOcr,
    customFig === "off" ? null : customFig,
    customRev === "off" ? null : customRev,
    system?.gpu_limit_gb || undefined
  );

  const localModel32bInstalled = !!system?.ollama.models.find((m) => m.name.replace(/^ollama:/, "") === "qwen2.5vl:32b");
  const localModel7bInstalled = downloadDone || !!system?.ollama.models.find((m) => m.name.replace(/^ollama:/, "") === "qwen2.5vl:7b");
  const rate7b = formatSecondsPerPage(MODELS["ollama:qwen2.5vl:7b"].secPerPage?.ocr);
  const rate32b = formatSecondsPerPage(MODELS["ollama:qwen2.5vl:32b"].secPerPage?.ocr);

  async function downloadModel() {
    setDownloading(true);
    setDownloadError(null);
    try {
      const { ok, error } = await api.pullOllamaModel("ollama:qwen2.5vl:7b");
      if (!ok) {
        setDownloadError(error || "Ollama CLI not found. Install from https://ollama.com and retry.");
        return;
      }
      setDownloadDone(true);
      const fresh = await api.getSystem();
      setSystem(fresh);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : String(err));
    } finally {
      setDownloading(false);
    }
  }

  async function checkKey() {
    setKeyChecking(true);
    try {
      await api.saveCloudKey("openai", apiKey);
      const fresh = await api.getSystem();
      setSystem(fresh);
      setKeyOk(fresh.cloud.openai.status === "ok");
    } catch {
      setKeyOk(false);
    } finally {
      setKeyChecking(false);
    }
  }

  /** Writes a pipeline into the builtin "Apuntes a mano" preset — used by the
   * "Con IA en este Mac" and "Con IA en la nube" cards, both of which say up
   * front that choosing them edits that preset (see the cards' notes below).
   * "Personalizado" never calls this: it creates its own preset instead (see
   * savePersonalizadoPreset). */
  async function saveIntoApuntes(patch: Partial<Pipeline>): Promise<string> {
    const list = [...presets];
    const idx = list.findIndex((p) => p.id === APUNTES_PRESET_ID);
    const basePipeline: Pipeline = idx >= 0 ? list[idx].pipeline : DEFAULT_PIPELINE;
    const pipeline: Pipeline = { ...basePipeline, ...patch };
    if (idx >= 0) {
      list[idx] = { ...list[idx], pipeline };
    } else {
      list.push({ id: APUNTES_PRESET_ID, name: "Apuntes a mano", builtin: true, pipeline });
    }
    // PATCH /api/presets upserts by id and never deletes (flow-issues.md
    // C7) — only the preset(s) that actually changed need to be sent.
    // `changedPresets` decides whether a PATCH is needed at all: a wizard
    // run that leaves "Apuntes a mano"'s pipeline exactly as it was must not
    // still write a byte-identical builtin override (see flow-issues.md
    // N12). The store is then refreshed from GET /api/presets.
    const changed = changedPresets(presets, list);
    if (changed.length > 0) await api.patchPresets({ presets: changed });
    setPresets(await api.getPresets());
    return APUNTES_PRESET_ID;
  }

  /** "Personalizado" never overwrites a builtin — it always creates (or
   * updates) the user's own "personalizado" preset instead. This is the fix
   * for the second root cause reported: the wizard used to write these
   * choices into "Apuntes a mano" directly, silently clobbering that
   * builtin's saved pipeline every time someone opened this card.
   *
   * A user can separately end up with their OWN preset already sitting at
   * the reserved id "personalizado" (e.g. Pipeline's "guardar como" flow
   * slugifies a first custom preset's default name the same way), later
   * renamed to something else entirely. Silently overwriting that preset's
   * pipeline just because it still lives at this id is exactly the "Optimo"
   * bug reported (flow-issues.md C7): the card's own copy promises this
   * never touches another saved preset. So only reuse the reserved id when
   * nothing is there yet, or what's there still looks wizard-authored (name
   * still "Personalizado"); otherwise branch to a fresh id instead of
   * clobbering the user's saved pipeline.
   *
   * Re-confirming step 2 within the SAME wizard run (Atrás → change
   * something → Continuar again) must PATCH the one preset this run already
   * created, never mint a sibling with the same name (flow-issues.md C17) —
   * `mintedPersonalizadoIdRef` remembers exactly that id for the rest of this
   * run. Only the very first save of a run falls through to the
   * id-selection heuristics below; a save whose (possibly renamed)
   * `customName` now collides with some OTHER already-saved preset reuses
   * that preset's id instead of creating a same-named duplicate. */
  async function savePersonalizadoPreset(): Promise<string> {
    const figureModel = customFig === "off" ? null : customFig;
    const reviewModel = customRev === "off" ? null : customRev;
    const pipeline: Pipeline = {
      ...DEFAULT_PIPELINE,
      ocr_model: customOcr,
      figure_model: figureModel,
      describe_figures: figureModel !== null,
      review_model: reviewModel,
    };
    const list = [...presets];
    const name = customName || "Personalizado";
    let id: string;
    if (mintedPersonalizadoIdRef.current) {
      id = mintedPersonalizadoIdRef.current;
    } else {
      const existingByName = list.find((p) => !p.builtin && p.name === name);
      if (existingByName) {
        id = existingByName.id;
      } else {
        const existing = list.find((p) => p.id === PERSONALIZADO_PRESET_ID);
        const safeToReuse = !existing || existing.name === "Personalizado";
        id = safeToReuse ? PERSONALIZADO_PRESET_ID : uniquePresetId(PERSONALIZADO_PRESET_ID, list);
      }
      mintedPersonalizadoIdRef.current = id;
    }
    const idx = list.findIndex((p) => p.id === id);
    const preset = { id, name, builtin: false, pipeline };
    if (idx >= 0) list[idx] = preset;
    else list.push(preset);
    // PATCH /api/presets upserts by id and never deletes (flow-issues.md
    // C7) — only the preset(s) that actually changed need to be sent.
    // `changedPresets` decides whether a PATCH is needed at all (N12). The
    // store is then refreshed from GET /api/presets.
    const changed = changedPresets(presets, list);
    if (changed.length > 0) await api.patchPresets({ presets: changed });
    setPresets(await api.getPresets());
    return id;
  }

  async function saveStep2AndContinue() {
    setSaving(true);
    setSaveError(null);
    try {
      let presetId: string;
      if (ocrChoice === "fast") {
        presetId = "rapido";
      } else if (ocrChoice === "local-ai") {
        presetId = await saveIntoApuntes({ ocr_model: localModel, figure_model: localModel, describe_figures: true });
      } else if (ocrChoice === "cloud-ai") {
        presetId = await saveIntoApuntes({
          ocr_model: "openai:gpt-4o",
          figure_model: "openai:gpt-4o",
          describe_figures: true,
          review_model: "openai:gpt-4o-mini",
        });
      } else {
        presetId = await savePersonalizadoPreset();
      }

      const body: Settings = {
        wizard_done: false,
        default_output_mode: outputMode,
        default_output_dir: outputMode === "fixed" ? outputDir : null,
        default_preset_id: presetId,
        default_mode: "once",
        // Read-modify-write: this PUT is a full replace, so an experimental
        // field the wizard doesn't ask about must still round-trip from
        // whatever GET /api/settings last answered, never reset to 1.
        local_gpu_permits: settingsCache?.local_gpu_permits ?? 1,
      };
      await api.putSettings(body);
      // "read back from the server after saving" — step 4 shows exactly
      // what GET /api/settings now answers, not what this component thinks
      // it just sent.
      const fresh = await api.getSettings();
      setSettingsCache(fresh);
      setEffectiveSettings(fresh);
      setSelectedPresetId(fresh.default_preset_id);
      setMode(fresh.default_mode);
      setStep(3);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function finish() {
    // `disabled={saving}` alone isn't a hard guard — a fast double-click can
    // fire this handler twice before the re-render that disables the button
    // commits, which is what actually produced the "two PUT /api/settings
    // from wizard finish" symptom in flow-issues.md N7, not a StrictMode
    // effect (there is no useEffect in this screen).
    if (finishingRef.current) return;
    finishingRef.current = true;
    setSaving(true);
    setSaveError(null);
    try {
      const base = effectiveSettings ?? settingsCache;
      const body: Settings = {
        wizard_done: true,
        default_output_mode: base?.default_output_mode ?? outputMode,
        default_output_dir: base?.default_output_dir ?? (outputMode === "fixed" ? outputDir : null),
        default_preset_id: base?.default_preset_id ?? "apuntes-a-mano",
        default_mode: base?.default_mode ?? "once",
        // Same read-modify-write rule as saveStep2AndContinue above.
        local_gpu_permits: base?.local_gpu_permits ?? 1,
      };
      const saved = await api.putSettings(body);
      setSettingsCache(saved);
      setOnboarded(true);
      navigate("/convertir");
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
      finishingRef.current = false;
    }
  }

  return (
    <div className="wiz">
      <div className="mainhead">
        <div>
          <div className="eyebrow">Primera vez</div>
          <h2>Configura 2markdown</h2>
        </div>
        <span className="sub">paso {step} de 4</span>
      </div>
      <div className="stepper">
        <span className={"s" + (step >= 1 ? " on" : "")} />
        <span className={"s" + (step >= 2 ? " on" : "")} />
        <span className={"s" + (step >= 3 ? " on" : "")} />
        <span className={"s" + (step >= 4 ? " on" : "")} />
      </div>

      {step === 1 && (
        <div style={{ maxWidth: 560 }}>
          <h2 style={{ fontSize: 22, marginBottom: 6 }}>¿Dónde dejamos lo convertido?</h2>
          <p className="sub" style={{ marginBottom: 18 }}>
            Esto es lo que Convertir usará por defecto en cada carpeta nueva — siempre editable para un trabajo concreto.
          </p>
          <div className="choices" role="radiogroup" aria-label="Dónde guardar por defecto" style={{ gridTemplateColumns: "1fr" }}>
            <div
              role="radio"
              aria-checked={outputMode === "sibling"}
              data-value="sibling"
              tabIndex={outputMode === "sibling" ? 0 : -1}
              className={"choice" + (outputMode === "sibling" ? " sel" : "")}
              onClick={() => setOutputMode("sibling")}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOutputMode("sibling");
                  return;
                }
                handleRadioGroupKeyDown(e, (v) => setOutputMode(v as OutputMode));
              }}
            >
              <div className="name">Junto al original</div>
              <p className="desc">
                Cada conversión crea una carpeta hermana con sufijo <code>_2markdown</code> dentro de la carpeta de entrada.
              </p>
            </div>
            <div
              role="radio"
              aria-checked={outputMode === "fixed"}
              data-value="fixed"
              tabIndex={outputMode === "fixed" ? 0 : -1}
              className={"choice" + (outputMode === "fixed" ? " sel" : "")}
              onClick={() => setOutputMode("fixed")}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOutputMode("fixed");
                  return;
                }
                handleRadioGroupKeyDown(e, (v) => setOutputMode(v as OutputMode));
              }}
            >
              <div className="name">Siempre en esta carpeta</div>
              <p className="desc">Todo lo convertido va a la misma carpeta, sin importar de dónde venga.</p>
              {outputMode === "fixed" && (
                <div className="pickrow" onClick={(e) => e.stopPropagation()}>
                  <input type="text" value={outputDir} onChange={(e) => setOutputDir(e.target.value)} />
                  <button
                    className="btn"
                    onClick={async (e) => {
                      e.stopPropagation();
                      const r = await api.pickFolder();
                      if (r.path) setOutputDir(r.path);
                    }}
                  >
                    Elegir…
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="wiz nav">
            <span>Puedes cambiarlo después en Ajustes.</span>
            <div className="r">
              <button className="btn primary" disabled={outputMode === "fixed" && !outputDir.trim()} onClick={() => setStep(2)}>
                Continuar
              </button>
            </div>
          </div>
        </div>
      )}

      {step === 2 && (
        <>
          <h2>¿Cómo quieres leer los documentos escaneados y a mano?</h2>
          <p className="sub" style={{ marginBottom: 22 }}>
            Los documentos con texto (Word, PDF normal, Excel) se convierten igual de bien con cualquiera. Esto sólo cambia
            las imágenes y los escaneos.
          </p>
          <div className="choices" role="radiogroup" aria-label="Cómo leer documentos escaneados">
            <div
              role="radio"
              aria-checked={ocrChoice === "fast"}
              data-value="fast"
              tabIndex={ocrChoice === "fast" ? 0 : -1}
              className={"choice" + (ocrChoice === "fast" ? " sel" : "")}
              onClick={() => setOcrChoice("fast")}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOcrChoice("fast");
                  return;
                }
                handleRadioGroupKeyDown(e, (v) => setOcrChoice(v as typeof ocrChoice));
              }}
            >
              <div className="name">
                Rápido, sin conexión{" "}
                <span className="pill ok">
                  <span className="dot" /> Listo
                </span>
              </div>
              <p className="desc">Ya viene incluido. Bien para texto impreso; flojo con letra a mano y fórmulas.</p>
              <div className="hw">
                <span>Coste: gratis · Velocidad: segundos por página</span>
                <span className="mono">Motor: Tesseract · eng+spa</span>
              </div>
              <div className="foot">
                <button type="button" className="btn ghost" onClick={() => setOcrChoice("fast")}>
                  Usar éste
                </button>
              </div>
            </div>

            <div
              role="radio"
              aria-checked={ocrChoice === "local-ai"}
              data-value="local-ai"
              tabIndex={ocrChoice === "local-ai" ? 0 : -1}
              className={"choice" + (ocrChoice === "local-ai" ? " sel" : "")}
              onClick={() => setOcrChoice("local-ai")}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOcrChoice("local-ai");
                  return;
                }
                handleRadioGroupKeyDown(e, (v) => setOcrChoice(v as typeof ocrChoice));
              }}
            >
              <div className="name">
                Con IA en este Mac <span className="pill hlp">Recomendado</span>
              </div>
              <p className="desc">Lee letra a mano y fórmulas. Todo se queda en tu ordenador.</p>
              <div className="hw">
                <span>
                  Detectado: <b>{system?.chip ?? "detectando…"} · {system ? fmtNumber(system.ram_gb) : "?"} GB</b>
                </span>
                <span className="mono">qwen2.5vl:7b · 5,6 GB · {rate7b} {localModel7bInstalled ? "✓ instalado" : ""}</span>
                <span className="mono" style={{ opacity: localModel32bInstalled ? 1 : 0.6 }}>
                  qwen2.5vl:32b · 20 GB · {rate32b} {localModel32bInstalled ? "✓ instalado" : ""}
                </span>
              </div>
              {localModel32bInstalled && (
                <div className="field" style={{ gap: 8 }} onClick={(e) => e.stopPropagation()}>
                  <label className="desc" style={{ fontSize: 12, display: "flex", gap: 12, alignItems: "center" }}>
                    <span>
                      <input
                        type="radio"
                        name="wiz-local-model"
                        checked={localModel === "ollama:qwen2.5vl:7b"}
                        onChange={() => setLocalModel("ollama:qwen2.5vl:7b")}
                      />{" "}
                      7b (rápido)
                    </span>
                    <span>
                      <input
                        type="radio"
                        name="wiz-local-model"
                        checked={localModel === "ollama:qwen2.5vl:32b"}
                        onChange={() => setLocalModel("ollama:qwen2.5vl:32b")}
                      />{" "}
                      32b (mejor calidad)
                    </span>
                  </label>
                </div>
              )}
              <div className="foot">
                {localModel7bInstalled ? (
                  <span className="pill ok">
                    <span className="dot" /> Instalado
                  </span>
                ) : (
                  <button className="btn primary" disabled={downloading} onClick={downloadModel}>
                    {downloading ? "Descargando…" : "Descargar 5,6 GB"}
                  </button>
                )}
              </div>
              <p className="desc" style={{ fontSize: 12, color: "var(--muted)" }}>
                Instala Ollama por ti si no lo tienes. Elegir esta opción edita el preajuste «Apuntes a mano» (lectura y
                figuras con este modelo).
              </p>
              {downloadError && (
                <p className="desc" style={{ fontSize: 12, color: "var(--bad)" }}>
                  {downloadError}
                </p>
              )}
            </div>

            <div
              role="radio"
              aria-checked={ocrChoice === "cloud-ai"}
              data-value="cloud-ai"
              tabIndex={ocrChoice === "cloud-ai" ? 0 : -1}
              className={"choice" + (ocrChoice === "cloud-ai" ? " sel" : "")}
              onClick={() => setOcrChoice("cloud-ai")}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOcrChoice("cloud-ai");
                  return;
                }
                handleRadioGroupKeyDown(e, (v) => setOcrChoice(v as typeof ocrChoice));
              }}
            >
              <div className="name">Con IA en la nube</div>
              <p className="desc">La mejor calidad con letra a mano. Unos céntimos por página; las páginas salen de tu ordenador.</p>
              <div className="field" style={{ gap: 8 }}>
                <input
                  type="password"
                  placeholder="sk-…"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  style={{ border: "none", background: "transparent", flex: 1, font: "inherit" }}
                />
                <button
                  className="btn ghost small"
                  disabled={keyChecking || !apiKey}
                  onClick={checkKey}
                  style={{ color: keyOk === false ? "var(--bad)" : "var(--ok)" }}
                >
                  {keyChecking ? "…" : "Comprobar"}
                </button>
              </div>
              {keyOk === false && <span style={{ fontSize: 12, color: "var(--bad)" }}>No se pudo validar la clave.</span>}
              {system?.cloud.openai.status === "out_of_credit" && (
                <span style={{ fontSize: 12, color: "var(--warn)" }}>La cuenta guardada tiene el saldo agotado.</span>
              )}
              <div className="hw">
                <span className="mono">openai:gpt-4o · ~1 ¢/página</span>
              </div>
              <p className="desc" style={{ fontSize: 12, color: "var(--muted)" }}>
                Elegir esta opción edita el preajuste «Apuntes a mano» (lectura, figuras y revisión en la nube).
              </p>
              <div className="foot">
                <button type="button" className="btn ghost" onClick={() => setOcrChoice("cloud-ai")}>
                  Usar éste
                </button>
              </div>
            </div>

            <div
              role="radio"
              aria-checked={ocrChoice === "custom"}
              data-value="custom"
              tabIndex={ocrChoice === "custom" ? 0 : -1}
              className={"choice" + (ocrChoice === "custom" ? " sel" : "")}
              onClick={() => setOcrChoice("custom")}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOcrChoice("custom");
                  return;
                }
                handleRadioGroupKeyDown(e, (v) => setOcrChoice(v as typeof ocrChoice));
              }}
            >
              <div className="name">Personalizado</div>
              <p className="desc">Elige el modelo de cada etapa. Crea tu propio preajuste — no toca «Apuntes a mano».</p>
              <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 8 }}>
                <label className="desc" style={{ fontSize: 12, display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 4 }}>
                  Nombre del preajuste
                  <input
                    type="text"
                    className="pick"
                    value={customName}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      setOcrChoice("custom");
                      setCustomName(e.target.value);
                    }}
                  />
                </label>
                <label className="desc" style={{ fontSize: 12, display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 4 }}>
                  Leer páginas escaneadas (OCR)
                  <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                    <ModelPicker
                      stage="ocr"
                      value={customOcr}
                      onChange={(v) => {
                        setOcrChoice("custom");
                        setCustomOcr(v as string);
                      }}
                    />
                  </div>
                </label>
                <label className="desc" style={{ fontSize: 12, display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 4 }}>
                  Describir figuras
                  <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                    <ModelPicker
                      stage="figures"
                      allowOff
                      offLabel="No describir"
                      value={customFig}
                      onChange={(v) => {
                        setOcrChoice("custom");
                        setCustomFig(v);
                      }}
                    />
                  </div>
                </label>
                <label className="desc" style={{ fontSize: 12, display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 4 }}>
                  Revisar erratas
                  <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                    <ModelPicker
                      stage="review"
                      allowOff
                      offLabel="Sin revisión"
                      value={customRev}
                      onChange={(v) => {
                        setOcrChoice("custom");
                        setCustomRev(v);
                      }}
                    />
                  </div>
                </label>
              </div>
              {customBlocked && (
                <p className="desc" style={{ fontSize: 12, color: "var(--bad)" }}>
                  {customBlocked.body}
                </p>
              )}
              {!customBlocked && customWarning && (
                <p className="desc" style={{ fontSize: 12, color: "var(--warn)" }}>
                  <b>{customWarning.title}.</b> {customWarning.body}
                </p>
              )}
              <div className="foot">
                <button
                  type="button"
                  className="btn ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    setOcrChoice("custom");
                  }}
                >
                  Usar éste
                </button>
              </div>
            </div>
          </div>
          {saveError && (
            <p className="desc" style={{ fontSize: 12, color: "var(--bad)" }}>
              No se pudo guardar: {saveError}
            </p>
          )}
          <div className="wiz nav">
            <span>Puedes cambiarlo después en Ajustes.</span>
            <div className="r">
              <button className="btn ghost" onClick={() => setStep(1)}>
                Atrás
              </button>
              <button
                className="btn primary"
                disabled={saving || (ocrChoice === "custom" && !!customBlocked)}
                onClick={saveStep2AndContinue}
              >
                {saving ? "Guardando…" : "Continuar"}
              </button>
            </div>
          </div>
        </>
      )}

      {step === 3 && (
        <CloudStep onBack={() => setStep(2)} onContinue={() => setStep(4)} />
      )}

      {step === 4 && (
        <div style={{ maxWidth: 520 }}>
          <h2 style={{ fontSize: 22, marginBottom: 6 }}>Listo</h2>
          <p className="sub" style={{ marginBottom: 14 }}>
            {/* Points at a real destination: the "Ajustes" screen's own
                "Ajustes" sub-tab (SettingsTab, tab === "ajustes") — the
                previous "Equipo y sincronización › Ajustes" named a tab that
                doesn't exist, and the following literal fix ("Ajustes ›
                Ajustes") still read as a confusing duplicate (see
                flow-issues.md N10). */}
            Esto es exactamente lo que Convertir usará por defecto — puedes cambiarlo en cualquier momento en Ajustes,
            en la pestaña «Ajustes».
          </p>
          {effectiveSettings && <SettingsSummary settings={effectiveSettings} presets={presets} />}
          {saveError && (
            <p className="desc" style={{ fontSize: 12, color: "var(--bad)" }}>
              No se pudo guardar: {saveError}
            </p>
          )}
          <div className="wiz nav">
            <span />
            <div className="r">
              <button className="btn ghost" onClick={() => setStep(3)}>
                Atrás
              </button>
              <button className="btn primary" disabled={saving} onClick={finish}>
                {saving ? "…" : "Empezar a convertir →"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Wizard step 3 — "Conecta servicios en la nube (opcional)". One masked
 * key input + Comprobar per provider (POST /api/cloud/{provider}/key),
 * status shown in Spanish, never the env var name. "Saltar" (and
 * "Continuar" once at least nothing is broken) both move on — no provider
 * is required to finish the wizard. */
function CloudStep({ onBack, onContinue }: { onBack: () => void; onContinue: () => void }) {
  const system = useAppStore((s) => s.system);
  const setSystem = useAppStore((s) => s.setSystem);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [checking, setChecking] = useState<CloudProviderId | null>(null);

  async function check(provider: CloudProviderId) {
    const key = (keys[provider] ?? "").trim();
    if (!key) return;
    setChecking(provider);
    try {
      await api.saveCloudKey(provider, key);
      const fresh = await api.getSystem();
      setSystem(fresh);
      setKeys((prev) => ({ ...prev, [provider]: "" }));
    } finally {
      setChecking(null);
    }
  }

  function statusLabel(status?: string): string {
    if (status === "ok") return "clave guardada";
    if (status === "out_of_credit") return "saldo agotado";
    if (status === "invalid_key") return "clave inválida";
    if (status === "unknown") return "no comprobada";
    return "falta la clave";
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <h2 style={{ fontSize: 22, marginBottom: 6 }}>Conecta servicios en la nube (opcional)</h2>
      <p className="sub" style={{ marginBottom: 18 }}>
        Sólo hace falta si quieres usar un modelo en la nube para leer, describir figuras o revisar erratas. Puedes
        añadirlas más tarde en Ajustes › Tu equipo.
      </p>
      <div className="choices" style={{ gridTemplateColumns: "1fr" }}>
        {CLOUD_PROVIDERS.map((id) => {
          const info = system?.cloud[id];
          return (
            <div className="choice" key={id} style={{ cursor: "default" }}>
              <div className="name">
                {PROVIDER_LABELS[id]}{" "}
                {info?.key_present && (
                  <span className={"pill " + (info.status === "ok" ? "ok" : "warn")}>
                    <span className="dot" /> {statusLabel(info.status)}
                  </span>
                )}
              </div>
              <p className="desc">{PROVIDER_BLURBS[id]}</p>
              <div className="field" style={{ gap: 8 }}>
                <input
                  type="password"
                  placeholder="Clave de API"
                  value={keys[id] ?? ""}
                  onChange={(e) => setKeys((prev) => ({ ...prev, [id]: e.target.value }))}
                  style={{ border: "none", background: "transparent", flex: 1, font: "inherit" }}
                />
                <button className="btn ghost small" disabled={checking === id || !keys[id]?.trim()} onClick={() => check(id)}>
                  {checking === id ? "…" : "Comprobar"}
                </button>
              </div>
            </div>
          );
        })}
      </div>
      <div className="wiz nav">
        <span>Puedes añadir o quitar claves después en Ajustes › Tu equipo.</span>
        <div className="r">
          <button className="btn ghost" onClick={onBack}>
            Atrás
          </button>
          <button className="btn ghost" onClick={onContinue}>
            Saltar
          </button>
          <button className="btn primary" onClick={onContinue}>
            Continuar
          </button>
        </div>
      </div>
    </div>
  );
}

/** Matches the wording this screen has always used ("~40 s/página",
 * "~2,5 min/página") but computed from lib/scheduling.ts's MODELS instead of
 * typed twice. */
function formatSecondsPerPage(seconds: number | undefined): string {
  if (seconds == null) return "";
  if (seconds < 60) return `~${Math.round(seconds)} s/página`;
  return `~${fmtNumber(seconds / 60, 1)} min/página`;
}
