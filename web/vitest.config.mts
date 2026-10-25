/**
 * Component tests. Offline, in jsdom: no API, no browser, no network.
 * Playwright (e2e/) covers the running stack separately.
 */
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  test: {
    environment: "jsdom",
    include: ["**/__tests__/**/*.test.tsx", "**/__tests__/**/*.test.ts"],
    exclude: ["node_modules/**", "e2e/**", ".next/**"],
  },
});
