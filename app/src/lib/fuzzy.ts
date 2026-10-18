/**
 * Small fuzzy matcher for the ModelPicker combobox (see
 * components/ModelPicker.tsx) — OpenRouter alone puts hundreds of model ids
 * in the catalog (x-ai/grok-*, z-ai/glm-*, anthropic/claude-*-latest…), far
 * too many for a plain substring filter to feel fast to type against.
 * Intentionally not a dependency: this is a few dozen lines, and pulling in
 * fuzzysort/fuse.js for "does 'gflash' match 'google/gemini-flash-latest'"
 * would be a lot of bytes for one function.
 *
 * Ranking is TIERED, not one blended score — a lower tier always wins over
 * every candidate in a higher tier, regardless of how good the higher tier's
 * within-tier score is. This is what makes "gpt-" list gpt-4o-mini/gpt-4o
 * before anything else instead of some unrelated model that happens to
 * subsequence-match "g", "p", "t" and "-" somewhere in its id:
 *
 *   0. the model NAME (id with any "provider:" prefix stripped) starts with
 *      the query — "gpt-" → "gpt-4o-mini".
 *   1. the query appears as a contiguous substring anywhere in the NAME —
 *      "4omini" fails this (not contiguous in "gpt-4o-mini"'s name)  but
 *      "omini" would hit it.
 *   2. the query appears as a contiguous substring anywhere in the full ID
 *      (including the "provider:" prefix) — catches a query that names the
 *      provider, e.g. "openai:gpt".
 *   3. every character of the query appears in the ID, in order, as a
 *      (possibly non-contiguous) subsequence — the abbreviation case,
 *      "4omini" → "4o"+"mini" landing on "gpt-[4o]-[mini]", "gflash" →
 *      "g"+"flash" landing on "google/[g]emini-[flash]".
 *
 * Within a tier, a shorter NAME wins ties (a query matching a shorter id is
 * usually the more specific/relevant one), and for the substring tiers an
 * earlier match position wins first.
 *
 * Matching is scoped to the id/name ONLY — never a group label or a
 * provider's display name. Mixing those in used to make typing "gpt-"
 * subsequence-match "Google" (the provider label "Google" contributed the
 * "g" and "o"s), pushing Gemini/Groq models above every actual GPT model.
 */
export interface FuzzyMatch<T> {
  item: T;
  score: number;
}

const TIER_WEIGHT = 1_000_000;

// A small, deliberate boost within a tier (never enough to cross into the
// next one — tiers are 1,000,000 apart, this is 500) for ids the app itself
// already treats as its recommended default for a stage (see
// Wizard.tsx/Pipeline.tsx's hardcoded "openai:gpt-4o" / "openai:gpt-4o-mini").
// Without it, the "shorter name wins" tie-break inside a tier ranks bare
// version stubs like "gpt-4"/"gpt-5" ahead of the actual flagship "gpt-4o"
// for a "gpt-" query, which reads as broken to anyone who knows the catalog.
const PREFERRED_BOOST = 500;

/** `null` when `query` doesn't match `id`/`name` at all (not even as a
 * subsequence of `id`). Otherwise a number where higher is a better match —
 * sort candidates descending. Case-insensitive. `preferred` nudges a known
 * good default above same-tier competitors without ever promoting it out of
 * its tier. */
export function fuzzyScore(query: string, id: string, name: string, preferred = false): number | null {
  if (!query) return 0;
  const q = query.toLowerCase();
  const idL = id.toLowerCase();
  const nameL = name.toLowerCase();
  const boost = preferred ? PREFERRED_BOOST : 0;

  // Tier 0: name starts with the query.
  if (nameL.startsWith(q)) return TIER_WEIGHT * 3 - name.length + boost;

  // Tier 1: contiguous substring within the name.
  const nameIdx = nameL.indexOf(q);
  if (nameIdx !== -1) return TIER_WEIGHT * 2 - nameIdx * 1000 - name.length + boost;

  // Tier 2: contiguous substring within the full id.
  const idIdx = idL.indexOf(q);
  if (idIdx !== -1) return TIER_WEIGHT * 1 - idIdx * 1000 - id.length + boost;

  // Tier 3: subsequence anywhere in the id (the id already contains the
  // name as its suffix, so this covers a subsequence confined to the name
  // too — no need to check both separately).
  const seq = subsequenceScore(q, idL);
  if (seq === null) return null;
  return seq - id.length * 0.01;
}

/** The original character-subsequence scorer: every character of `q` must
 * appear in `t`, in order, with bonuses for runs of consecutive characters
 * and for landing right after a "/", ":", "-", "_", "." or at the very
 * start — exactly the shape a typed abbreviation takes. `null` if `q` is not
 * a subsequence of `t` at all. */
function subsequenceScore(q: string, t: string): number | null {
  let qi = 0;
  let score = 0;
  let prevMatchIndex = -1;
  let consecutive = 0;

  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] !== q[qi]) continue;

    let charScore = 1;
    if (ti === prevMatchIndex + 1) {
      consecutive++;
      charScore += consecutive * 3;
    } else {
      consecutive = 0;
    }

    const prevChar = ti > 0 ? t[ti - 1] : "";
    if (ti === 0 || /[/:_.\-]/.test(prevChar)) {
      charScore += 4;
    }

    score += charScore;
    prevMatchIndex = ti;
    qi++;
  }

  if (qi < q.length) return null;
  return score;
}

/** Filters + sorts `items` by `fuzzyScore(query, getId(item), getName(item))`,
 * best first. An empty query returns every item, in its original order
 * (score 0 for all), so callers can use this unconditionally instead of
 * branching on "is the user searching yet". */
export function fuzzyFilter<T>(
  items: T[],
  query: string,
  getId: (item: T) => string,
  getName: (item: T) => string,
  isPreferred?: (item: T) => boolean
): FuzzyMatch<T>[] {
  const q = query.trim();
  const scored: FuzzyMatch<T>[] = [];
  for (const item of items) {
    const s = fuzzyScore(q, getId(item), getName(item), isPreferred?.(item) ?? false);
    if (s !== null) scored.push({ item, score: s });
  }
  if (!q) return scored;
  scored.sort((a, b) => b.score - a.score);
  return scored;
}
