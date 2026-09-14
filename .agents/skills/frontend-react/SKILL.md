---
name: frontend-react
description: Scaffolds React webapps with Vite, Bun, shadcn/ui, and lucide-react icons. Official create commands are human-only — the agent provides exact bun commands and configures shadcn after init. Use when creating React webapps or when project-scaffold selects React over Next.
---

# Frontend React

## When to use

- Interactive web applications (dashboards, admin panels, SPAs)
- Client-heavy UIs with rich state management
- Projects that do not need SSR or static site generation

Use **frontend-next** for landing pages, marketing sites, or projects needing SSR/SSG.

## Stack

| Tool | Purpose |
|------|---------|
| Bun | Package manager and runtime |
| Vite + React + TypeScript | Build tool and framework |
| shadcn/ui | Component library |
| lucide-react | Icons |
| Tailwind CSS | Styling (installed via shadcn) |

## Directory layout (after human init)

```
frontend/
├── package.json
├── bun.lock
├── tsconfig.json
├── vite.config.ts
├── index.html
├── components.json        # shadcn config
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── index.css
│   ├── components/
│   │   └── ui/            # shadcn components
│   └── lib/
│       └── utils.ts
└── public/
```

## Scaffold workflow

### Phase 1 — Agent prepares

1. **Create** empty `frontend/` directory at repo root (if not exists).
2. **Print** the human-init commands below — do NOT execute them.

### Phase 2 — Human runs official create command

Provide these exact commands for the user:

```bash
# From repo root — creates frontend/ with Vite React TS template
bun create vite frontend --template react-ts

cd frontend
bun install
```

### Phase 3 — Agent continues after human init

Once the user confirms the Vite project is created:

1. **Initialize shadcn**:

```bash
cd frontend
bunx --bun shadcn@latest init
```

When guiding the user, recommend these shadcn init options:
- Style: **New York**
- Base color: project preference (default **Zinc**)
- CSS variables: **yes**

2. **Add lucide-react**:

```bash
bun add lucide-react
```

3. **Add common shadcn components** as needed:

```bash
bunx --bun shadcn@latest add button card input
```

4. **Configure** `vite.config.ts` for Docker dev (`server.host: true`, port `5173`).
5. **Add** `lint` script to `package.json` if missing (for Makefile `lint-frontend` target).

## vite.config.ts (Docker-friendly)

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: 5173,
  },
});
```

## package.json scripts (minimum for CI)

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "tsc --noEmit",
    "format": "prettier --write .",
    "preview": "vite preview"
  }
}
```

Add ESLint when the project needs stricter linting; update the `lint` script accordingly.
`format` is what `make format-frontend` runs. Add Prettier as a dev dependency (`bun add -d prettier`) when scaffolding.

## Agent may write

- `vite.config.ts` adjustments for Docker
- `components.json` after shadcn init
- Component files, pages, hooks
- `package.json` script additions

## Agent must NOT execute

- `bun create vite` / `bun create`
- `bunx shadcn init` / `bunx shadcn add`
- `bun install` (user runs locally or via Docker)

## Integration with sibling skills

| Skill | When |
|-------|------|
| dockerization-template | Vite frontend Dockerfile, port `3000:5173` |
| makefile-operations | `bun-add`, `lint-frontend`, `format-frontend`, `lint-fix-frontend`, `test-frontend` |
| github-actions | CI calls `make lint` / `make test` |

## Additional resources

- Full command sequence: [reference.md](reference.md)
