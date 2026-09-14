import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import { api } from "../api";
import { ApiError } from "../api/client";
import type { Job, JobFileState, OriginalInfo, PageChange, PageDetail, UnsavedPageEditsDetail } from "../api/types";
import { kindLabelEs } from "../lib/format";

type ViewMode = "vista" | "codigo" | "editar";

/** Replaces the old full-screen Revisar route: opens over the Convertir file
 * list from one row (done/warn), scoped to that single file. Deep-linkable
 * at /convertir/:jobId/:fileIndex (see App.tsx/Convert.tsx). Esc closes. */
export default function ReviewDrawer({
  jobId,
  fileIndex,
  onClose,
}: {
  jobId: string;
  fileIndex: number;
  onClose: () => void;
}) {
  const [job, setJob] = useState<Job | null>(null);
  const [file, setFile] = useState<JobFileState | null>(null);
  const [original, setOriginal] = useState<OriginalInfo | null>(null);
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<PageDetail | null>(null);
  const [view, setView] = useState<ViewMode>("vista");
  const [draft, setDraft] = useState("");
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const [saving, setSaving] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [rereading, setRereading] = useState(false);
  const [rereadError, setRereadError] = useState<string | null>(null);
  const [savedMarkdown, setSavedMarkdown] = useState("");

  useEffect(() => {
    api.getJob(jobId).then((j) => {
      setJob(j);
      const f = j.files.find((x) => x.index === fileIndex) ?? null;
      setFile(f);
      setPage(1);
    });
    api
      .getOriginal(jobId, fileIndex)
      .then(setOriginal)
      .catch(() => setOriginal(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, fileIndex]);

  useEffect(() => {
    api.getPage(jobId, fileIndex, page).then((d) => {
      setDetail(d);
      setDraft(d.markdown);
      setSavedMarkdown(d.markdown);
      setDismissed(new Set());
    });
  }, [jobId, fileIndex, page]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const mdImageComponents = useMemo(
    () => ({
      img: (props: { src?: string; alt?: string }) => <FigureImage src={props.src} alt={props.alt} jobId={jobId} fileIndex={fileIndex} />,
    }),
    [jobId, fileIndex]
  );

  const pageCount = detail?.pages ?? file?.pages ?? null;

  async function save() {
    setSaving(true);
    setSaveError(null);
    setJustSaved(false);
    try {
      const d = await api.putPage(jobId, fileIndex, page, { markdown: draft });
      setDetail((prev) => ({
        ...d,
        image_png_base64: d.image_png_base64 ?? prev?.image_png_base64 ?? null,
        image_url: d.image_url ?? prev?.image_url ?? null,
      }));
      setSavedMarkdown(draft);
      // Guardar otherwise gives no acknowledgement at all beyond the button
      // returning to its idle label — no toast, no state change anywhere
      // (flow-issues.md C16). A transient "Guardado" is enough to confirm
      // the click did something, without needing a whole toast system.
      setJustSaved(true);
      setTimeout(() => setJustSaved(false), 2000);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function reread() {
    if (draft !== savedMarkdown) {
      const proceed = window.confirm(
        "Tienes cambios sin guardar en esta página; se perderán si vuelves a leerla con la nube. ¿Continuar?"
      );
      if (!proceed) return;
    }
    await runReread(false);
  }

  async function runReread(confirm: boolean) {
    setRereading(true);
    setRereadError(null);
    try {
      const updatedJob = await api.retryJob(jobId, {
        file_index: fileIndex,
        page,
        pipeline_patch: { review_model: "openai:gpt-4o" },
        confirm,
      });
      setJob(updatedJob);
      setFile(updatedJob.files.find((x) => x.index === fileIndex) ?? null);
      const d = await api.getPage(jobId, fileIndex, page);
      setDetail(d);
      setDraft(d.markdown);
      setSavedMarkdown(d.markdown);
      const f = updatedJob.files.find((x) => x.index === fileIndex);
      if (f?.status === "warn" || f?.status === "failed") {
        setRereadError(f.reason ?? "no se pudo completar la relectura.");
      }
    } catch (err) {
      if (
        !confirm &&
        err instanceof ApiError &&
        err.status === 409 &&
        (err.detail as UnsavedPageEditsDetail | undefined)?.code === "unsaved_page_edits"
      ) {
        setRereading(false);
        const proceed = window.confirm(
          "Se volverá a leer la página con la nube; se perderán los cambios guardados en ella. ¿Continuar?"
        );
        if (proceed) await runReread(true);
        return;
      }
      const detailMessage = err instanceof ApiError ? (err.detail as UnsavedPageEditsDetail | undefined)?.message : undefined;
      setRereadError(detailMessage ?? (err instanceof Error ? err.message : String(err)));
    } finally {
      setRereading(false);
    }
  }

  function undo(i: number, change: PageChange) {
    setDraft((prev) => prev.replace(change.after, change.before));
    setDismissed((prev) => new Set(prev).add(i));
  }

  async function openOriginal() {
    if (original?.path) await api.openPath({ path: original.path });
  }

  const fileName = file?.path.split("/").pop() ?? file?.path ?? "";
  // A file cancelled before it produced any output has nothing to review —
  // show that instead of a blank markdown pane with a live Guardar button
  // (see flow-issues.md N11).
  const notConverted = file != null && file.status === "cancelled" && !file.output_path;
  // Whole-file (non-paged) documents write their frontmatter into the same
  // markdown this drawer edits, so `draft` for them starts with a `---`
  // block of converter metadata — real content for Guardar to save, but not
  // something a reader opening Revisar wants staring back at them before any
  // actual text (flow-issues.md C11). Paged documents (pageCount != null)
  // never carry it on an individual page, so only strip for the whole-file
  // case, and only for the rendered preview — `draft`/the textarea keeps the
  // frontmatter intact so editing and Guardar still round-trip it.
  const renderedMarkdown = normalizeMarkdownForRender(pageCount == null ? stripLeadingFrontmatter(draft) : draft);

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="mainhead">
          <div>
            <div className="eyebrow">Revisar</div>
            <h2>
              {fileName}
              {pageCount != null ? ` · página ${page}` : ""}
            </h2>
          </div>
          <button className="btn ghost small" onClick={onClose}>
            Cerrar ✕
          </button>
        </div>

        <div className="rev">
          {pageCount != null && (
            <div className="pages">
              {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
                <button key={p} className={"p" + (p === page ? " cur" : "")} onClick={() => setPage(p)}>
                  <span>pág. {p}</span>
                </button>
              ))}
            </div>
          )}

          <div className="pane">
            <div className="ph">
              <span>Original</span>
              <span>{original?.kind === "pdf" ? "300 dpi" : ""}</span>
            </div>
            <div className="paper">
              {original && !original.previewable ? (
                <div style={{ padding: 16 }}>
                  <p style={{ color: "var(--muted)", marginBottom: 10 }}>
                    Este archivo ({kindLabelEs(original.kind)}) no tiene vista previa dentro de la app.
                  </p>
                  <button className="btn" onClick={openOriginal}>
                    Abrir el original
                  </button>
                </div>
              ) : imageSrc(detail) ? (
                <img src={imageSrc(detail)!} alt="Página original" />
              ) : detail ? (
                <span style={{ color: "var(--muted)" }}>No hay imagen de esta página.</span>
              ) : null}
            </div>
          </div>

          <div className="pane">
            <div className="ph">
              <span>Markdown</span>
              <span className="view-toggle">
                <button className={view === "vista" ? "on" : ""} onClick={() => setView("vista")}>
                  Vista
                </button>
                <button className={view === "codigo" ? "on" : ""} onClick={() => setView("codigo")}>
                  Código
                </button>
                <button className={view === "editar" ? "on" : ""} onClick={() => setView("editar")}>
                  Editar
                </button>
              </span>
            </div>
            {notConverted ? (
              <div className="rendered">
                <p style={{ color: "var(--muted)", padding: 16 }}>Este archivo aún no se ha convertido.</p>
              </div>
            ) : (
              <>
                {view === "codigo" && <div className="md">{draft}</div>}
                {view === "vista" && (
                  <div className="rendered">
                    <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]} components={mdImageComponents}>
                      {renderedMarkdown}
                    </ReactMarkdown>
                  </div>
                )}
                {view === "editar" && (
                  <>
                    <div className="md">
                      <textarea
                        value={draft}
                        onChange={(e) => {
                          setDraft(e.target.value);
                          setJustSaved(false);
                        }}
                      />
                    </div>
                    <div className="rendered" style={{ borderTop: "1px solid var(--line-soft)" }}>
                      <ReactMarkdown remarkPlugins={[remarkMath]} rehypePlugins={[rehypeKatex]} components={mdImageComponents}>
                        {renderedMarkdown}
                      </ReactMarkdown>
                    </div>
                  </>
                )}
              </>
            )}
          </div>

          <div className="rev-foot">
            <div className="changes">
              <span>Revisor:</span>
              {detail?.changes.map((c, i) =>
                dismissed.has(i) ? null : (
                  <span className="c" key={i}>
                    {c.before} → {c.after}
                    <button onClick={() => undo(i, c)}>deshacer</button>
                  </span>
                )
              )}
              {(!detail || detail.changes.length === 0) && <span style={{ color: "var(--muted)" }}>sin cambios</span>}
              {rereadError && <span style={{ color: "var(--bad)" }}>No se pudo releer: {rereadError}</span>}
              {saveError && <span style={{ color: "var(--bad)" }}>No se pudo guardar: {saveError}</span>}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn ghost" onClick={reread} disabled={rereading || notConverted}>
                {rereading ? "…" : "Volver a leer con…"}
              </button>
              <button className="btn primary" onClick={save} disabled={saving || notConverted}>
                {saving ? "Guardando…" : justSaved ? "Guardado ✓" : "Guardar"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function imageSrc(detail: PageDetail | null): string | null {
  if (!detail) return null;
  if (detail.image_url) return detail.image_url;
  if (detail.image_png_base64) return `data:image/png;base64,${detail.image_png_base64}`;
  return null;
}

/** Drops a leading `---\n...\n---` YAML frontmatter block, if present, so the
 * whole-file preview starts at the actual document instead of the
 * converter's metadata (source/converted_at/ocr_model/…). */
function stripLeadingFrontmatter(markdown: string): string {
  const m = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(markdown);
  return m ? markdown.slice(m[0].length) : markdown;
}

function normalizeMarkdownForRender(markdown: string): string {
  return markdown
    .replace(/\\\[([\s\S]*?)\\\]/g, (_m, inner) => `$$${inner}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_m, inner) => `$${inner}$`)
    .replace(/!\[([^\]]*)\]\((?!<)([^)]*\s[^)]*)\)/g, (_m, alt, url) => `![${alt}](<${url.trim()}>)`);
}

function assetSrc(jobId: string | null, fileIndex: number | null, relativeSrc: string): string | null {
  if (!jobId || fileIndex == null) return null;
  if (/^(https?:)?\/\//.test(relativeSrc) || relativeSrc.startsWith("data:")) return relativeSrc;
  const encoded = relativeSrc
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(decodeURIComponent(segment)))
    .join("/");
  return `/api/jobs/${jobId}/files/${fileIndex}/assets/${encoded}`;
}

function FigureImage({ src, alt, jobId, fileIndex }: { src?: string; alt?: string; jobId: string | null; fileIndex: number | null }) {
  const [failed, setFailed] = useState(false);
  const resolved = src ? assetSrc(jobId, fileIndex, src) : null;
  if (!resolved || failed) {
    return (
      <span className="fig-missing">
        <span aria-hidden="true">🖼</span> {alt || "Figura"} — imagen no disponible
      </span>
    );
  }
  return <img src={resolved} alt={alt} onError={() => setFailed(true)} />;
}
