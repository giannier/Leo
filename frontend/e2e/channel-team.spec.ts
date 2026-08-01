// See `./fixtures` for why the rate limiter is reset rather than raised.
import { expect, test } from "./fixtures";

import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Number connection, team, and activation (TASK-023).
 *
 * The verification the task asks for is an end-to-end run "con solicitud
 * simulada llevada a `connected` vía API admin" - so the first case files a
 * request in the browser, drives it through the real admin API the way
 * Giannier will, and checks the panel catches up.
 *
 * That is the whole point of connecting a number in v1: a person does the
 * provider work by hand (Clarification 007), so the two halves have to meet.
 */

const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";

interface Session {
  email: string;
  password: string;
  phone: string;
  token: string;
}

async function register(
  request: import("@playwright/test").APIRequestContext,
  page: import("@playwright/test").Page,
): Promise<Session> {
  const user = freshIdentity();

  await page.goto("/register");
  await page.getByLabel("Tu nombre").fill(user.fullName);
  await page.getByLabel("Nombre de tu negocio").fill(user.businessName);
  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Teléfono de contacto").fill(user.phone);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Crear mi cuenta" }).click();
  await expect(page.getByRole("heading", { name: "Revisa tu correo" })).toBeVisible();

  const message = await waitForEmail(user.email);
  await page.goto(verificationPath(message));
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL("/", { timeout: 15_000 });

  // A token for the direct API calls below. Taken through the same login the
  // browser used, so it carries exactly the caller's real authority.
  const login = await request.post(`${API}/auth/login`, {
    data: { email: user.email, password: user.password },
  });
  const token = (await login.json()).access_token as string;
  return { email: user.email, password: user.password, phone: user.phone, token };
}

/** Asks for a one-time code and reads it out of the development outbox.
 *
 * The database stores only its hash, so the delivery is the one place the
 * plaintext exists - the same reason the email outbox exists for verification
 * links. */
async function readOtp(
  request: import("@playwright/test").APIRequestContext,
  session: Session,
) {
  const otp = await request.post(`${API}/auth/phone/request-otp`, {
    headers: { Authorization: `Bearer ${session.token}` },
  });
  expect(otp.ok()).toBeTruthy();

  // The development sender writes the code to the outbox alongside emails.
  // If it is not there, the test says so rather than timing out mysteriously.
  const { readFileSync } = await import("node:fs");
  const outbox = process.env.E2E_OUTBOX ?? "../backend/.tmp/outbox.jsonl";
  let code: string | null = null;
  for (let attempt = 0; attempt < 40 && code === null; attempt++) {
    try {
      const lines = readFileSync(outbox, "utf8").split("\n").filter(Boolean);
      for (const line of lines.reverse()) {
        const entry = JSON.parse(line) as { to: string[]; text: string };
        // Addressed to the phone: the OTP goes to a number, not an inbox.
        if (!entry.to.includes(session.phone)) continue;
        const match = /\b(\d{6})\b/.exec(entry.text);
        if (match) {
          code = match[1];
          break;
        }
      }
    } catch {
      /* not written yet */
    }
    if (code === null) await new Promise((r) => setTimeout(r, 250));
  }
  return code;
}

/**
 * Promotes a user to platform admin.
 *
 * Through the database, because there is deliberately no API that grants it -
 * an endpoint that can make somebody a platform admin is an endpoint that can
 * be used to make somebody a platform admin. Same shape as the rate-limiter
 * fixture reaching for `redis-cli`: a development-only escape hatch that the
 * product itself does not offer.
 */
async function makePlatformAdmin(email: string): Promise<void> {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const container = process.env.E2E_POSTGRES_CONTAINER ?? "leo-dev-postgres-1";
  await run("docker", [
    "exec",
    container,
    "psql",
    "-U",
    "leo",
    "-d",
    "leo",
    "-c",
    `UPDATE users SET is_platform_admin = true WHERE email = '${email}'`,
  ]);
}

