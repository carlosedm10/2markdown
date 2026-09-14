import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useAppStore } from "./store/useAppStore";
import { api } from "./api";
import Wizard from "./screens/Wizard";
import Convert from "./screens/Convert";
import Historial from "./screens/Historial";
import PipelineScreen from "./screens/Pipeline";
import Ajustes from "./screens/Ajustes";

/** `make app` starts vite before uvicorn is listening, so the very first
 * GET /api/system and /api/presets calls this shell fires can hit the proxy
 * before the real server is up (~2s, occasionally more under load) — without
 * this, that raced as a bare `.catch(() => {})` per call: no retry, a
 * console error per failed request, and the app silently stuck showing
 * whatever empty/null state `system`/`presets` start as. Retries with
 * backoff instead, from 0.5s up to 3s between attempts, for up to ~30s total
 * before giving up and saying so. */
async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  opts: { minDelayMs?: number; maxDelayMs?: number; timeoutMs?: number } = {}
): Promise<T> {
  const minDelayMs = opts.minDelayMs ?? 500;
  const maxDelayMs = opts.maxDelayMs ?? 3000;
  const timeoutMs = opts.timeoutMs ?? 30000;
  const start = Date.now();
  let delay = minDelayMs;
  for (;;) {
    try {
      return await fn();
    } catch (err) {
      if (Date.now() - start >= timeoutMs) throw err;
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 2, maxDelayMs);
    }
  }
}

type EngineStatus = "connecting" | "ready" | "unreachable";

function Rail() {
  const onboarded = useAppStore((s) => s.onboarded);
  return (
    <nav className="rail">
      <div className="brand">2markdown</div>
      {onboarded && (
        <>
          <NavLink to="/convertir" className={({ isActive }) => (isActive ? "active" : "")}>
            Convertir
          </NavLink>
          <NavLink to="/historial" className={({ isActive }) => (isActive ? "active" : "")}>
            Historial
          </NavLink>
          <NavLink to="/ajustes" className={({ isActive }) => (isActive ? "active" : "")}>
            Ajustes
          </NavLink>
        </>
      )}
      {/* Once onboarded, the wizard is reachable from Ajustes › "Volver a
          ejecutar la configuración inicial" — a permanent rail entry here
          duplicates that control and invites re-entering the wizard by
          accident, which can silently overwrite a saved preset (see
          flow-issues.md C7/C13). The route itself stays mounted for that
          Ajustes button and for deep links; only the rail link is gated. */}
      {!onboarded && (
        <div className="foot">
          <NavLink to="/primera-vez" className={({ isActive }) => (isActive ? "active" : "")}>
            Primera vez
          </NavLink>
        </div>
      )}
    </nav>
  );
}

export default function App() {
  const onboarded = useAppStore((s) => s.onboarded);
  const setOnboarded = useAppStore((s) => s.setOnboarded);
  const setSystem = useAppStore((s) => s.setSystem);
  const setPresets = useAppStore((s) => s.setPresets);
  const setSettings = useAppStore((s) => s.setSettings);
  const setSelectedPresetId = useAppStore((s) => s.setSelectedPresetId);
  const setMode = useAppStore((s) => s.setMode);
  const theme = useAppStore((s) => s.theme);
  const [engineStatus, setEngineStatus] = useState<EngineStatus>("connecting");

  useEffect(() => {
    let cancelled = false;
    async function boot() {
      try {
        const [system, presets] = await Promise.all([
          retryWithBackoff(() => api.getSystem()),
          retryWithBackoff(() => api.getPresets()),
        ]);
        if (cancelled) return;
        setSystem(system);
        setPresets(presets);
        // Whether the wizard shows is decided here, fresh, every boot —
        // GET /api/settings.wizard_done, never a localStorage flag (see
        // useAppStore.ts). A single attempt, not retryWithBackoff: unlike
        // /api/system and /api/presets (which race the server's ~2s
        // startup), a 404 here means "this server predates /api/settings",
        // a permanent condition retrying for 30s would only delay booting
        // into the wizard for no reason. Degrade to "show the wizard"
        // rather than crash or silently skip onboarding.
        try {
          const settings = await api.getSettings();
          if (cancelled) return;
          setSettings(settings);
          setOnboarded(settings.wizard_done);
          setSelectedPresetId(settings.default_preset_id);
          setMode(settings.default_mode);
        } catch {
          if (!cancelled) setOnboarded(false);
        }
        setEngineStatus("ready");
      } catch {
        if (!cancelled) setEngineStatus("unreachable");
      }
    }
    boot();
    return () => {
      cancelled = true;
    };
  }, [setSystem, setPresets, setSettings, setOnboarded, setSelectedPresetId, setMode]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <div className="app-shell">
      <Rail />
      <main className="main">
        {engineStatus !== "ready" ? (
          <div style={{ maxWidth: 480 }}>
            <div className="eyebrow">2markdown</div>
            <h2 style={{ fontSize: 22, marginBottom: 6 }}>
              {engineStatus === "connecting" ? "Conectando con el motor…" : "No se pudo conectar con el motor"}
            </h2>
            <p className="sub">
              {engineStatus === "connecting"
                ? "El motor local tarda un par de segundos en arrancar. Esto se actualiza solo."
                : "Comprueba que el motor está en marcha (make app en una terminal) y vuelve a abrir esta ventana."}
            </p>
          </div>
        ) : (
          <Routes>
            <Route path="/" element={<Navigate to={onboarded ? "/convertir" : "/primera-vez"} replace />} />
            <Route path="/primera-vez" element={<Wizard />} />
            <Route path="/convertir" element={<Convert />} />
            <Route path="/convertir/:jobId" element={<Convert />} />
            <Route path="/convertir/:jobId/:fileIndex" element={<Convert />} />
            <Route path="/historial" element={<Historial />} />
            <Route path="/historial/:jobId" element={<Historial />} />
            <Route path="/ajustes" element={<Ajustes />} />
            <Route path="/pipeline" element={<PipelineScreen />} />
            {/* Old destinations, kept redirecting so any saved link/bookmark
                still lands somewhere sensible instead of 404ing. */}
            <Route path="/en-marcha" element={<Navigate to="/convertir" replace />} />
            <Route path="/revisar" element={<Navigate to="/convertir" replace />} />
            <Route path="/equipo" element={<Navigate to="/ajustes" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        )}
      </main>
    </div>
  );
}
