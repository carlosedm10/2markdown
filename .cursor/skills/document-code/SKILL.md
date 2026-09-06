---
name: document-code
description: >-
  Document a codebase the human-first way: one entry point per scope (AGENTS.md), one doc per
  domain, a ~10-minute total read budget, and principles/taxonomy/data-flows instead of code-level
  detail (agents read the code itself). ALWAYS starts with a deep dive into the actual code — never
  writes or trusts docs derived from existing docs. Triages what already exists: a polished,
  accurate doc is LEFT ALONE or surgically updated for what changed in the code; only missing or
  structurally broken docs are written from scratch. Use when asked to "document this
  repo/component/service", "our docs are stale / too long / nobody reads them", "update the docs
  for this change", "write an AGENTS.md", "set up a documentation convention", "audit the docs",
  "onboarding docs for a new joiner", "docs an AI agent can use", or "simplify the documentation".
  Also covers README terminal recordings (VHS `.tape` → GIF): when a demo is worth recording at
  all, which command to record, and the three-per-README cap.
---

# document-code

Documentation that a new joiner — or someone from another team — can read end to end in **~10 minutes** and come away understanding the system. Written for humans; agents read the same files.

The skill works in three modes and **picks per file, not per task**: leave alone, update surgically, or write from scratch. **Phase 0 is never optional.**

## When to use

- Documenting a repo, service, component, or module for the first time.
- Docs exist but are stale, bloated, contradictory, or unread.
- A code change landed and its docs need to catch up.
- Setting up (or enforcing) a documentation convention across a codebase.
- Writing or trimming an `AGENTS.md`.
- Trigger phrases: "document this", "audit the docs", "our docs are stale", "update the docs", "onboarding doc", "write an AGENTS.md", "simplify the documentation".

## When not to use

- API reference, generated docs, or a changelog (those are artifacts, not docs).
- A design proposal for work not yet done → that is a **plan**, not documentation.
- A one-file script or throwaway prototype — the code is the doc.

## Rule zero — preserve what already works

**Never regenerate a doc you could have edited.** A doc that already follows the protocol and is accurate is *finished work*; rewriting it destroys hand-earned nuance (caveats, war stories, deliberate wording, ordering someone argued about) and produces a diff nobody can review.

Default to the **smallest possible diff**. Rewrite a whole file only when Phase 1 classifies it `RESTRUCTURE` or `CREATE`, and say why.

What you must not touch when updating:
- Working prose, section order, and formatting choices that already read well.
- Hand-added caveats, "why not X" asides, and gotchas — these are the highest-value lines in any doc and the least likely to be recoverable.
- Voice and level of formality.
- Anything you cannot show is wrong. "I would have phrased it differently" is not a defect.

## The two budgets (the spine of everything)

1. **The 10-minute rule.** All the documentation in the repo, read back to back, fits in ~10 minutes. Adding something means cutting something. Per-scope target: one screen for an entry point, ~5 minutes for a domain doc.
2. **Principles, not code.** Docs carry the taxonomy, the architecture shape, the data flows, and the *why* behind decisions. Code-level detail — column names, method signatures, step-by-step recipes — stays in the code (and in skills like this one), where it cannot rot.

Corollary: **write only what the reader cannot get by reading the code in the same time.** A doc that restates the code is worse than no doc, because it will disagree with the code within a month.

## Workflow

### Phase 0 — Deep dive into the code (MANDATORY, always first)

Never write documentation from existing documentation, a ticket, or a memory. Existing docs are *evidence of intent*, not fact — treat every claim in them as unverified.

1. Map the structure: entry points, top-level folders, boundaries/ownership, build & test tooling.
2. Trace **one real request/operation end to end** and write down the actual path. This becomes the data-flow section and exposes the true architecture.
3. Extract the **taxonomy**: the domain vocabulary that repeats across folders, types, and APIs. If the same 6–10 names appear everywhere, that list *is* the mental model.
4. Find the **decisions**: places where the code deliberately does something surprising (an abstraction, a cache, a workaround, a boundary). Note the *why* — this is the highest-value, least-recoverable knowledge.
5. Note **flaws you pass** (violations, dead code, inconsistencies). They do **not** go in docs — see "Code flaws" below.

For a large codebase, fan out parallel read-only subagents by area and synthesize. Ask them for file-path-backed claims, not prose.

### Phase 1 — Triage every existing doc (one verdict each)

For each doc-ish file in scope (`README`, `AGENTS.md`, `docs/`, scattered `*.md`), assign exactly one verdict. Report the verdicts before editing anything.

