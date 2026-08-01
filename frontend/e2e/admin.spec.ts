// See `./fixtures` for why the rate limiter is reset rather than raised.
import { expect, test } from "./fixtures";

import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * The platform operator's panel (TASK-024).
 *
 * The whole surface is one deliberate exception: it crosses tenant
 * boundaries, and nothing else in the product does. So half of what is
 * checked here is the boundary rather than the feature - that an ordinary
 * customer reaching for `/admin` is sent to their own panel, and that
 * credentials go in and never come back out.
 */

const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";

interface Account {
  email: string;
  password: string;
  phone: string;
  business: string;
  token: string;
}

async function signUp(
  request: import("@playwright/test").APIRequestContext,
  page: import("@playwright/test").Page,
): Promise<Account> {
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

  const login = await request.post(`${API}/auth/login`, {
    data: { email: user.email, password: user.password },
  });
  return {
    email: user.email,
    password: user.password,
    phone: user.phone,
    business: user.businessName,
    token: (await login.json()).access_token as string,
  };
}

async function signIn(page: import("@playwright/test").Page, account: Account) {
  await page.goto("/login");
  await page.getByLabel("Correo electrónico").fill(account.email);
  await page.getByLabel("Contraseña", { exact: true }).fill(account.password);
  await page.getByRole("button", { name: "Entrar" }).click();

  // Wait for the session to actually exist before returning. Clicking and
  // moving on raced: locally the login won, in CI it did not, and the next
  // `goto` landed on a page that bounced straight back to /login. The
  // failure then pointed at whatever that page was supposed to show.
  await expect(page).toHaveURL("/", { timeout: 20_000 });
}

/** See `channel-team.spec.ts`: a script, never an endpoint. */
async function promote(email: string): Promise<void> {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  const run = promisify(execFile);
  const uv = process.platform === "win32" ? "uv.exe" : "uv";
  await run(
    uv,
    ["run", "--directory", "backend", "python", "-m", "scripts.grant_platform_admin", email],
    { cwd: ".." },
  );
}

