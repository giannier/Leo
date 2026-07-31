/**
 * Reads the development email outbox.
 *
 * The verification token is stored hashed, so the database can never give it
 * back - the message is the only place its plaintext exists. With
 * `EMAIL_PROVIDER=console` and `EMAIL_OUTBOX_PATH` set, the backend appends
 * every "sent" message to a JSONL file, and this reads the link out of it.
 *
 * That is the whole reason those two settings exist. Without them the
 * end-to-end suite has to skip the middle of `register → verify → login`,
 * which is precisely the step most likely to break.
 */

import { readFileSync } from "node:fs";

const OUTBOX = process.env.E2E_OUTBOX ?? "../.tmp/outbox.jsonl";

interface OutboxEntry {
  id: string;
  at: string;
  to: string[];
  subject: string;
  links: string[];
  text: string;
}

function readAll(): OutboxEntry[] {
  let raw: string;
  try {
    raw = readFileSync(OUTBOX, "utf8");
  } catch {
    return [];
  }
  return raw
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as OutboxEntry);
}

/**
 * The newest message addressed to `email`.
 *
 * Polled rather than read once: registration answers 201 as soon as the row
 * is committed, and the send happens right after, so a single read races the
 * write on a fast machine.
 */
export async function waitForEmail(email: string, timeoutMs = 15_000): Promise<OutboxEntry> {
  const deadline = Date.now() + timeoutMs;
  const target = email.toLowerCase();

  while (Date.now() < deadline) {
    const match = readAll()
      .reverse()
      .find((entry) => entry.to.some((address) => address.toLowerCase() === target));
    if (match) return match;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }

  throw new Error(
    `No email for ${email} in ${OUTBOX} after ${timeoutMs}ms.\n` +
      "Is the backend running with EMAIL_PROVIDER=console and EMAIL_OUTBOX_PATH set?",
  );
}

/** The tokenised link out of a message, as a path the browser can visit. */
export function verificationPath(entry: OutboxEntry): string {
  const link = entry.links.find((href) => href.includes("token="));
  if (!link) {
    throw new Error(`No tokenised link in "${entry.subject}"`);
  }
  const url = new URL(link);
  return `${url.pathname}${url.search}`;
}

/** A unique identity per run, so reruns never collide on email or phone. */
export function freshIdentity() {
  const stamp = `${Date.now()}${Math.floor(Math.random() * 1000)}`.slice(-9);
  return {
    email: `e2e-${stamp}@example.com`,
    password: "una-clave-de-prueba-larga",
    fullName: "Ana Quispe",
    businessName: `Panaderia E2E ${stamp.slice(-4)}`,
    // E.164 Peru mobile. Unique per run because a verified contact phone can
    // only be on one live trial at a time (Clarification 005).
    phone: `+519${stamp.slice(-8)}`,
  };
}
