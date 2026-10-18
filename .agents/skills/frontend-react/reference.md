# Frontend React reference

## Complete human-init command block

Copy-paste for the user:

```bash
# From repo root
bun create vite frontend --template react-ts
cd frontend
bun install
bunx --bun shadcn@latest init
bun add lucide-react
bunx --bun shadcn@latest add button card input
```

## tsconfig.json path alias (for shadcn @/ imports)

Ensure `tsconfig.json` includes:

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  }
}
```

## Example App.tsx with shadcn + lucide

```tsx
import { Button } from "@/components/ui/button";
import { Rocket } from "lucide-react";

export default function App() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Button>
        <Rocket className="mr-2 h-4 w-4" />
        Get Started
      </Button>
    </div>
  );
}
```
