import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { CloudModelInfo, LocalModelInfo, ModelId, ModelsResponse, ResolveModelResponse } from "../api/types";
import { fuzzyFilter } from "../lib/fuzzy";

/** One shared GET /api/models fetch for every ModelPicker mounted at once
 * (Pipeline has three, Wizard's "Personalizado" card has three more) —
 * without this each would fire its own request on mount. Not confirmed live
 * on the server yet (see docs/desktop-app.md); a failure here just leaves
 * every picker showing its "cargando modelos…" / error state instead of
 * crashing the screen. */
let modelsPromise: Promise<ModelsResponse> | null = null;
function getModelsCached(): Promise<ModelsResponse> {
  if (!modelsPromise) {
    modelsPromise = api.getModels().catch((err) => {
      modelsPromise = null;
      throw err;
    });
  }
  return modelsPromise;
}
/** Test-only escape hatch: force the next ModelPicker mount to re-fetch. */
export function _resetModelsCache(): void {
  modelsPromise = null;
}

function useModelsCatalog(): { models: ModelsResponse | null; error: boolean } {
  const [models, setModels] = useState<ModelsResponse | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    getModelsCached()
      .then((m) => {
        if (!cancelled) setModels(m);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return { models, error };
}

// The cloud defaults the app itself picks elsewhere (Wizard.tsx's "cloud-ai"
// step, Pipeline.tsx's DEFAULT_PIPELINE review model) — surfaced first
// within their fuzzy-match tier so e.g. typing "gpt-" lists the flagship
// gpt-4o ahead of bare version stubs like gpt-4/gpt-5 (see lib/fuzzy.ts's
// `preferred` boost).
const RECOMMENDED_MODEL_NAMES = new Set(["gpt-4o", "gpt-4o-mini"]);

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  groq: "Groq",
  mistral: "Mistral",
  openrouter: "OpenRouter",
};

function providerLabel(id: string): string {
  return PROVIDER_LABELS[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
}

function modelDisplayName(id: ModelId): string {
  const parts = id.split(":");
  return parts.length > 1 ? parts.slice(1).join(":") : id;
}

// A model whose bare name is unmistakably one vendor's own (claude-*,
// gemini-*, gpt-*…) but that shows up tagged under a DIFFERENT provider is a
// catalog cross-listing, not a real option this app can drive — pydantic-ai
// has no route that sends a "claude-*" id to Google's API (flow-issues.md
// N3: GET /api/models has been observed returning exactly that combination).
// "openrouter" is exempt: reselling other vendors' models under one API id
// is its actual business, not a mistake.
const VENDOR_NAME_PATTERNS: { re: RegExp; provider: string }[] = [
  { re: /^claude(-|$)/i, provider: "anthropic" },
  { re: /^gemini(-|$)/i, provider: "google" },
  { re: /^(gpt-|chatgpt|o[1-9](-|$))/i, provider: "openai" },
  { re: /^(mistral|mixtral|codestral)(-|$)/i, provider: "mistral" },
];

function isVendorMismatch(modelName: string, groupProviderId: string): boolean {
  if (groupProviderId === "openrouter") return false;
  const hit = VENDOR_NAME_PATTERNS.find((p) => p.re.test(modelName));
  return !!hit && hit.provider !== groupProviderId;
}

function problemMessage(problem: string, providerId: string | null): { text: string; kind: "warn" | "bad" } {
  switch (problem) {
    case "not_installed":
      return { text: "Este modelo no está descargado todavía.", kind: "warn" };
    case "no_api_key":
      return { text: `Falta la clave de ${providerId ? providerLabel(providerId) : "este proveedor"}.`, kind: "warn" };
    case "unknown_price":
      return { text: "Precio desconocido — podrás añadirlo en la estimación.", kind: "warn" };
    case "unknown_provider":
      return { text: "Proveedor desconocido: revisa el id (proveedor:modelo).", kind: "bad" };
    default:
      return { text: problem, kind: "warn" };
  }
}

export interface ModelPickerProps {
  stage: "ocr" | "figures" | "review";
  /** Current model id, or "off" when `allowOff` and nothing is selected. */
  value: ModelId | "off";
  onChange: (value: ModelId | "off") => void;
  /** Whether a "no model" choice exists for this stage (figures/review can be
   * turned off entirely; ocr always reads with at least Tesseract). */
  allowOff?: boolean;
  offLabel?: string;
  className?: string;
}

type Pill = { text: string; kind: "warn" | "bad" };

interface Row {
  value: ModelId | "off";
  label: string;
  sub: string;
  pills: Pill[];
}

interface Section {
  label: string;
  rows: Row[];
}

const MAX_RENDERED_ROWS = 50;

function localRow(m: LocalModelInfo): Row {
  const pills: Pill[] = [];
  if (!m.installed) pills.push({ text: "no instalado", kind: "warn" });
  return {
    value: m.id,
    label: modelDisplayName(m.id),
    sub: `${m.size_gb} GB${m.loaded ? " · en memoria" : ""}`,
    pills,
  };
}

/** `providerId` is the group this row is actually rendered under — derived
 * from the id's own "<provider>:<model>" prefix (see the grouping note
 * below), not necessarily `m.provider`, so the badge always names the same
 * provider as the section header it sits in. */
function cloudRow(m: CloudModelInfo, providerId: string, keyPresent: boolean, stage: ModelPickerProps["stage"]): Row {
  const pills: Pill[] = [];
  if (!keyPresent) pills.push({ text: "sin clave", kind: "warn" });
  if (!m.price_known) pills.push({ text: "precio desconocido", kind: "warn" });
  // "visión ?" only matters for stages that actually read images (ocr/
  // figures) — review never looks at the page image, so the pill is just
  // noise there (flow-issues.md N2).
  if (m.vision === null && stage !== "review") pills.push({ text: "visión ?", kind: "warn" });
  return {
    value: m.id,
    label: modelDisplayName(m.id),
    sub: providerLabel(providerId),
    pills,
  };
}

/** <input role="combobox"> + listbox popup, fed by GET /api/models and
 * validated through POST /api/models/resolve. Shared by Pipeline.tsx's three
 * per-stage pickers and Wizard.tsx's "Personalizado" onboarding card.
 *
 * Replaces a plain `<select>`: with OpenRouter's catalog in play a stage can
 * offer hundreds of ids (x-ai/grok-*, z-ai/glm-*, anthropic/claude-*-latest…)
 * that a native dropdown makes unusable — no search, no way to type an
 * abbreviation. This opens a panel with its own search input on top,
 * fuzzy-matched (see lib/fuzzy.ts), capped at MAX_RENDERED_ROWS rows with a
 * "sigue escribiendo" hint for the rest, and keeps the free-typed "Otro
 * modelo…" id path — now surfaced as a "Usar «texto» tal cual" row instead
 * of a separate menu entry, running through the exact same
 * resolve/download/accept flow as before. */
export default function ModelPicker({ stage, value, onChange, allowOff = false, offLabel = "Sin modelo", className }: ModelPickerProps) {
  const { models, error } = useModelsCatalog();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [customMode, setCustomMode] = useState(false);
  const [customText, setCustomText] = useState("");
  const [resolving, setResolving] = useState(false);
  const [resolved, setResolved] = useState<ResolveModelResponse | null>(null);
  const [downloading, setDownloading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  // The popup portals to document.body (see `panelPos` below) so it always
  // paints above whatever the trigger happens to be nested in — Pipeline's
  // flow diagram stacks the trigger inside an absolutely-positioned
  // `.flow-node` sitting over an SVG full of its own absolutely-positioned
  // siblings, and a plain in-flow `.combo-panel` used to render BEHIND those
  // sibling nodes/connectors instead of on top of them. `panelRef` lets the
  // outside-click handler below still recognize clicks inside the portaled
  // panel as "inside", even though it is no longer a DOM descendant of
  // `rootRef`.
  const panelRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const listId = useId();
  const [panelPos, setPanelPos] = useState<{ top: number; left: number; width: number } | null>(null);

  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  // Close on outside click / focus leaving the widget — "outside" means
  // neither the trigger nor the portaled panel.
  useEffect(() => {
    if (!open) return;
    function onDocMouseDown(e: MouseEvent) {
      const t = e.target as Node;
      const insideTrigger = rootRef.current && rootRef.current.contains(t);
      const insidePanel = panelRef.current && panelRef.current.contains(t);
      if (!insideTrigger && !insidePanel) setOpen(false);
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  // Keep the portaled panel glued under the trigger — recomputed on open and
  // whenever the page scrolls (capture phase, so it catches scrolling inside
  // any ancestor, not just the window) or resizes.
  useLayoutEffect(() => {
    if (!open) return;
    function updatePos() {
      const el = triggerRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setPanelPos({ top: r.bottom + 4, left: r.left, width: Math.max(r.width, 260) });
    }
    updatePos();
    window.addEventListener("scroll", updatePos, true);
    window.addEventListener("resize", updatePos);
    return () => {
      window.removeEventListener("scroll", updatePos, true);
      window.removeEventListener("resize", updatePos);
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
      // Focus the panel's own search input, not the trigger — the panel is
      // a search-first popup, not just a menu.
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [open]);

  const allowed = models?.stages[stage] ?? [];
  const localOptions = useMemo(() => (models ? models.local.filter((m) => allowed.includes(m.id)) : []), [models, allowed]);
  const cloudOptions = useMemo(() => (models ? models.cloud.filter((m) => allowed.includes(m.id)) : []), [models, allowed]);
  const providerKeyPresent = useMemo(() => new Map((models?.providers ?? []).map((p) => [p.id, p.key_present])), [models]);
  const knownIds = useMemo(() => {
    const s = new Set<string>([...localOptions.map((m) => m.id), ...cloudOptions.map((m) => m.id)]);
    if (allowed.includes("tesseract")) s.add("tesseract");
    return s;
  }, [localOptions, cloudOptions, allowed]);
  const isCustomSelected = value !== "off" && !knownIds.has(value);

  // Vision-capable models surface first for stages where that matters — see
  // the task's "sort within each provider alphabetically, but put models we
  // know are vision-capable first when the stage is ocr/figures".
  const visionFirst = stage === "ocr" || stage === "figures";

  const sections: Section[] = useMemo(() => {
    const out: Section[] = [];
    if (isCustomSelected) {
      out.push({
        label: "Personalizado",
        rows: [{ value, label: value, sub: "sin verificar", pills: [] }],
      });
    }
    if (localOptions.length > 0) {
      const rows = [...localOptions]
        .sort((a, b) => {
          if (visionFirst && a.vision !== b.vision) return a.vision ? -1 : 1;
          return modelDisplayName(a.id).localeCompare(modelDisplayName(b.id));
        })
        .map(localRow);
      out.push({ label: "Instalados en este Mac", rows });
    }
    // Built right after "Instalados", before the (potentially huge) cloud
    // provider loop below — a catalog like OpenRouter can run to hundreds of
    // rows, and this section used to be pushed last and fall past the
    // MAX_RENDERED_ROWS cap before it ever got a chance to render (see
    // flow-issues.md M8: "No describir"/"Tesseract" invisible on an
    // unfiltered figures picker). It is also pulled out and exempted from
    // that cap below, so its position here is a display-order nicety, not
    // what keeps it visible.
    if (allowed.includes("tesseract") || allowOff) {
      const rows: Row[] = [];
      if (allowed.includes("tesseract")) {
        rows.push({ value: "tesseract", label: "Tesseract", sub: "CPU", pills: [] });
      }
      if (allowOff) {
        rows.push({ value: "off", label: offLabel, sub: "", pills: [] });
      }
      out.push({ label: "Sin modelo / Tesseract", rows });
    }
    // Group by the id's OWN "<provider>:<model>" prefix, not the catalog's
    // `m.provider` field — genai-prices can list a model (e.g. a Vertex AI
    // resale of an Anthropic model) under one provider's catalog while the
    // model itself is only ever invocable through a different provider
    // string. Trusting `m.provider` there put every claude-* id under a
    // "Nube · Google" group (flow-issues.md N3): the prefix baked into the
    // id is what `normalize_model_id`/pydantic-ai actually dispatch on, so
    // it — not the catalog's own bookkeeping — is the model's real provider.
    const cloudByProvider = new Map<string, typeof cloudOptions>();
    for (const m of cloudOptions) {
      const idProvider = m.id.split(":")[0] || m.provider;
      const list = cloudByProvider.get(idProvider) ?? [];
      list.push(m);
      cloudByProvider.set(idProvider, list);
    }
    for (const [providerId, list] of cloudByProvider.entries()) {
      const keyPresent = providerKeyPresent.get(providerId as never) ?? false;
      const rows = [...list]
        .filter((m) => !isVendorMismatch(modelDisplayName(m.id), providerId))
        .sort((a, b) => {
          if (visionFirst && a.vision !== b.vision) return a.vision ? -1 : b.vision === null ? -1 : 1;
          return modelDisplayName(a.id).localeCompare(modelDisplayName(b.id));
        })
        .map((m) => cloudRow(m, providerId, keyPresent, stage));
      // A provider whose every listed model was a cross-listing filtered
      // out above (isVendorMismatch) has nothing left to show — an empty
      // "Nube · X" header would just be noise.
      if (rows.length > 0) out.push({ label: `Nube · ${providerLabel(providerId)}${keyPresent ? "" : " (sin clave)"}`, rows });
    }
    return out;
  }, [isCustomSelected, value, localOptions, cloudOptions, providerKeyPresent, allowed, allowOff, offLabel, visionFirst]);

  // Fuzzy-filter every section against the query, then cap the combined row
  // count so a huge catalog never renders hundreds of DOM nodes at once.
  // Sections are then reordered by their own best-scoring row, so the group
  // holding the single best match (e.g. "Instalados en este Mac" for "gpt-"
  // when a gpt model is installed) always sorts first instead of following
  // the fixed Personalizado/Instalados/Nube/Sin-modelo section order.
  const { filteredSections, totalMatches, overflow } = useMemo(() => {
    let total = 0;
    const scoredSections: { label: string; rows: Row[]; bestScore: number }[] = [];
    for (const section of sections) {
      const matches = fuzzyFilter(section.rows, query, (r) => r.value, (r) => r.label, (r) => RECOMMENDED_MODEL_NAMES.has(modelDisplayName(r.value as string)));
      total += matches.length;
      if (matches.length === 0) continue;
      // Bias ranking toward what the user can actually use right now: a
      // same-scoring no-key cloud row (e.g. a free OpenRouter catalog with
      // hundreds of ids) must never outrank a key-present/installed one —
      // otherwise typing a fragment like "7b" spends the whole row budget on
      // cloud rows sin clave before the installed local model is ever
      // reached (flow-issues.md M7).
      const keyless = section.label.includes("(sin clave)");
      const bias = keyless ? -0.5 : 0;
      scoredSections.push({ label: section.label, rows: matches.map((m) => m.item), bestScore: matches[0].score + bias });
    }
    if (query.trim()) scoredSections.sort((a, b) => b.bestScore - a.bestScore);

    // "Instalados en este Mac" always leads when it has any match at all —
    // installed/local is always the most immediately usable option, cloud
    // catalog size notwithstanding — and "Sin modelo / Tesseract" is exempt
    // from the row budget entirely, so neither can ever be pushed out by a
    // huge provider catalog (flow-issues.md M7/M8).
    const installedAt = scoredSections.findIndex((s) => s.label === "Instalados en este Mac");
    const installed = installedAt >= 0 ? scoredSections.splice(installedAt, 1)[0] : null;
    const noModelAt = scoredSections.findIndex((s) => s.label === "Sin modelo / Tesseract");
    const noModel = noModelAt >= 0 ? scoredSections.splice(noModelAt, 1)[0] : null;

    const out: Section[] = [];
    let budget = MAX_RENDERED_ROWS;
    if (installed) {
      const take = installed.rows.slice(0, Math.min(installed.rows.length, budget));
      budget -= take.length;
      out.push({ label: installed.label, rows: take });
    }
    if (noModel) out.push({ label: noModel.label, rows: noModel.rows }); // exempt from budget
    for (const section of scoredSections) {
      if (budget <= 0) break;
      const take = section.rows.slice(0, budget);
      budget -= take.length;
      if (take.length > 0) out.push({ label: section.label, rows: take });
    }
    return { filteredSections: out, totalMatches: total, overflow: Math.max(0, total - MAX_RENDERED_ROWS) };
  }, [sections, query]);

  const showCustomOffer = query.trim().length > 0 && totalMatches === 0;

  // Flatten to a single list of selectable rows (options + the custom-offer
  // row) so arrow-key navigation and aria-activedescendant have one linear
  // index to work with, independent of the group headers.
  const flatOptions = useMemo(() => {
    const flat: { sectionLabel: string; row: Row }[] = [];
    for (const section of filteredSections) {
      for (const row of section.rows) flat.push({ sectionLabel: section.label, row });
    }
    return flat;
  }, [filteredSections]);

  function optionId(index: number): string {
    return `${listId}-opt-${index}`;
  }

  function selectValue(v: ModelId | "off") {
    onChange(v);
    setOpen(false);
  }

  function fireDebouncedResolve(id: string) {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!id.trim()) {
      setResolved(null);
      setResolving(false);
      return;
    }
    setResolving(true);
    debounceRef.current = setTimeout(async () => {
      try {
        const r = await api.resolveModel({ id: id.trim() });
        setResolved(r);
      } catch {
        setResolved(null);
      } finally {
        setResolving(false);
      }
    }, 400);
  }

  function openCustomWith(text: string) {
    setCustomMode(true);
    setCustomText(text);
    setResolved(null);
    fireDebouncedResolve(text);
    setOpen(false);
  }

  async function downloadCustom() {
    if (!resolved) return;
    setDownloading(true);
    try {
      await api.pullOllamaModel(resolved.id);
      const r = await api.resolveModel({ id: resolved.id });
      setResolved(r);
    } finally {
      setDownloading(false);
    }
  }

  function acceptCustom() {
    if (!resolved || resolved.problems.includes("unknown_provider")) return;
    onChange(resolved.id);
    setCustomMode(false);
    setCustomText("");
    setResolved(null);
  }

  function cancelCustom() {
    setCustomMode(false);
    setCustomText("");
    setResolved(null);
  }

  function onSearchKeyDown(e: React.KeyboardEvent) {
    const maxIndex = flatOptions.length - 1 + (showCustomOffer ? 1 : 0);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(maxIndex, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActiveIndex(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActiveIndex(maxIndex);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (showCustomOffer && activeIndex === flatOptions.length) {
        openCustomWith(query);
      } else {
        const picked = flatOptions[activeIndex];
        if (picked) selectValue(picked.row.value);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  }

  function onTriggerKeyDown(e: React.KeyboardEvent) {
    if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
      e.preventDefault();
      setOpen(true);
    }
  }

  if (error) {
    return <div className="loadinfo">No se pudo cargar la lista de modelos.</div>;
  }
  if (!models) {
    return (
      <div className={className ?? "pick"} aria-disabled="true" style={{ opacity: 0.6, cursor: "default" }}>
        Cargando modelos…
      </div>
    );
  }

  if (customMode) {
    const blocked = !resolved || resolved.problems.includes("unknown_provider");
    return (
      <div style={{ display: "grid", gap: 6 }}>
        <input
          type="text"
          className={className ?? "pick"}
          placeholder="proveedor:modelo (p.ej. anthropic:claude-3-5-sonnet)"
          value={customText}
          autoFocus
          onChange={(e) => {
            setCustomText(e.target.value);
            fireDebouncedResolve(e.target.value);
          }}
        />
        {resolving && <div className="loadinfo">Comprobando…</div>}
        {!resolving && resolved && resolved.problems.length === 0 && <div className="loadinfo">Modelo válido.</div>}
        {!resolving &&
          resolved &&
          resolved.problems.map((p) => {
            const msg = problemMessage(p, resolved.id.includes(":") ? resolved.id.split(":")[0] : null);
            return (
              <div key={p} className={"loadinfo" + (msg.kind === "bad" ? " bad" : "")} style={{ color: msg.kind === "bad" ? "var(--bad)" : "var(--warn)" }}>
                {msg.text}
                {p === "not_installed" && (
                  <button type="button" className="btn small" style={{ marginLeft: 8 }} disabled={downloading} onClick={downloadCustom}>
                    {downloading ? "Descargando…" : "Descargar"}
                  </button>
                )}
                {p === "no_api_key" && (
                  <>
                    {" "}
                    <Link to="/ajustes">Ir a Ajustes</Link>
                  </>
                )}
              </div>
            );
          })}
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn ghost small" onClick={cancelCustom}>
            Cancelar
          </button>
          <button type="button" className="btn small" disabled={blocked} onClick={acceptCustom}>
            Usar este modelo
          </button>
        </div>
      </div>
    );
  }

  const currentRow = flatOptionForValue(sections, value);
  const triggerLabel = value === "off" ? offLabel : currentRow?.label ?? modelDisplayName(value);
  const triggerPills = currentRow?.pills ?? [];

  return (
    <div className={"combo" + (className ? ` ${className}` : "")} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={listId}
        className="combo-trigger"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKeyDown}
      >
        <span className="combo-main">
          <span className="combo-value" title={triggerLabel}>
            {triggerLabel}
          </span>
          <span className="combo-pills">
            {triggerPills.map((p) => (
              <span key={p.text} className={"pill " + (p.kind === "bad" ? "bad" : "warn")} style={{ fontSize: 10, padding: "1px 6px" }}>
                {p.text}
              </span>
            ))}
          </span>
        </span>
        <span className="combo-caret" aria-hidden="true" />
      </button>
      {open &&
        panelPos &&
        createPortal(
          <div
            className="combo-panel combo-panel-portal"
            ref={panelRef}
            style={{ position: "fixed", top: panelPos.top, left: panelPos.left, width: panelPos.width, right: "auto" }}
          >
          <input
            ref={searchRef}
            type="text"
            className="combo-search"
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={
              flatOptions.length > 0 || showCustomOffer
                ? activeIndex < flatOptions.length
                  ? optionId(activeIndex)
                  : `${listId}-custom`
                : undefined
            }
            aria-autocomplete="list"
            placeholder="Buscar modelo…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onSearchKeyDown}
          />
          <ul role="listbox" id={listId} className="combo-list">
            {flatOptions.length === 0 && !showCustomOffer && <li className="combo-empty">Sin resultados.</li>}
            {(() => {
              let flatIndex = -1;
              return filteredSections.map((section) => (
                <li key={section.label} className="combo-section" role="presentation">
                  <div className="combo-group-label">{section.label}</div>
                  <ul role="presentation" className="combo-section-rows">
                    {section.rows.map((row) => {
                      flatIndex++;
                      const idx = flatIndex;
                      return (
                        <li
                          key={row.value}
                          id={optionId(idx)}
                          role="option"
                          aria-selected={value === row.value}
                          className={"combo-option" + (idx === activeIndex ? " active" : "") + (value === row.value ? " sel" : "")}
                          onMouseEnter={() => setActiveIndex(idx)}
                          onMouseDown={(e) => {
                            e.preventDefault();
                            selectValue(row.value);
                          }}
                        >
                          <span className="combo-option-label">{row.label}</span>
                          {row.sub && <span className="combo-option-sub">{row.sub}</span>}
                          {row.pills.map((p) => (
                            <span key={p.text} className={"pill " + (p.kind === "bad" ? "bad" : "warn")} style={{ fontSize: 10 }}>
                              {p.text}
                            </span>
                          ))}
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ));
            })()}
            {overflow > 0 && <li className="combo-more">{overflow} más — sigue escribiendo</li>}
            {showCustomOffer && (
              <li
                id={`${listId}-custom`}
                role="option"
                aria-selected={false}
                className={"combo-option combo-custom" + (activeIndex === flatOptions.length ? " active" : "")}
                onMouseEnter={() => setActiveIndex(flatOptions.length)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  openCustomWith(query);
                }}
              >
                Usar «{query}» tal cual
              </li>
            )}
          </ul>
          </div>,
          document.body
        )}
    </div>
  );
}

function flatOptionForValue(sections: Section[], value: ModelId | "off"): Row | null {
  for (const section of sections) {
    for (const row of section.rows) {
      if (row.value === value) return row;
    }
  }
  return null;
}
