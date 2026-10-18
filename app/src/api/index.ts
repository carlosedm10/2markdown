/** Single import point for every screen: `import { api } from "../api"`.
 * VITE_MOCK=1 swaps in fixtures (mockClient.ts); otherwise talks to the real
 * server through the Vite proxy (client.ts) at http://127.0.0.1:8765. */
import { realClient } from "./client";
import { mockClient } from "./mockClient";
import type { ApiClient } from "./contract";

export const isMock = import.meta.env.VITE_MOCK === "1";
export const api: ApiClient = isMock ? mockClient : realClient;

export type { ApiClient } from "./contract";
export * from "./types";