/** A customer with a verified phone and a filed connection request. */
async function customerWithRequest(
  request: import("@playwright/test").APIRequestContext,
  page: import("@playwright/test").Page,
): Promise<{ account: Account; number: string }> {
  const account = await signUp(request, page);

  await request.post(`${API}/auth/phone/request-otp`, {
    headers: { Authorization: `Bearer ${account.token}` },
  });

  const { readFileSync } = await import("node:fs");
  const outbox = process.env.E2E_OUTBOX ?? "../backend/.tmp/outbox.jsonl";
  let code: string | null = null;
  for (let attempt = 0; attempt < 40 && code === null; attempt++) {
    try {
      for (const line of readFileSync(outbox, "utf8").split("\n").filter(Boolean).reverse()) {
        const entry = JSON.parse(line) as { to: string[]; text: string };
        if (!entry.to.includes(account.phone)) continue;
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
  expect(code, "no OTP reached the outbox").not.toBeNull();

  await request.post(`${API}/auth/phone/verify-otp`, {
    headers: { Authorization: `Bearer ${account.token}` },
    data: { code },
  });

  const number = `+519${Date.now().toString().slice(-8)}`;
  const filed = await request.post(`${API}/tenant/connection-requests`, {
    headers: { Authorization: `Bearer ${account.token}` },
    data: {
      phone_e164: number,
      display_name: "Panaderia Leo",
      business_description: "Panaderia artesanal en Miraflores",
    },
  });
  expect(filed.ok(), await filed.text()).toBeTruthy();

  return { account, number };
}

test.describe("El panel de operación", () => {
  test("un cliente común no llega, y no se le dice que existe", async ({ request, page }) => {
    const account = await signUp(request, page);
    await signIn(page, account);

    await page.goto("/admin");

    // Sent to their own panel. From their side this address does not exist,
    // and the backend answers 404 rather than 403 for the same reason.
    await expect(page).toHaveURL("/", { timeout: 15_000 });
    await expect(page.getByText("Solicitudes de conexión")).toBeHidden();
  });

  test("el operador conecta un número y el cliente lo ve", async ({ request, page }) => {
    const { account: customer, number } = await customerWithRequest(request, page);

    const operator = await signUp(request, page);
    await promote(operator.email);
    await signIn(page, operator);

    // This operator also runs a business, which is the ordinary case: the
    // first one registers like any customer and is then promoted. They keep
    // their own panel and cross over through a link, rather than being
    // redirected away from an account that is theirs.
    await page.getByRole("link", { name: "Panel de operación" }).click();

    await expect(page).toHaveURL(/\/admin/, { timeout: 15_000 });
    await expect(page.getByRole("heading", { name: "Solicitudes de conexión" })).toBeVisible();

    const card = page.locator("section").filter({ hasText: number });
    await expect(card).toBeVisible({ timeout: 15_000 });

    await card.getByRole("button", { name: "Tomar" }).click();
    await expect(page.getByText("En trámite").first()).toBeVisible({ timeout: 15_000 });

    await page.getByRole("button", { name: "Nuevas" }).click();
    await page.getByRole("button", { name: "En trámite" }).click();
    const taken = page.locator("section").filter({ hasText: number });
    await taken.getByRole("button", { name: "Conectar" }).click();

    await page.getByLabel("WABA ID").fill("waba-1234567890");
    await page.getByLabel("Phone number ID").fill("phone-1234567890");
    await page.getByLabel("API key").fill("yc-live-abcdef123456");
    await page.getByRole("button", { name: "Conectar el número" }).click();

    // Gone from the open queue.
    await expect(page.getByText(number)).toBeHidden({ timeout: 20_000 });

    // And live for the customer.
    await page.getByRole("button", { name: "Cerrar sesión" }).click();
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
    await signIn(page, customer);
    await page.goto("/channel");
    await expect(page.getByText("Número conectado")).toBeVisible({ timeout: 15_000 });
    // The credentials went in and never came back out.
    await expect(page.getByText("yc-live-abcdef123456")).toBeHidden();
  });

  test("rechazar exige un motivo, y el cliente lo lee", async ({ request, page }) => {
    const { account: customer, number } = await customerWithRequest(request, page);

    const operator = await signUp(request, page);
    await promote(operator.email);
    await signIn(page, operator);
    await page.goto("/admin");

    const card = page.locator("section").filter({ hasText: number });
    await card.getByRole("button", { name: "Rechazar" }).click();

    // The button stays out of reach until there is something to tell them.
    const send = page.getByRole("button", { name: "Rechazar solicitud" });
    await expect(send).toBeDisabled();

    await page
      .getByLabel("¿Por qué la rechazas?")
      .fill("El nombre no cumple las políticas de Meta.");
    await expect(send).toBeEnabled();
    await send.click();

    await expect(page.getByText(number)).toBeHidden({ timeout: 20_000 });

    // Verbatim, on the customer's own screen, so they can correct and re-file.
    await page.getByRole("button", { name: "Cerrar sesión" }).click();
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
    await signIn(page, customer);
    await page.goto("/channel");
    // Twice on that screen - the rejection notice and the history row - so
    // this asks that it is there, not that it is unique.
    await expect(
      page.getByText("El nombre no cumple las políticas de Meta.").first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  test("extender una prueba exige un motivo y mueve la fecha", async ({ request, page }) => {
    const customer = await signUp(request, page);

    const operator = await signUp(request, page);
    await promote(operator.email);
    await signIn(page, operator);
    await page.goto("/admin");

    await page.getByRole("link", { name: "Negocios" }).click();
    await page.getByLabel("Buscar por nombre").fill(customer.business);
    await page.getByText(customer.business).first().click();

    await expect(page.getByRole("heading", { name: customer.business })).toBeVisible({
      timeout: 15_000,
    });
    const before = await page.getByText(/La prueba vence/).locator("..").innerText();

    // A concession with no reason is indistinguishable, later, from an
    // account somebody quietly kept free.
    const extend = page.getByRole("button", { name: "Extender" });
    await expect(extend).toBeDisabled();

    await page.getByLabel("Motivo").fill("Demora nuestra en conectar el número.");
    await expect(extend).toBeEnabled();
    await extend.click();

    await expect(page.getByText(/Prueba extendida 7 dias/)).toBeVisible({ timeout: 15_000 });
    const after = await page.getByText(/La prueba vence/).locator("..").innerText();
    expect(after).not.toBe(before);
  });
});
