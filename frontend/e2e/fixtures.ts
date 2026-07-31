/**
 * Shared fixtures for the end-to-end suite.
 *
 * The one job here is resetting the auth rate limiter between tests.
 *
 * `/auth/*` allows five requests per minute per client address, and a browser
 * driving register → verify → login burns through that in one test. Every
 * test after the first would then fail with a 429 - not because anything is
 * broken, but because the limiter is working.
 *
 * The limit is *not* raised for tests. It is a real control, and a suite that
 * runs against a weakened version of production stops being evidence. The
 * counter is a Redis key with a known namespace, so the fixture deletes it
 * and each test starts with a clean window.
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { test as base } from "@playwright/test";

const run = promisify(execFile);

const REDIS_CONTAINER = process.env.E2E_REDIS_CONTAINER ?? "leo-dev-redis-1";
/** Matches `RedisRateLimiter`'s default namespace in `app/core/rate_limit.py`. */
const KEY_PATTERN = "ratelimit:*";

async function resetRateLimiter(): Promise<void> {
  try {
    const { stdout } = await run("docker", [
      "exec",
      REDIS_CONTAINER,
      "redis-cli",
      "--scan",
      "--pattern",
      KEY_PATTERN,
    ]);
    const keys = stdout.split("\n").map((k) => k.trim()).filter(Boolean);
    if (keys.length > 0) {
      await run("docker", ["exec", REDIS_CONTAINER, "redis-cli", "DEL", ...keys]);
    }
  } catch {
    // Not fatal. If Docker is not reachable the tests still run; they will
    // just hit the limiter, and the 429 in the failure output says so
    // plainly enough that nobody will hunt for a phantom bug.
  }
}

export const test = base.extend({
  page: async ({ page }, use) => {
    await resetRateLimiter();
    await use(page);
  },
});

export { expect } from "@playwright/test";