| Verdict | When | What you do |
|---|---|---|
| **KEEP** | Follows the kit, claims verify true, within budget, reads well. | **Nothing.** Say "no change needed" and move on. |
| **UPDATE** | Structurally sound but drifted: a few wrong/missing facts, a new subsystem to add, a moved path. | Surgical edits only — touch the affected lines, leave the rest byte-identical. |
| **RESTRUCTURE** | Answers several questions at once, sits at the wrong level, duplicates another doc, or is mostly code-level detail. | Move/split/merge per the placement rule, preserving every sentence still worth keeping. |
| **CREATE** | The scope has no doc, or the existing one is beyond repair (mostly wrong). | Write from the template. |

**Find the drift, don't guess at it.** The reliable signal is time: check when the doc last changed versus the code it describes.

```bash
git log -1 --format=%cd -- <doc>                 # when the doc was last touched
git log --oneline --since=<that date> -- <code>  # what changed in the code since
```

Then read only those changes and ask: does any of it contradict or extend the doc? That is your edit list — and it is usually a handful of lines, not a rewrite.

Verify claims against Phase 0:
- **Wrong** → fix (a wrong doc is worse than no doc).
- **Code-level detail** → cut; the code owns it.
- **Duplicated** → keep the copy closest to the code, link the rest.
- **Broken links / moved files** → verify every relative link resolves; grep for old paths, including in code comments.
- **Contradictions between docs** → the one further from the code is usually wrong.

### Phase 2 — Decide the shape (only for RESTRUCTURE / CREATE)

Map what survives onto the kit below. Placement rule: **a doc lives at the lowest level that contains everything it talks about.** If a doc keeps talking about two areas, it moves up; if a root-level doc is really about one domain, it moves down. The root **routes, never hosts** — each domain has exactly one home, and cross-layer stories live in the doc of the scope that owns the contract.

### Phase 3 — Write

`UPDATE` → editing tools, never a full-file rewrite. `CREATE` → the templates in `reference.md`, domain doc first (it holds the thinking), then the entry point that routes to it, then any code-adjacent README.

Mechanics that matter:
- **Full-width lines.** Never hard-wrap prose mid-line; let markdown wrap. Keep line structure only inside code blocks, diagrams, and tables. (When updating a file that is already hard-wrapped, match its existing style rather than reflowing everything into your diff — reflow is its own commit.)
- One ASCII diagram per doc, maximum, showing the real path.
- Tables for taxonomies; bullets for decisions; prose for flows.
- Define a term before using it, or gloss it inline the first time.

### Phase 4 — Verify with fresh eyes

Do not trust your own draft. Run a genuine verification pass (a subagent reading cold is ideal):

