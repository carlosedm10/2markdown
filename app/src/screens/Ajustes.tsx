import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAppStore } from "../store/useAppStore";
import { api } from "../api";
import type {
  BlockedInfo,
  CloudProviderId,
  DefaultMode,
  FolderInfo,
  InspectResponse,
  OutputMode,
  Preset,
  Settings,
  SystemInfo,
  Sync,
} from "../api/types";
import { fmtNumber, plural } from "../lib/format";
import { GPU_MAX_GB, gpuState } from "../lib/scheduling";

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  groq: "Groq",
  mistral: "Mistral",
  openrouter: "OpenRouter",
};

/** The one-line "para qué sirve" blurb — same copy the wizard's cloud step
 * shows, so the two never drift apart (see Wizard.tsx). */
export const PROVIDER_BLURBS: Record<CloudProviderId, string> = {
  openai: "GPT-4o para leer, describir figuras y revisar erratas.",
  anthropic: "Claude — muy bueno con letra difícil y tablas.",
  google: "Gemini — rápido y barato para lotes grandes.",
  groq: "Llama a máxima velocidad, precio bajo.",
  mistral: "Modelos europeos, buena relación calidad-precio.",
  openrouter: "Cientos de modelos distintos con una sola clave.",
};

export const CLOUD_PROVIDERS: CloudProviderId[] = ["openai", "anthropic", "google", "groq", "mistral", "openrouter"];

