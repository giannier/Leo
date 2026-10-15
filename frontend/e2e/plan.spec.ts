// See `./fixtures` for why the rate limiter is reset rather than raised.
import type { Browser, Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Mi plan and Cobros (Feature 009, TASK-908): the whole manual payment, by hand.
 *
 * The owner picks a plan and gets a reference; the platform admin records the
 * transfer against it; the owner's plan is active and the receipt is listed.
 * The admin is promoted with the same script Giannier uses
 * (`scripts.grant_platform_admin`). Billing is off in development ("Modo
 * pruebas"): choosing and paying work the same; only pausing never happens.
 */

async function register(page: Page): Promise<{ email: string; password: string }> {
  const user = freshIdentity();
  await page.goto("/register");
  await page.getByLabel("Tu nombre").fill(user.fullName);
  await page.getByLabel("Nombre de tu negocio").fill(user.businessName);
  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Crear mi cuenta" }).click();
  const message = await waitForEmail(user.email);
  await page.goto(verificationPath(message));
  await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  await page.getByLabel("Correo electrónico").fill(user.email);
  await page.getByLabel("Contraseña", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "Entrar" }).click();
  await expect(page).toHaveURL("/", { timeout: 15_000 });
  return { email: user.email, password: user.password };
}

async function promote(email: string): Promise<void> {
  const { execFile } = await import("node:child_process");
  const { promisify } = await import("node:util");
  await promisify(execFile)(
    process.platform === "win32" ? "uv.exe" : "uv",
    ["run", "--directory", "backend", "python", "-m", "scripts.grant_platform_admin", email],
    { cwd: ".." },
  );
}

async function adminPage(browser: Browser): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  const admin = await register(page);
  await promote(admin.email);
  await page.goto("/admin/cobros");
  await expect(page.getByRole("heading", { name: "Cobros" })).toBeVisible({ timeout: 15_000 });
  return page;
}

test.describe("Mi plan", () => {
  test("elegir un plan, pagar con referencia y ver el comprobante", async ({ page, browser }) => {
    await register(page);
    await page.goto("/plans");
    await expect(page.getByRole("heading", { name: "Mi plan" })).toBeVisible();
    await expect(page.getByTestId("billing-status")).toContainText("prueba gratis");
    await expect(page.getByTestId("plan-emprende")).toContainText("US$ 29.00");

    await page.getByRole("button", { name: "Anual (−20 %)" }).click();
    await expect(page.getByTestId("plan-emprende")).toContainText("US$ 278.40");
    await page.getByRole("button", { name: "Mensual" }).click();
    await page.getByTestId("plan-emprende").getByRole("button", { name: "Elegir" }).click();

    const pending = page.getByTestId("pending-charge");
    await expect(pending).toContainText("Plan: Emprende mensual: US$ 29.00");
    await expect(pending).toContainText("Yape al 999 000 111");
    const reference = (await pending.getByTestId("charge-reference").textContent())?.trim() ?? "";
    expect(reference).toMatch(/^LEO-[A-Z2-9]{6}$/);

    // The platform records the transfer against the reference.
    const admin = await adminPage(browser);
    await expect(admin.getByTestId("admin-pending")).toContainText(reference);
    const form = admin.getByRole("form", { name: "Registrar un pago" });
    await form.getByLabel("Referencia (LEO-...)").fill(reference);
    await form.getByLabel("N.° de operación del banco o Yape").fill(`OP-${Date.now()}`);
    await form.getByLabel("Monto en dólares").fill("29.00");
    await form.getByLabel("Monto recibido en soles (opcional)").fill("108.75");
    await form.getByRole("button", { name: "Registrar pago" }).click();
    await expect(admin.getByText(/Pago registrado: Plan: Emprende mensual, comprobante LEO-\d{6}/)).toBeVisible();
    await expect(admin.getByText(reference)).toHaveCount(0);
    await admin.context().close();

    // The owner's plan is live, nothing is owed, and the receipt is there.
    await page.reload();
    await expect(page.getByTestId("billing-status")).toContainText("Emprende mensual");
    await expect(page.getByTestId("billing-status")).toContainText("Activo");
    await expect(page.getByTestId("pending-charge")).toHaveCount(0);
    await expect(page.getByTestId("billing-payments")).toContainText("US$ 29.00");
    await expect(page.getByTestId("billing-usage")).toContainText("de 1,000");
    await expect(page.getByTestId("plan-emprende")).toContainText("Tu plan");

    const download = page.waitForEvent("download");
    await page.getByTestId("billing-payments").getByRole("button", { name: /Comprobante LEO-/ }).click();
    expect((await download).suggestedFilename()).toMatch(/^LEO-\d{6}\.pdf$/);
  });
});
