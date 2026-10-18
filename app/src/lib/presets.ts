import type { Pipeline, Preset } from "../api/types";

function pipelineEqual(a: Pipeline, b: Pipeline): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * PATCH /api/presets (upsert-by-id, never deletes — see flow-issues.md C7)
 * should only ever carry the presets that actually changed (a brand-new
 * preset counts as changed) — sending the whole, mostly unmodified list on
 * every save is what let a wizard run write a byte-identical "override" for
 * a builtin whose pipeline hadn't changed at all, silently writing e.g.
 * `builtin_overrides['apuntes-a-mano'].review_model = null` server-side (see
 * flow-issues.md N12): never send a no-op.
 *
 * `current` is the list as last known from the server (before this edit);
 * `next` is what the caller wants on disk afterwards. Every caller refetches
 * `GET /api/presets` after a successful PATCH and treats that full
 * `Preset[]` as the new source of truth via `setPresets` — only the PATCH
 * request body is trimmed to `changedPresets(current, next)`.
 */
export function changedPresets(current: Preset[], next: Preset[]): Preset[] {
  const byId = new Map(current.map((p) => [p.id, p]));
  return next.filter((p) => {
    const prev = byId.get(p.id);
    return !prev || prev.name !== p.name || !pipelineEqual(prev.pipeline, p.pipeline);
  });
}
