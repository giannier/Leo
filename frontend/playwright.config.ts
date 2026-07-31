import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end configuration (TASK-020).
 *
 * These run against the **real stack** - the Next.js dev server talking to the
 * FastAPI backend talking to Postgres. That is the point: the things this
 * suite is here to catch are the ones no unit test can see, like the refresh
 * cookie not travelling because of a `SameSite` attribute or a CORS header.
 *
 * The backend must already be up (`make up`). Playwright does not start it:
 * a web server it manages would need the database and the migrations too, and
 * a half-started stack fails in ways that look like test failures.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  // Serial. The suite registers real accounts in a shared database, and the
  // trial phone lock is global - two parallel workers racing on the same
  // number would fail for a reason that has nothing to do with the code.
  workers: 1,
  fullyParallel: false,
  // Never in CI: a `.only` left behind would quietly shrink the suite to one
  // test and still report green.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],

  use: {
    baseURL: BASE_URL,
    // Kept only for failures. Traces for green runs are gigabytes of nothing.
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      // The audience is on mid-range Android phones, so the phone layout is
      // not an afterthought to check by hand - the bottom nav, the 44px
      // targets and the safe-area padding only exist on this viewport.
      name: "android",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