test.describe("Conexión del número", () => {
  test("sin teléfono verificado, explica el paso que falta", async ({ page, request }) => {
    await register(request, page);
    await page.goto("/channel");

    // The form is not offered, and the reason is stated. A disabled form with
    // no explanation is the worst version of this gate.
    await expect(page.getByText("Primero verifica tu teléfono")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("link", { name: "Verificar mi teléfono" })).toBeVisible();
  });

  test("una solicitud llega hasta conectada y el panel lo refleja", async ({ page, request }) => {
    // The case the task names: filed in the browser, driven to `connected`
    // through the real admin API the way Giannier will, and the panel
    // catching up. In v1 a person does the provider work by hand
    // (Clarification 007), so the two halves have to meet.
    const session = await register(request, page);

    const code = await readOtp(request, session);
    expect(code, "no OTP reached the outbox; is EMAIL_OUTBOX_PATH set?").not.toBeNull();
    const verified = await request.post(`${API}/auth/phone/verify-otp`, {
      headers: { Authorization: `Bearer ${session.token}` },
      data: { code },
    });
    expect(verified.ok(), await verified.text()).toBeTruthy();

    // --- the customer files it -------------------------------------------
    await page.goto("/channel");
    const number = `+519${Date.now().toString().slice(-8)}`;
    await page.getByLabel("Número de WhatsApp").fill(number);
    await page.getByLabel("Nombre que verán tus clientes").fill("Panadería Leo");
    await page.getByRole("button", { name: "Pedir la conexión" }).click();

    await expect(page.getByText("Tu solicitud está en camino")).toBeVisible({ timeout: 15_000 });

    // --- the operator picks it up ----------------------------------------
    const admin = await register(request, page);
    await makePlatformAdmin(admin.email);
    // Re-login: `is_platform_admin` is read from the database on every
    // request, but this token was minted before the flag existed.
    const relogin = await request.post(`${API}/auth/login`, {
      data: { email: admin.email, password: admin.password },
    });
    const adminToken = (await relogin.json()).access_token as string;
    const adminAuth = { Authorization: `Bearer ${adminToken}` };

    const queue = await request.get(`${API}/platform-admin/connection-requests`, {
      headers: adminAuth,
    });
    expect(queue.ok(), await queue.text()).toBeTruthy();
    const pending = (await queue.json()) as { id: string; phone_e164: string }[];
    const mine = pending.find((entry) => entry.phone_e164 === number);
    expect(mine, "the request never reached the admin queue").toBeTruthy();

    const connected = await request.put(
      `${API}/platform-admin/connection-requests/${mine!.id}`,
      {
        headers: adminAuth,
        data: {
          status: "connected",
          credentials: {
            provider: "ycloud",
            values: {
              waba_id: "waba-1234567890",
              phone_number_id: "phone-1234567890",
              api_key: "yc-live-abcdef123456",
            },
          },
        },
      },
    );
    expect(connected.ok(), await connected.text()).toBeTruthy();

    // --- back in the customer's panel ------------------------------------
    await page.goto("/login");
    await page.getByLabel("Correo electrónico").fill(session.email);
    await page.getByLabel("Contraseña", { exact: true }).fill(session.password);
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page).toHaveURL("/", { timeout: 15_000 });

    await page.goto("/channel");
    await expect(page.getByText("Número conectado")).toBeVisible({ timeout: 15_000 });
    // The number shows twice - the connected card and the history row -
    // so the assertion is that it is there, not that it is unique.
    await expect(page.getByText(number).first()).toBeVisible();
    // And the credentials never come back out.
    await expect(page.getByText("yc-live-abcdef123456")).toBeHidden();
  });
});

test.describe("Equipo", () => {
  test("muestra los asientos, invita y cancela", async ({ page, request }) => {
    await register(request, page);
    await page.goto("/team");

    // The seat count is visible before inviting, not after being refused.
    await expect(page.getByText(/de 2 asientos usados/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Dueño")).toBeVisible();

    await page.getByLabel("Correo de la persona").fill("operario@example.com");
    await page.getByLabel("¿Qué va a poder hacer?").selectOption("operator");
    await page.getByRole("button", { name: "Enviar invitación" }).click();

    await expect(page.getByText("Invitación enviada")).toBeVisible({ timeout: 15_000 });
    // Twice on screen once it lands - the confirmation and the pending
    // list - so this asks for the list row specifically.
    await expect(
      page.getByText("operario@example.com", { exact: true }),
    ).toBeVisible();

    // Now the seat is taken, and the form says so up front rather than
    // letting somebody fill it in and be refused.
    await expect(page.getByText(/2 de 2 asientos usados/)).toBeVisible();
    await expect(page.getByText("No te quedan asientos")).toBeVisible();

    await page.getByRole("button", { name: "Cancelar" }).click();
    await expect(page.getByText(/1 de 2 asientos usados/)).toBeVisible({ timeout: 15_000 });
  });

  test("explica lo que puede hacer cada rol", async ({ page, request }) => {
    await register(request, page);
    await page.goto("/team");

    // Nobody outside the codebase knows what `tenant_admin` means.
    await expect(page.getByText(/Lee y responde conversaciones/)).toBeVisible({ timeout: 15_000 });
    await page.getByLabel("¿Qué va a poder hacer?").selectOption("tenant_admin");
    await expect(page.getByText(/configurar el agente, el catálogo y el equipo/)).toBeVisible();
  });
});
