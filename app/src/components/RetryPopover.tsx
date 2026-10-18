import { useEffect, useState } from "react";
import { api } from "../api";
import { ApiError } from "../api/client";
import type { EstimateResponse, Job, JobFileState, ModelId, UnsavedPageEditsDetail } from "../api/types";
import { fmtTime, fmtUsd } from "../lib/format";
import ModelPicker from "./ModelPicker";

/** "Volver a convertir con… ▾" — a small popover, not a full navigation,
 * pre-filled from the job's own pipeline. Confirmar sends a `pipeline_patch`
 * retry for this one file; a 409 unsaved_page_edits asks to confirm, a 422
 * (blocked pipeline) surfaces the server's message inline. */
export default function RetryPopover({
  jobId,
  fileIndex,
  onClose,
  onDone,
}: {
  jobId: string;
  fileIndex: number;
  onClose: () => void;
  onDone: (job: Job) => void;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [file, setFile] = useState<JobFileState | null>(null);
  const [ocr, setOcr] = useState<ModelId>("tesseract");
  const [figures, setFigures] = useState<ModelId | "off">("off");
  const [review, setReview] = useState<ModelId | "off">("off");
  const [estimate, setEstimate] = useState<EstimateResponse | null>(null);
  const [estimateUnavailable, setEstimateUnavailable] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsConfirm, setNeedsConfirm] = useState<UnsavedPageEditsDetail | null>(null);

  useEffect(() => {
    api.getJob(jobId).then((j) => {
      setJob(j);
      const f = j.files.find((x) => x.index === fileIndex) ?? null;
      setFile(f);
      setOcr(j.pipeline.ocr_model);
      setFigures(j.pipeline.describe_figures && j.pipeline.figure_model ? j.pipeline.figure_model : "off");
      setReview(j.pipeline.review_model ?? "off");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, fileIndex]);

  useEffect(() => {
    if (!file) return;
    let cancelled = false;
    api
      .inspect({ path: file.path })
      .then((inspection) =>
        api.estimate({
          inspect_id: inspection.inspect_id,
          pipeline: {
            ocr_model: ocr,
            figure_model: figures === "off" ? null : figures,
            review_model: review === "off" ? null : review,
            workers: job?.pipeline.workers ?? 1,
            describe_figures: figures !== "off",
            clean: job?.pipeline.clean ?? true,
            tables: job?.pipeline.tables ?? true,
            emit_chunks: false,
            ocr_dpi: job?.pipeline.ocr_dpi ?? 300,
          },
        })
      )
      .then((r) => {
        if (!cancelled) setEstimate(r);
      })
      .catch(() => {
        if (!cancelled) setEstimateUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, ocr, figures, review]);

  async function confirm(force = false) {
    setConfirming(true);
    setError(null);
    try {
      const updated = await api.retryJob(jobId, {
        file_index: fileIndex,
        pipeline_patch: {
          ocr_model: ocr,
          figure_model: figures === "off" ? null : figures,
          review_model: review === "off" ? null : review,
          describe_figures: figures !== "off",
        },
        confirm: force,
      });
      onDone(updated);
      onClose();
    } catch (err) {
      if (
        !force &&
        err instanceof ApiError &&
        err.status === 409 &&
        (err.detail as UnsavedPageEditsDetail | undefined)?.code === "unsaved_page_edits"
      ) {
        setNeedsConfirm(err.detail as UnsavedPageEditsDetail);
        return;
      }
      if (err instanceof ApiError && err.status === 422) {
        setError(typeof err.detail === "string" ? err.detail : "Esta combinación de modelos no cabe en la GPU.");
        return;
      }
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Volver a convertir «{file?.path.split("/").pop() ?? ""}»</h3>
        <div className="row">
          <label>Leer páginas escaneadas (OCR)</label>
          <ModelPicker stage="ocr" value={ocr} onChange={(v) => setOcr(v as ModelId)} />
        </div>
        <div className="row">
          <label>Describir figuras</label>
          <ModelPicker stage="figures" allowOff offLabel="No describir" value={figures} onChange={setFigures} />
        </div>
        <div className="row">
          <label>Revisar erratas</label>
          <ModelPicker stage="review" allowOff offLabel="Sin revisión" value={review} onChange={setReview} />
        </div>

        <div className="est" style={{ marginTop: 8 }}>
          {estimate ? (
            <div className="big">
              <div>
                <div className="n">{fmtTime(estimate.total_seconds)}</div>
                <div className="l">tiempo estimado</div>
              </div>
              <div>
                <div className="n">{estimate.total_usd == null ? "?" : fmtUsd(estimate.total_usd)}</div>
                <div className="l">coste en nube</div>
              </div>
            </div>
          ) : estimateUnavailable ? (
            <p style={{ fontSize: 12, color: "var(--muted)", padding: 10 }}>Estimación no disponible para este archivo.</p>
          ) : (
            <p style={{ fontSize: 12, color: "var(--muted)", padding: 10 }}>Calculando estimación…</p>
          )}
        </div>

        {needsConfirm && (
          <p style={{ fontSize: 12.5, color: "var(--warn)" }}>
            Esta página tiene cambios guardados que se perderán al volver a leerla. ¿Continuar de todos modos?
          </p>
        )}
        {error && <p style={{ fontSize: 12.5, color: "var(--bad)" }}>{error}</p>}

        <div className="actions">
          <button className="btn ghost" onClick={onClose}>
            Cancelar
          </button>
          {needsConfirm ? (
            <button className="btn primary" disabled={confirming} onClick={() => confirm(true)}>
              {confirming ? "…" : "Continuar de todos modos"}
            </button>
          ) : (
            <button className="btn primary" disabled={confirming} onClick={() => confirm(false)}>
              {confirming ? "…" : "Confirmar"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
