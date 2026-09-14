/** Formatting helpers shared across screens — mirrors the mockup's fmtTime/fmtUsd
 * (docs/mockups/desktop-v4.html) so the numbers read identically. USD only,
 * never EUR: unknown price is a distinct state, never a false $0.
 *
 * Every number the UI renders goes through `fmtNumber` (es-ES: comma
 * decimals) so a value read straight off the API ("51.5 GB", "0.04") never
 * sits next to a hand-formatted one ("5,6 GB") in the same sentence. */

export function fmtNumber(n: number, fractionDigits = 1): string {
  return new Intl.NumberFormat("es-ES", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(n);
}

export function fmtTime(seconds: number): string {
  if (seconds < 60) return Math.round(seconds) + " s";
  if (seconds < 3600) return "~" + Math.round(seconds / 60) + " min";
  const h = seconds / 3600;
  return "~" + (h < 10 ? fmtNumber(h, 1) : fmtNumber(Math.round(h), 0)) + " h";
}

/** null means "genai-prices has no table for this model" — render the caller's
 * own unknown-price affordance instead of calling this with null. Always one
 * unit (USD, "$"): a column that sometimes reads dollars and sometimes cents
 * ("$0" next to "0.04 ¢") is easy to misread as a hundred-fold difference. */
export function fmtUsd(usd: number | null): string {
  if (usd == null) return "Precio del modelo desconocido";
  if (usd === 0) return "$0";
  if (usd < 0.01) return "$" + fmtNumber(usd, 4);
  return "$" + fmtNumber(usd, 2);
}

/** Spanish singular/plural agreement for a counted noun, e.g. plural(1,
 * "archivo") === "1 archivo", plural(3, "archivo") === "3 archivos". */
export function plural(n: number, singular: string, pluralForm = singular + "s"): string {
  return `${n} ${n === 1 ? singular : pluralForm}`;
}

// A handful of raw English status tokens the server is known to still send
// as a file's `reason` in some cancel paths (e.g. cancelling a file that was
// already mid-run) instead of the Spanish phrase used for every other reason
// ("cancelado antes de empezar", etc — see flow-issues.md C10). Translate
// the ones we know about here rather than surfacing the enum value verbatim
// in an otherwise all-Spanish app; anything else passes through unchanged
// since it's already meant to be read as-is (a real Spanish message).
const KNOWN_REASON_TOKENS: Record<string, string> = {
  cancelled: "cancelado tras la página en curso",
  cancelling: "cancelando tras la página en curso",
};

export function reasonLabel(reason: string): string {
  return KNOWN_REASON_TOKENS[reason] ?? reason;
}

// N8: engine-composed free text (the estimate's per-stage `note`, e.g. the
// GPU-memory substitution message) can still embed a model id with its
// `provider:` namespace prefix attached (`ollama:qwen2.5vl:7b`), while every
// other place the app shows a model — the comboboxes, preset blurbs, the
// Convertir header — shows it unprefixed (`qwen2.5vl:7b`, via `modelLabel`'s
// own `model.split(":").slice(1).join(":")`). Left alone, the same model
// reads as two different ones two lines apart. Strip the same known
// provider namespaces wherever they appear in a longer sentence, not just
// at the start of a bare id.
const MODEL_PROVIDER_PREFIXES = ["ollama", "openai", "anthropic", "google", "groq", "mistral", "openrouter"];
const MODEL_PREFIX_RE = new RegExp(`\\b(?:${MODEL_PROVIDER_PREFIXES.join("|")}):`, "g");

export function stripModelProviderPrefixes(text: string): string {
  return text.replace(MODEL_PREFIX_RE, "");
}

/** Spanish "running" label for a queue row's current page, keyed off the
 * `page_started` event's `engine` (tesseract | "ollama:<tag>" |
 * "<provider>:<model>" | null before the server has reported one yet). */
export function engineLabel(engine: string | null): string {
  if (engine == null) return "Leyendo…";
  if (engine === "tesseract") return "Leyendo con Tesseract…";
  if (engine.startsWith("ollama:")) return `Leyendo con ${stripModelProviderPrefixes(engine)} (local)…`;
  return `Leyendo con ${stripModelProviderPrefixes(engine)} (nube)…`;
}

export function fmtBytes(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let v = bytes / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return fmtNumber(v, v < 10 ? 1 : 0) + " " + units[i];
}

/** Spanish label for a file's kind — shared by the pre-convert inspect list
 * (InspectFile.kind), the queue rows (JobFileState.kind) and the Revisar
 * drawer's ORIGINAL panel (OriginalInfo.kind), which use overlapping but not
 * identical vocabularies (see api/types.ts's `FileKind` vs `JobFileKind`).
 * Exported from here (rather than living in one screen) so no other call
 * site can reintroduce a raw English kind token (see flow-issues.md N2). */
export function kindLabelEs(kind: string | null): string {
  switch (kind) {
    case "text":
      return "texto";
    case "html":
      return "web";
    case "ebook":
      return "libro";
    case "office":
      return "Office";
    case "image":
      return "imagen";
    case "mindmap":
      return "mapa mental";
    case "scanned":
      return "escaneado";
    case "pdf":
      return "PDF";
    case "other":
      return "otro";
    default:
      return "—";
  }
}