function providerLabel(id: string): string {
  return PROVIDER_LABELS[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

/** /api/system only ever lists models Ollama already has on disk — there is
 * no "available to download" entry in that list — so every model it returns
 * is installed. `loaded` is a separate, secondary signal: whether it is
 * currently resident in VRAM, not whether it exists at all. */
function fmtModelName(name: string): string {
  return name.startsWith("ollama:") ? name : `ollama:${name}`;
}

/** Fallback ONLY for a server old enough that /api/system's OllamaModelInfo
 * doesn't carry `vision` yet (see api/types.ts) — infer vision vs. text from
 * the model family name. */
function isVisionModel(name: string): boolean {
  const bare = name.replace(/^ollama:/, "");
  if (/vl|vision|llava|(^|[:_-])v(?=[:_-]|$)/i.test(bare)) return true;
  if (/^gemma3(?!:?1b(?:$|[:-]))(:|$)/i.test(bare)) return true;
  return false;
}

/** Spanish label for a per-provider key-check status — never shows the raw
 * OpenAIStatus enum value or an env var name (see the restructure brief). */
function statusLabel(status?: string): string {
  if (status === "ok") return "clave válida";
  if (status === "out_of_credit") return "sin saldo";
  if (status === "invalid_key") return "clave inválida";
  if (status === "no_key") return "falta la clave";
  if (status === "unknown") return "no comprobada";
  return "falta la clave";
}
function statusPillClass(status?: string): string {
  if (status === "ok") return "ok";
  if (status === "out_of_credit" || status === "invalid_key") return "warn";
  return "mute";
}

type Tab = "equipo" | "presets" | "syncs" | "ajustes" | "avanzado";

export default function Ajustes() {
  const system = useAppStore((s) => s.system);
  const setSystem = useAppStore((s) => s.setSystem);
  const presets = useAppStore((s) => s.presets);
  const setPresets = useAppStore((s) => s.setPresets);
  const startEditPreset = useAppStore((s) => s.startEditPreset);
  const selectedPresetId = useAppStore((s) => s.selectedPresetId);
  const setSelectedPresetId = useAppStore((s) => s.setSelectedPresetId);
  const settings = useAppStore((s) => s.settings);
  const setSettings = useAppStore((s) => s.setSettings);
  const setOnboarded = useAppStore((s) => s.setOnboarded);
  const inspect = useAppStore((s) => s.inspect);
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>("equipo");
  const [syncs, setSyncs] = useState<Sync[]>([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [folders, setFolders] = useState<FolderInfo[]>([]);
  const [forgetting, setForgetting] = useState<string | null>(null);

  useEffect(() => {
    api.getSyncs().then(setSyncs);
  }, []);

  useEffect(() => {
    api
      .getFolders()
      .then(setFolders)
      .catch(() => setFolders([]));
  }, []);

  // "Quitar" (flow-issues.md C17) — builtins never get the button at all
  // (see the render below), so this only ever runs for a user preset.
  // Falls the selection away from the deleted preset first (never leaves
  // `selectedPresetId`/the default pointing at something GET /api/presets
  // no longer returns) before the delete, then refreshes from the server's
  // own list rather than filtering the local copy by hand.
  const [deletingPreset, setDeletingPreset] = useState<string | null>(null);
  async function removePreset(p: Preset) {
    if (p.builtin) return;
    if (!window.confirm(`¿Quitar el preajuste «${p.name}»? Esta acción no se puede deshacer.`)) return;
    setDeletingPreset(p.id);
    try {
      const remaining = presets.filter((x) => x.id !== p.id);
      const fallbackId = remaining.find((x) => x.id === "apuntes-a-mano")?.id ?? remaining[0]?.id ?? "apuntes-a-mano";
      if (selectedPresetId === p.id) setSelectedPresetId(fallbackId);
      const fresh = await api.deletePreset(p.id);
      setPresets(fresh);
      if (settings?.default_preset_id === p.id) {
        const updated = await api.putSettings({ ...settings, default_preset_id: fallbackId });
        setSettings(updated);
      }
    } finally {
      setDeletingPreset(null);
    }
  }

  async function forgetFolder(path: string) {
    setForgetting(path);
    try {
      await api.forgetFolder(path);
      setFolders((prev) => prev.filter((f) => f.path !== path));
    } finally {
      setForgetting(null);
    }
  }

  // Poll while this tab is visible so a sync that ran in the background
  // (drop a file in the watched folder, wait) shows up without a full page
  // reload — the list was previously fetched once on mount and never again.
  // Only while the "Sincronizaciones y carpetas" sub-tab is actually open —
  // this used to poll from every sub-tab of Ajustes (flow-issues.md N7).
  useEffect(() => {
    if (tab !== "syncs") return;
    const id = setInterval(() => {
      if (document.visibilityState === "visible") api.getSyncs().then(setSyncs);
    }, 4000);
    return () => clearInterval(id);
  }, [tab]);

  async function toggleSync(s: Sync) {
    const updated = await api.patchSync(s.id, { enabled: !s.enabled });
    setSyncs((prev) => prev.map((x) => (x.id === s.id ? updated : x)));
  }

  async function removeSync(s: Sync) {
    if (!window.confirm(`¿Eliminar la sincronización de «${s.input}»? Dejará de vigilar la carpeta.`)) return;
    setDeleting(s.id);
    try {
      await api.deleteSync(s.id);
      setSyncs((prev) => prev.filter((x) => x.id !== s.id));
    } finally {
      setDeleting(null);
    }
  }

  const [advancedResetNote, setAdvancedResetNote] = useState<string | null>(null);

  function resetAdvanced() {
    setAdvancedResetNote("Aún no disponible: falta un endpoint de configuración en el servidor.");
  }

  const unknownCount = system?.prices.unknown_models.length ?? 0;

  return (
    <div className="set">
      <nav>
        <a className={tab === "equipo" ? "cur" : ""} onClick={() => setTab("equipo")}>
          Tu equipo
        </a>
        <a className={tab === "presets" ? "cur" : ""} onClick={() => setTab("presets")}>
          Preajustes
        </a>
        <a className={tab === "syncs" ? "cur" : ""} onClick={() => setTab("syncs")}>
          Sincronizaciones y carpetas
        </a>
        <a className={tab === "ajustes" ? "cur" : ""} onClick={() => setTab("ajustes")}>
          Ajustes
        </a>
        <a className={tab === "avanzado" ? "cur" : ""} onClick={() => setTab("avanzado")}>
          Avanzado
        </a>
      </nav>

      {tab === "equipo" && (
        <div className="group">
          <h4>Lo que tienes en este Mac</h4>
          <div className="kit">
            <div className="it">
              <div>
                <b>Motor rápido · Tesseract</b>
                <span>Incluido en la app{system?.tesseract.version ? ` · v${system.tesseract.version}` : ""}</span>
              </div>
              <span className={"pill " + (system?.tesseract.installed ? "ok" : "bad")}>
                <span className="dot" /> {system?.tesseract.installed ? "Listo" : "Sin instalar"}
              </span>
            </div>
            <div className="it">
              <div>
                <b>Ollama</b>
                <span>
                  {system?.ollama.running ? `v${system.ollama.version} · en marcha` : "no detectado"} ·{" "}
                  {plural(system?.ollama.models.filter((m) => m.loaded).length ?? 0, "modelo cargado", "modelos cargados")}
                </span>
              </div>
              <span className={"pill " + (system?.ollama.running ? "ok" : "bad")}>
                <span className="dot" /> {system?.ollama.running ? "Listo" : "Sin instalar"}
              </span>
            </div>
            {system?.ollama.models.map((m) => {
              const isHeuristic = m.vision === undefined;
              const vision = isHeuristic ? isVisionModel(m.name) : m.vision;
              return (
                <div className="it" key={m.name}>
                  <div>
                    <b>{fmtModelName(m.name)}</b>
                    <span>
                      {fmtNumber(m.size_gb)} GB · {vision ? "modelo de visión local" : "modelo de texto local"}
                      {isHeuristic && (
                        <span
                          title="Estimado por el nombre del modelo: el servidor todavía no informa si es de visión o de texto."
                          style={{ color: "var(--muted)" }}
                        >
                          {" "}
                          (estimado)
                        </span>
                      )}
                      {m.loaded ? " · en memoria" : ""}
                    </span>
                  </div>
                  <span className="pill ok">
                    <span className="dot" /> Instalado
                  </span>
                </div>
              );
            })}
            <div className="it" style={{ gridColumn: "1/-1" }}>
              <div>
                <b>Memoria</b>
                <span>
                  {system ? fmtNumber(system.ram_gb) : "?"} GB · GPU hasta ~{system ? fmtNumber(system.gpu_limit_gb) : "?"} GB
                </span>
              </div>
              <span className="pill mute">ok</span>
            </div>
            <div className="it" style={{ gridColumn: "1/-1" }}>
              <div>
                <b>Precios de modelos · USD</b>
                <span>
                  genai-prices
                  {unknownCount > 0
                    ? ` · ${unknownCount} modelo(s) sin precio: ${system?.prices.unknown_models.join(", ")}`
                    : " · todos con precio"}
                </span>
              </div>
              <span className={"pill " + (unknownCount > 0 ? "warn" : "ok")}>
                <span className="dot" /> {unknownCount > 0 ? `${unknownCount} sin precio` : "al día"}
              </span>
            </div>
          </div>

          <h4 style={{ marginTop: 22 }}>Servicios en la nube</h4>
          <p style={{ color: "var(--ink-2)", fontSize: 13, maxWidth: 560, marginBottom: 4 }}>
            Añade la clave de cada proveedor que quieras usar en un preajuste. Nunca se muestra en texto plano una vez
            guardada.
          </p>
          <div className="kit">
            {CLOUD_PROVIDERS.map((id) => (
              <CloudProviderRow key={id} provider={id} system={system} onChanged={setSystem} />
            ))}
          </div>
        </div>
      )}

      {tab === "presets" && (
        <div className="group">
          <h4>Preajustes</h4>
          {presets.map((p) => (
            <div
              className="it"
              key={p.id}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto auto",
                alignItems: "center",
                gap: 8,
                width: "100%",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <b>{p.name}</b>
                <span className={"pill " + (p.builtin ? "mute" : "hlp")}>{p.builtin ? "de serie" : "personalizado"}</span>
              </div>
              {!p.builtin && (
                <button
                  type="button"
                  className="btn ghost small"
                  disabled={deletingPreset === p.id}
                  onClick={() => removePreset(p)}
                >
                  {deletingPreset === p.id ? "Quitando…" : "Quitar"}
                </button>
              )}
              {p.builtin && <span />}
              <button
                type="button"
                className="btn ghost small"
                onClick={() => {
                  startEditPreset(p.id, p.pipeline);
                  navigate("/pipeline");
                }}
              >
                Editar ▸
              </button>
            </div>
          ))}
        </div>
      )}

      {tab === "syncs" && (
        <>
          <div className="group">
            <h4>Carpetas sincronizadas</h4>
            {syncs.map((s) => (
              <div className="sync" key={s.id}>
                <div className="paths">
                  <span className="p">
                    <b>Vigila</b>
                    {s.input}
                  </span>
                  <span className="p">
                    <b>Escribe en</b>
                    {s.output}
                  </span>
                  <small>
                    Preajuste «{presets.find((p) => p.id === s.preset_id)?.name ?? s.preset_id}»
                    {s.last_run ? ` · última conversión ${timeAgo(s.last_run)}` : ""} · {plural(s.files_today, "archivo")} hoy
                  </small>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <button
                    className={"toggle" + (s.enabled ? " on" : "")}
                    role="switch"
                    aria-checked={s.enabled}
                    aria-label={s.enabled ? "desactivar sincronización" : "activar sincronización"}
                    onClick={() => toggleSync(s)}
                  />
                  <button
                    className="btn ghost small"
                    disabled={deleting === s.id}
                    onClick={() => removeSync(s)}
                    aria-label={`Eliminar sincronización de ${s.input}`}
                  >
                    {deleting === s.id ? "…" : "Eliminar"}
                  </button>
                </div>
              </div>
            ))}
            <button type="button" className="sync" style={{ borderStyle: "dashed" }} onClick={() => setDialogOpen(true)}>
              <div className="paths">
                <span className="p" style={{ color: "var(--muted)" }}>
                  Añadir carpeta sincronizada…
                </span>
                <small>Elige carpeta a vigilar, carpeta de salida y preajuste</small>
              </div>
              <span />
            </button>
          </div>

          <div className="group">
            <h4>Carpetas recordadas</h4>
            <p style={{ color: "var(--ink-2)", fontSize: 13, maxWidth: 560, marginBottom: 4 }}>
              <b>Sincronización</b> vigila una carpeta y convierte lo nuevo sola. <b>Carpeta recordada</b> sólo recuerda qué
              preajuste usaste ahí la última vez, en Convertir — no vigila ni convierte nada por su cuenta.
            </p>
            {folders.length === 0 && <p style={{ color: "var(--muted)", fontSize: 12.5 }}>Aún no se recuerda ninguna carpeta.</p>}
            {folders.map((f) => (
              <div className="sync" key={f.path}>
                <div className="paths">
                  <span className="p">
                    <b>Carpeta</b>
                    {f.path}
                  </span>
                  <small>
                    Preajuste «{presets.find((p) => p.id === f.preset_id)?.name ?? f.preset_id}»
                    {f.last_used ? ` · última conversión ${timeAgo(f.last_used)}` : ""}
                  </small>
                </div>
                <button
                  className="btn ghost small"
                  disabled={forgetting === f.path}
                  onClick={() => forgetFolder(f.path)}
                  aria-label={`Olvidar carpeta ${f.path}`}
                >
                  {forgetting === f.path ? "…" : "Olvidar"}
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === "ajustes" && (
        <SettingsTab
          settings={settings}
          presets={presets}
          onSaved={(s) => setSettings(s)}
          onRerunWizard={() => {
            setOnboarded(false);
            navigate("/primera-vez");
          }}
        />
      )}

      {tab === "avanzado" && (
        <div className="group">
          <h4>Avanzado</h4>
          <p style={{ color: "var(--ink-2)", fontSize: 13, maxWidth: 480 }}>
            Expone <code>twomarkdown/config.py</code> tal cual: DPI, idioma, timeouts, <code>emit_chunks</code>. No hace
            falta tocarlo para el caso normal.
          </p>
          <button className="btn ghost small" style={{ width: "fit-content" }} onClick={resetAdvanced}>
            Restablecer
          </button>
          {advancedResetNote && <p style={{ color: "var(--muted)", fontSize: 12 }}>{advancedResetNote}</p>}
          <GpuPermitsControl settings={settings} presets={presets} system={system} inspect={inspect} onSaved={setSettings} />
        </div>
      )}

      {dialogOpen && <AddSyncDialog onClose={() => setDialogOpen(false)} onCreated={(s) => setSyncs((prev) => [...prev, s])} />}
    </div>
  );
}

/** One provider row with a masked key input, Guardar/Quitar clave, and the
 * "para qué sirve" blurb — the same list the wizard's cloud step shows (see
 * Wizard.tsx). Never displays an env var name. */
function CloudProviderRow({
  provider,
  system,
  onChanged,
}: {
  provider: CloudProviderId;
  system: ReturnType<typeof useAppStore.getState>["system"];
  onChanged: (s: NonNullable<ReturnType<typeof useAppStore.getState>["system"]>) => void;
}) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const info = system?.cloud[provider];
  const keyPresent = info?.key_present ?? false;

  async function save() {
    if (!key.trim()) return;
    setBusy(true);
    try {
      // Render straight from what the probe just told us — a `getSystem()`
      // refetch here can race the server's own cached cloud-status update
      // and show "no comprobada" even though the key was just validated as
      // invalid (see flow-issues.md C6).
      const result = await api.saveCloudKey(provider, key.trim());
      setKey("");
      if (system) onChanged({ ...system, cloud: { ...system.cloud, [provider]: result } });
      else onChanged(await api.getSystem());
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      const result = await api.removeCloudKey(provider);
      if (system) onChanged({ ...system, cloud: { ...system.cloud, [provider]: result } });
      else onChanged(await api.getSystem());
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="it" style={{ gridColumn: "1/-1" }}>
      <div style={{ flex: 1 }}>
        <b>{providerLabel(provider)}</b>
        <span>{PROVIDER_BLURBS[provider]}</span>
        {!keyPresent && (
          <div className="pickrow" style={{ marginTop: 6 }}>
            <input
              type="password"
              placeholder="Clave de API"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && save()}
              style={{ flex: 1 }}
            />
            <button className="btn small" disabled={busy || !key.trim()} onClick={save}>
              {busy ? "…" : "Añadir clave"}
            </button>
          </div>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
        <span className={"pill " + statusPillClass(info?.status)}>
          <span className="dot" /> {statusLabel(info?.status)}
        </span>
        {keyPresent && (
          <button className="btn ghost small" disabled={busy} onClick={remove}>
            {busy ? "…" : "Quitar clave"}
          </button>
        )}
      </div>
    </div>
  );
}

/** Ajustes › Ajustes — edits the exact same `Settings` the wizard writes on
 * first run (GET/PUT /api/settings, full replace). This is the escape hatch
 * for "the wizard is the first edit of settings, not the only one":
 * everything chosen here is what Convertir (and a future wizard run) will
 * read as the default from now on. */
function SettingsTab({
  settings,
  presets,
  onSaved,
  onRerunWizard,
}: {
  settings: Settings | null;
  presets: { id: string; name: string }[];
  onSaved: (s: Settings) => void;
  onRerunWizard: () => void;
}) {
  const [outputMode, setOutputMode] = useState<OutputMode>(settings?.default_output_mode ?? "sibling");
  const [outputDir, setOutputDir] = useState(settings?.default_output_dir ?? "");
  const [defaultPresetId, setDefaultPresetId] = useState(settings?.default_preset_id ?? presets[0]?.id ?? "");
  const [defaultMode, setDefaultMode] = useState<DefaultMode>(settings?.default_mode ?? "once");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [rerunning, setRerunning] = useState(false);

  async function save() {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const body: Settings = {
        wizard_done: settings?.wizard_done ?? true,
        default_output_mode: outputMode,
        default_output_dir: outputMode === "fixed" ? outputDir : null,
        default_preset_id: defaultPresetId,
        default_mode: defaultMode,
        // This tab doesn't edit GPU permits — that's Avanzado's control —
        // but PUT is a full replace, so carry the current value through
        // instead of silently resetting it to 1.
        local_gpu_permits: settings?.local_gpu_permits ?? 1,
      };
      const result = await api.putSettings(body);
      onSaved(result);
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function rerunWizard() {
    setRerunning(true);
    try {
      const body: Settings = {
        wizard_done: false,
        default_output_mode: outputMode,
        default_output_dir: outputMode === "fixed" ? outputDir : null,
        default_preset_id: defaultPresetId,
        default_mode: defaultMode,
        local_gpu_permits: settings?.local_gpu_permits ?? 1,
      };
      const result = await api.putSettings(body);
      onSaved(result);
      onRerunWizard();
    } finally {
      setRerunning(false);
    }
  }

  return (
    <div className="group">
      <h4>Ajustes por defecto</h4>
      <p style={{ color: "var(--ink-2)", fontSize: 13, maxWidth: 560, marginBottom: 4 }}>
        Lo mismo que preguntó el asistente la primera vez. Cambiarlo aquí afecta a la siguiente carpeta que abras en
        Convertir, no a los trabajos ya en marcha.
      </p>
      <div className="row" style={{ display: "grid", gap: 6, maxWidth: 480 }}>
        <label>Dónde guardar</label>
        <select className="pick" value={outputMode} onChange={(e) => setOutputMode(e.target.value as OutputMode)}>
          <option value="sibling">Junto al original (carpeta hermana _2markdown)</option>
          <option value="fixed">Siempre en una carpeta fija</option>
        </select>
        {outputMode === "fixed" && (
          <div className="pickrow">
            <input type="text" value={outputDir} onChange={(e) => setOutputDir(e.target.value)} placeholder="~/Documentos/2markdown" />
            <button
              className="btn small"
              onClick={async () => {
                const r = await api.pickFolder();
                if (r.path) setOutputDir(r.path);
              }}
            >
              Elegir…
            </button>
          </div>
        )}
      </div>
      <div className="row" style={{ display: "grid", gap: 6, maxWidth: 480, marginTop: 12 }}>
        <label>Preajuste por defecto</label>
        <select className="pick" value={defaultPresetId} onChange={(e) => setDefaultPresetId(e.target.value)}>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="row" style={{ display: "grid", gap: 6, maxWidth: 480, marginTop: 12 }}>
        <label>Modo por defecto</label>
        <select className="pick" value={defaultMode} onChange={(e) => setDefaultMode(e.target.value as DefaultMode)}>
          <option value="once">Una vez</option>
          <option value="sync">Mantener sincronizada</option>
        </select>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 14 }}>
        <button className="btn primary" disabled={saving} onClick={save}>
          {saving ? "Guardando…" : "Guardar"}
        </button>
        {saved && <span style={{ fontSize: 12, color: "var(--ok)" }}>Guardado.</span>}
        {saveError && <span style={{ fontSize: 12, color: "var(--bad)" }}>No se pudo guardar: {saveError}</span>}
      </div>
      <div style={{ marginTop: 24, borderTop: "1px solid var(--line-soft)", paddingTop: 14 }}>
        <button className="btn ghost" disabled={rerunning} onClick={rerunWizard}>
          {rerunning ? "…" : "Volver a ejecutar la configuración inicial"}
        </button>
      </div>
    </div>
  );
}

/** Ajustes › Avanzado's "Permisos GPU (experimental)" control — the only UI
 * for `Settings.local_gpu_permits` (mirrors `twomarkdown.config.llm_config.
 * local_gpu_permits`/`agents/image_ocr.py`'s `_page_ocr_lock`; see
 * docs/desktop-app.md's GPU-permits note). Saving is a read-modify-write PUT
 * /api/settings — it spreads the current `settings` object and only patches
 * this one field, so nothing else on the resource gets clobbered.
 *
 * "2" is disabled whenever the default preset's local models wouldn't fit
 * two residents: this asks `POST /api/estimate` for the authoritative
 * verdict when a folder has already been inspected this session (`inspect`
 * from the store), and otherwise falls back to the same client-side
 * arithmetic (`lib/scheduling.ts`'s `gpuState`, `RESIDENT_FACTOR`-scaled)
 * that /api/estimate itself mirrors. */
function GpuPermitsControl({
  settings,
  presets,
  system,
  inspect,
  onSaved,
}: {
  settings: Settings | null;
  presets: Preset[];
  system: SystemInfo | null;
  inspect: InspectResponse | null;
  onSaved: (s: Settings) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [blockedTwo, setBlockedTwo] = useState<BlockedInfo | null>(null);

  const permits = settings?.local_gpu_permits ?? 1;
  const defaultPreset = presets.find((p) => p.id === settings?.default_preset_id);
  const gpuLimit = system?.gpu_limit_gb || GPU_MAX_GB;

  useEffect(() => {
    let cancelled = false;
    async function check() {
      if (!defaultPreset) {
        setBlockedTwo(null);
        return;
      }
      const { ocr_model, describe_figures, figure_model, review_model } = defaultPreset.pipeline;
      if (inspect) {
        try {
          const est = await api.estimate({ inspect_id: inspect.inspect_id, pipeline: defaultPreset.pipeline });
          if (!cancelled) setBlockedTwo(est.blocked);
          return;
        } catch {
          // Fall through to the client-side estimate below (e.g. the
          // inspect_id expired since it was cached).
        }
      }
      const { blocked } = gpuState(ocr_model, describe_figures ? figure_model : null, review_model, gpuLimit);
      if (!cancelled) setBlockedTwo(blocked);
    }
    check();
    return () => {
      cancelled = true;
    };
  }, [defaultPreset, inspect, gpuLimit]);

  async function choose(next: number) {
    if (next === permits || saving) return;
    if (next === 2 && blockedTwo) return; // belt-and-suspenders — the button is already disabled
    if (!settings) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const body: Settings = { ...settings, local_gpu_permits: next };
      const result = await api.putSettings(body);
      onSaved(result);
      setSaved(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ marginTop: 24, borderTop: "1px solid var(--line-soft)", paddingTop: 14, maxWidth: 480 }}>
      <label style={{ display: "block", marginBottom: 6 }}>Permisos GPU (experimental)</label>
      <div role="radiogroup" aria-label="Permisos GPU" style={{ display: "inline-flex", gap: 4 }}>
        <button
          type="button"
          role="radio"
          aria-checked={permits === 1}
          className={"btn small" + (permits === 1 ? " primary" : " ghost")}
          disabled={saving}
          onClick={() => choose(1)}
        >
          1
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={permits === 2}
          className={"btn small" + (permits === 2 ? " primary" : " ghost")}
          disabled={saving || !!blockedTwo}
          title={blockedTwo ? `${blockedTwo.title} — ${blockedTwo.body}` : undefined}
          onClick={() => choose(2)}
        >
          2
        </button>
      </div>
      <p style={{ color: "var(--ink-2)", fontSize: 13, marginTop: 8 }}>
        Con un solo permiso la GPU ya va saturada. Dos permisos solapan OCR y figuras y pueden ganar un 10–25 %, no el
        doble. Mídelo antes de dejarlo activado.
      </p>
      {blockedTwo && (
        <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 4 }}>
          {blockedTwo.title}: {blockedTwo.body}
        </p>
      )}
      {saving && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Guardando…</span>}
      {saved && !saving && <span style={{ fontSize: 12, color: "var(--ok)" }}>Guardado: {permits}.</span>}
      {saveError && (
        <p style={{ color: "var(--bad)", fontSize: 12, marginTop: 4 }}>No se pudo guardar: {saveError}</p>
      )}
    </div>
  );
}

function AddSyncDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (s: Sync) => void }) {
  const presets = useAppStore((s) => s.presets);
  const [input, setInput] = useState("");
  const [output, setOutput] = useState("");
  const [presetId, setPresetId] = useState(presets[0]?.id ?? "");

  async function create() {
    if (!input || !output || !presetId) return;
    const s = await api.createSync({ input, output, preset_id: presetId, enabled: true });
    onCreated(s);
    onClose();
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Añadir carpeta sincronizada</h3>
        <div className="row">
          <label>Carpeta a vigilar</label>
          <div className="pickrow">
            <input type="text" value={input} onChange={(e) => setInput(e.target.value)} placeholder="~/Escaneos" />
            <button
              className="btn small"
              onClick={async () => {
                const r = await api.pickFolder();
                if (r.path) setInput(r.path);
              }}
            >
              Elegir…
            </button>
          </div>
        </div>
        <div className="row">
          <label>Carpeta de salida</label>
          <div className="pickrow">
            <input type="text" value={output} onChange={(e) => setOutput(e.target.value)} placeholder="~/Obsidian/Apuntes" />
            <button
              className="btn small"
              onClick={async () => {
                const r = await api.pickFolder();
                if (r.path) setOutput(r.path);
              }}
            >
              Elegir…
            </button>
          </div>
        </div>
        <div className="row">
          <label>Preajuste</label>
          <select className="pick" value={presetId} onChange={(e) => setPresetId(e.target.value)}>
            {presets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={onClose}>
            Cancelar
          </button>
          <button className="btn primary" onClick={create}>
            Añadir
          </button>
        </div>
      </div>
    </div>
  );
}

function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const h = Math.round(ms / 3600_000);
  if (h < 1) return "hace unos minutos";
  return `hace ${h} h`;
}
