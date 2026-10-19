/**
 * Playwright configuration for the P0 end-to-end check.
 *
 * Deliberately not part of any default test run: the spec drives a real
 * browser against a real stack — API, worker, Postgres, Redis and Docker — and
 * runs real Spark. It is the mechanical form of the Phase 8 done-when
 * criterion, and it is run on purpose with `make e2e`.
 *
 * Uses the Chrome already installed on the machine rather than downloading a
 * browser. If that channel is unavailable, `npx playwright install chromium`
 * and set `PLAYWRIGHT_CHANNEL=` to use the bundled one instead.
 */

import { defineConfig, devices } from "@playwright/test";

const WEB = process.env.PLAYWRIGHT_WEB_URL ?? "http://localhost:3000";
const CHANNEL = process.env.PLAYWRIGHT_CHANNEL ?? "chrome";

export default defineConfig({
  testDir: "./e2e",

  // A real import, a real Spark run and a real experiment. Generous, and the
  // spec reports its own progress so a slow step is visible rather than opaque.
  timeout: 15 * 60 * 1000,
  expect: { timeout: 30 * 1000 },

  // One worker: the stack has one worker process, and two browsers competing
  // for it would queue rather than parallelise.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,

  reporter: [["list"]],

  use: {
    baseURL: WEB,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    ...(CHANNEL ? { channel: CHANNEL } : {}),
  },
});
