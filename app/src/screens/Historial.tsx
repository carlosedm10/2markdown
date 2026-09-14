import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useAppStore } from "../store/useAppStore";
import { api } from "../api";
import type { Job, JobFileState } from "../api/types";
import { fmtUsd, plural, reasonLabel } from "../lib/format";
import RetryPopover from "../components/RetryPopover";

function fileName(path: string): string {
  return path.split("/").pop() || path;
}
function pagesLabel(pages: number | null): string {
  return pages == null ? "—" : plural(pages, "página");
}
function fmtSeconds(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}
function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("es-ES", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** GET /api/jobs?limit= — history, newest first, persisted across server
 * restarts. Expanding a job shows the same file rows with the same actions
 * (open, review, re-convert, reveal) that Convertir's own "done" state
 * shows. */
export default function Historial() {
  const navigate = useNavigate();
  const params = useParams<{ jobId?: string }>();
  const presets = useAppStore((s) => s.presets);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(params.jobId ?? null);
  const [retryTarget, setRetryTarget] = useState<{ jobId: string; fileIndex: number } | null>(null);

  useEffect(() => {
    api.listJobs(100).then(setJobs);
  }, []);

  function refreshJob(jobId: string) {
    api.getJob(jobId).then((j) => {
      setJobs((prev) => (prev ? prev.map((x) => (x.job_id === jobId ? j : x)) : prev));
    });
  }

  async function openFile(f: JobFileState) {
    if (f.output_path) await api.openPath({ path: f.output_path });
  }
  async function revealFile(f: JobFileState) {
    if (f.output_path) await api.revealPath({ path: f.output_path });
  }
  async function openOutputFolder(job: Job) {
    await api.openPath({ path: job.output });
  }

  if (jobs === null) {
    return (
      <div>
        <div className="mainhead">
          <div>
            <div className="eyebrow">Historial</div>
            <h2>Cargando…</h2>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="mainhead">
        <div>
          <div className="eyebrow">Historial</div>
          <h2>{jobs.length ? `${plural(jobs.length, "conversión", "conversiones")}` : "Todavía no has convertido nada"}</h2>
        </div>
      </div>

      {jobs.length === 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          Convierte una carpeta desde Convertir y aparecerá aquí, incluso después de reiniciar la app.
        </div>
      )}

      <div className="queue" style={{ marginTop: 12 }}>
        {jobs.map((job) => {
          const isOpen = expanded === job.job_id;
          const preset = presets.find((p) => p.id === job.preset_id);
          return (
            <div key={job.job_id}>
              <button
                type="button"
                className="q"
                style={{ width: "100%", cursor: "pointer", textAlign: "left", background: "none", border: "none", font: "inherit" }}
                onClick={() => setExpanded(isOpen ? null : job.job_id)}
              >
                <span className={"st " + (job.status === "done" ? "ok" : job.status === "failed" ? "bad" : "wait")}>
                  {isOpen ? "▾" : "▸"}
                </span>
                <div className="name">
                  {fileName(job.input ?? job.output)}
                  <small>
                    {fmtDate(job.started)} · {preset?.name ?? job.preset_id ?? "—"} · {job.ok} ✓ · {job.warn} ! · {job.failed} ✕
                    {job.cancelled ? ` · ${job.cancelled} cancelado(s)` : ""}
                  </small>
                </div>
                <div className="prog">
                  <span className="t">{job.seconds != null ? fmtSeconds(job.seconds) : ""}</span>
                </div>
                <span className="tm">{fmtUsd(job.usd ?? 0)}</span>
              </button>

              {isOpen && (
                <div style={{ padding: "4px 0 14px 28px" }}>
                  <div className="qfoot" style={{ marginBottom: 8 }}>
                    <span style={{ fontSize: 12, color: "var(--muted)" }}>
                      {job.input ? `${job.input} → ${job.output}` : job.output}
                    </span>
                    <button className="btn ghost small" onClick={() => openOutputFolder(job)}>
                      Abrir carpeta de salida
                    </button>
                  </div>
                  <div className="queue">
                    {job.files.map((f) => (
                      <div className="q" key={f.index}>
                        <span
                          className={
                            "st " + (f.status === "ok" ? "ok" : f.status === "warn" ? "warn" : f.status === "cancelled" ? "bad" : f.status === "failed" ? "bad" : "wait")
                          }
                        >
                          {f.status === "ok" ? "✓" : f.status === "warn" ? "!" : f.status === "failed" ? "✕" : f.status === "cancelled" ? "⊘" : ""}
                        </span>
                        <div className="name">
                          {fileName(f.path)}
                          <small>
                            {pagesLabel(f.pages)}
                            {f.reason ? (
                              <>
                                {" · "}
                                <b>{reasonLabel(f.reason)}</b>
                              </>
                            ) : null}
                          </small>
                        </div>
                        <div className="actions">
                          {f.output_path && (
                            <button className="btn ghost small" onClick={() => openFile(f)}>
                              Abrir .md
                            </button>
                          )}
                          {(f.status === "ok" || f.status === "warn") && (
                            <button className="btn ghost small" onClick={() => navigate(`/convertir/${job.job_id}/${f.index}`)}>
                              Revisar
                            </button>
                          )}
                          <button className="btn ghost small" onClick={() => setRetryTarget({ jobId: job.job_id, fileIndex: f.index })}>
                            Volver a convertir…
                          </button>
                          {f.output_path && (
                            <button className="btn ghost small" onClick={() => revealFile(f)}>
                              Mostrar en Finder
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {retryTarget && (
        <RetryPopover
          jobId={retryTarget.jobId}
          fileIndex={retryTarget.fileIndex}
          onClose={() => setRetryTarget(null)}
          onDone={() => refreshJob(retryTarget.jobId)}
        />
      )}
    </div>
  );
}