1. **Link check** — every relative link resolves to a real file (verify, don't assume).
2. **Accuracy spot-check** — pick ~5 factual claims and confirm each against the code.
3. **Read-time** — total word count across the doc set ÷ ~200 wpm ≤ 10 minutes.
4. **Contradictions** — no two docs disagree; nothing references a deleted file.
5. **Altitude** — quote anything that reads like code documentation and cut it.
6. **Newcomer confusion** — undefined terms, assumed context, used-before-defined.
7. **Diff review** — every changed line is a fact fix, an addition, or a deletion you can justify. Revert incidental rewording.

Fix what it finds, then re-verify. Stop when a pass produces nothing new.

## The file kit (do not invent more file types)

| File | Answers | How it changes |
|---|---|---|
| `AGENTS.md` (per scope) | "What are the rules here, and what do I read next?" | Rules added/removed. One screen, never more. **It routes; it does not explain.** |
| `docs/README.md` (per domain) | "What is this, how is it built, how does data flow, and why is it this way?" | Edited in place to stay true. ~5-minute read. |
| Code-adjacent `README.md` | "How does this one gnarly folder work?" | Lives *inside* the folder it describes (a provider adapter, a codegen pipeline), next to its artifacts. Mechanics + hard-won quirks. |
| `plans/*.md` | "What are we about to build?" | A workspace, with a status line. Distilled into the domain doc and **deleted** when shipped. |
| Tracking artifacts | "What is the current status of N things?" | Ticked mechanically (`endpoints.csv`, `COVERAGE.md`). Live next to what they track; label them as artifacts. |
| `docs/demo/*.tape` + rendered GIF | "What does this actually look like when I run it?" | The tape is the source; the GIF is re-rendered, never edited. **Three in the README, maximum.** See below. |

**Why exactly these — the churn principle.** Docs rot for two reasons: distance from the code they describe, and mixing content that changes in different ways. Each file above has exactly **one update gesture** — rules are added/removed, explanations are edited, plans are deleted, artifacts are ticked. A file mixing gestures (rules + history + status) is always partially stale, so people stop trusting it, so they stop updating it. Fewer files is not the goal; **one gesture per file** is.

## Terminal recordings in the README (VHS)

A recording is the one thing in the kit that shows *behaviour over time* — a picker, a dependency-ordered run, a confirmation gate, a TUI taking shape. It is also the fastest-rotting file you can add: nobody edits a GIF, so a stale one is re-recorded or it lies. Record with [VHS](https://github.com/charmbracelet/vhs) (`brew install vhs`), keep the `.tape` next to the `.gif`, and earn every one.

**Three in the repo README, maximum. Domain docs get none** — if a flow needs a picture there, it needs a diagram, not a movie.

### Step R0 — prove the recording is NEEDED (before writing a tape)

Do not start from "what would look cool". Start from what people actually run, read out of the repo itself:

1. **Read the `Makefile`** (or `Justfile`/`package.json` scripts) — the target list *is* the intended interface, and the target everyone runs first is your strongest candidate.
2. **Read the setup path** — the bootstrap/install script and the README's Quick start. Whatever a new joiner types in week one is what a recording is for.
3. **Read the CLI's verb list** if the repo ships one, plus any interactive flag (`--pick`, wizards, `-i`).

Then keep a candidate only if **all three** hold:

- **Static text can't carry it.** The value is in the motion: an interactive selection, a progress/parallel run, a spinner resolving, a prompt being answered, panes tiling. If the output is a fixed block of text, paste it as a fenced code block — copyable, greppable, diffable, and ~200× smaller.
- **It's the repo's reason to exist**, not a peripheral flag or a one-off.
- **It replaces prose you'd otherwise have to write**, and the reader understands the command better after watching than after reading.

Reject on sight: `brew install`-style setup steps, anything showing credentials, tokens, customer data, or internal hostnames, and "look how pretty" recordings with no informational payload.

### Step R1 — pick the surviving three

If more than three pass, do not record more — choose along **different axes**, so the three together teach the tool rather than repeat it:

1. **The one-glance command** — the thing you run to see where you stand (the README hero, right under the title).
2. **The scariest operation** — the destructive or long-running one, shown with its guard rail and its real duration (sped up).
3. **The one nobody knows exists** — the interactive/selective mode that changes how people work.

Each GIF sits directly under the heading whose claim it proves, with alt text equal to the command (`![fdev status](docs/demo/fdev-status.gif)`). A recording that isn't backed by a sentence of prose is decoration — cut it or write the sentence.

### Triage and upkeep

Treat **tape + GIF as one unit** and give the *tape* the verdict. `UPDATE` a recording only when the command's output shape actually changed — then edit the tape and re-render (`vhs docs/demo/<name>.tape`); never hand-patch a GIF. A recording of a command that no longer exists is a broken link: delete both files and the README line.

Tape template, settings, and the size budget are in `reference.md`.

**Entry-point naming.** `AGENTS.md` is the emerging cross-tool standard. Some tools only auto-load their own filename (e.g. Claude Code reads `CLAUDE.md`), so put the content in `AGENTS.md` and leave a one-line relay file (`@./AGENTS.md`) for those tools. Apply the same pattern at every scope — no exceptions, or the asymmetry becomes the thing people ask about.

## Non-negotiable rules

- **Phase 0 first, always.** Code is the source of truth. Docs, tickets, and memories are hypotheses.
- **Preserve by default.** Smallest diff that makes the doc true; a polished doc gets `KEEP`.
- **Nothing enters a doc unverified.** If you cannot confirm it while writing, cut it.
- **A fact lives in exactly one file**; everything else links to it.
- **A new section goes *inside* the existing doc.** It graduates to its own file only when it genuinely cannot stay short — and first ask whether that detail belongs in docs at all.
- **Docs ride the PR.** A change to architecture, a data flow, or a decision updates its doc in the same PR. Treat a stale doc like a failing test.
- **Decisions get a bullet** when a choice would surprise a new joiner or closed a real alternative. "We use React Router" is not a decision; "automations own no logic, they reference other entities" is.
- **Prefer deleting to hedging.** Never pad a doc to look thorough.

## Code flaws found along the way

Phase 0 surfaces real problems. They are **not documentation** — do not soften them into prose or hide them in a doc. Write them to a separate `INCONSISTENCIES.md` at the scope root: one line each, grouped by severity, each verified against the code, and deleted as they are fixed. Docs describe the intended system; this file tracks where the code disagrees with it.

## References

- `reference.md` — the triage rubric (KEEP vs UPDATE signals), copy-paste templates for every file in the kit, the verification checklist, and a worked example of the doc tree.
