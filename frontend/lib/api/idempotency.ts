/**
 * The pieces behind "pressing Guardar twice must not save twice".
 *
 * One `Idempotency-Key` per intent: a retry of the same payload after a failure reuses it,
 * a success forgets it. Kept apart from the HTTP client so it can be checked on its own.
 */

const KEY_LIFETIME_MS = 5 * 60_000;
const retryKeys = new Map<string, { key: string; until: number }>();

/** The one string that says "this is the same write". */
export function writeFingerprint(method: string, path: string, body: unknown): string {
  return `${method} ${path} ${JSON.stringify(body ?? null)}`;
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** The key for this payload: the previous attempt's while it may still have succeeded. */
export function keyFor(fingerprint: string, now: number = Date.now()): string {
  const known = retryKeys.get(fingerprint);
  if (known && known.until > now) return known.key;
  const key = newKey();
  retryKeys.set(fingerprint, { key, until: now + KEY_LIFETIME_MS });
  return key;
}

export function forgetKey(fingerprint: string): void {
  retryKeys.delete(fingerprint);
}
