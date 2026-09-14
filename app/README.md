# 2markdown Desktop — frontend

React 18 + TypeScript + Vite. Talks to the local FastAPI server
(`twomarkdown.server`, `http://127.0.0.1:8765`) described in
[`../docs/desktop-app.md`](../docs/desktop-app.md); the wire types in
`src/api/types.ts` mirror `twomarkdown/server/schemas.py` field-for-field.

## Commands

```sh
bun install      # install dependencies
bun run dev      # start Vite on :5173, proxies /api (incl. websockets) to 127.0.0.1:8765
bun run dev:mock # start Vite with VITE_MOCK=1 (in-memory fixtures, no server needed)
bun run build    # tsc -b && vite build → dist/
bun run lint     # tsc --noEmit (also the CI check; no separate test runner yet)
bun run format   # prettier --write .
bun run preview  # preview the production build
```

## Mock mode

`bun run dev:mock` (or `VITE_MOCK=1 bun run dev`, or drop a
`.env.development.local` with that line) swaps every API call for in-memory fixtures
(`src/api/fixtures.ts`, `src/api/mockClient.ts`) that mirror the example
values baked into `docs/mockups/desktop-v4.html` (the "Apuntes" folder: 8
archivos · 25 páginas · 22 escaneadas). This lets every screen — including
the live event stream on "En marcha" — be reviewed before
`twomarkdown/server` exists. Screens never import `client.ts`/`mockClient.ts`
directly; they go through `src/api/index.ts`, which picks one client based on
`VITE_MOCK`.

## Structure

- `src/api/` — `types.ts` (contract, do not edit without updating
  `twomarkdown/server/schemas.py` too), `contract.ts` (the `ApiClient`
  interface both clients implement), `client.ts` (real fetch/WS client),
  `mockClient.ts` + `fixtures.ts` (VITE_MOCK=1 stand-in).
- `src/lib/scheduling.ts` — the estimate/scheduling arithmetic, a TypeScript
  port of the simulator in `docs/mockups/desktop-v4.html` (one GPU permit
  shared by local OCR+figures, 4 remote permits) plus a mirror of
  `twomarkdown/batch/estimate.py`'s resident-memory arithmetic
  (`RESIDENT_FACTOR = 1.45` over each distinct local model's file size,
  compared against the GPU limit → `blocked` / `warning`). Two callers only:
  `mockClient.ts`'s `/api/estimate` stand-in (VITE_MOCK=1) and the Wizard's
  "Personalizado" card, which needs an instant verdict before any
  `inspect_id` exists. Every screen that has a real `/api/estimate` response
  (Pipeline, Convertir) renders the server's `gpu_resident_gb` /
  `gpu_limit_gb` / `gpu_headroom_gb` / `blocked` / `warning` verbatim and
  never re-derives them here.
- `src/lib/format.ts` — `fmtTime`/`fmtUsd`/`fmtBytes` (USD only, never EUR;
  unknown price is `null`, never a false `$0`).
- `src/store/useAppStore.ts` — zustand store for cross-screen UI state
  (selected preset, current job, wizard progress, theme, …).
- `src/screens/` — one screen (single-screen restructure, see below) per rail
  destination: `Wizard` (Primera vez), `Convert` (Convertir — Archivos/Cómo
  before a run, the same file list as the live queue and the result list
  during/after one), `Historial` (every past job, newest first, expandable
  per-file rows reusing Convert's row actions), `Ajustes` (tabs: Tu equipo,
  Preajustes, Sincronizaciones y carpetas, Ajustes, Avanzado — the old Team
  and Pipeline screens live here now), `Pipeline` (the pipeline diagram
  editor, opened from Convertir's "Ajustar…"/"Editar" or from a preset row
  in Ajustes › Preajustes, never its own rail entry).
- `src/styles/theme.css` — the mockup's design tokens and component classes,
  ported 1:1 (colors, `.pill`/`.btn`/`.stage`/`.lane`/etc.), with the app's
  own shell (`.app-shell`/`.rail`/`.main`) replacing the mockup's tab/notes
  chrome, which was only for the design review document.

## What's real vs. stubbed

- All screens are fully built and wired to the `ApiClient` interface. The
  desktop restructure collapsed the old six-screen flow (Convertir → En
  marcha → Revisar, plus Queue/Review/Team as separate rail destinations)
  into one screen: Convertir's file list *is* the queue and *is* the result
  list — every action (cancel, open, review, retry with another model)
  lives on the file row, no navigation away. Review is a right-side drawer
  opened from a row (`ReviewDrawer.tsx`, deep-linkable at
  `/convertir/:jobId/:fileIndex`), not its own screen.
- In mock mode, everything works end-to-end against fixtures, including a
  scripted fake event stream for the running queue and a working Pipeline
  simulator (blocked-GPU banner, alternatives, manual price form).
- Against a real server, every call already targets the exact endpoint in
  `docs/desktop-app.md`'s table. Two endpoints are frontend-only
  conveniences not in that contract (`pickFolder` → `POST /api/pick-folder`,
  `pullOllamaModel`/`saveOpenAIKey` → `POST /api/ollama/pull` and
  `POST /api/cloud/openai/key`); each degrades to a visible fallback (a
  pasted path, a disabled "Instalado" pill) if the endpoint 404s, per the
  brief.
- `PATCH /api/syncs/{id}` is called directly (`client.ts`'s `patchSync`) —
  the server must implement it (see `docs/desktop-app.md`); this toggles a
  sync's `enabled` flag in place from Ajustes › Sincronizaciones y carpetas
  without losing its `id`/`last_run`/`files_today`.
