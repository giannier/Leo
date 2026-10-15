// See `./fixtures` for why the rate limiter is reset rather than raised.
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/**
 * Resultados and Métricas (Feature 010, TASK-1004 and TASK-1005).
 *
 * A fresh business has no traffic, so what is checked is that the screens tell
 * the truth about nothing (zeros, "ninguna", no invented sales), switch period,
 * offer the chart as a table, and save the settings; and that the platform
 * dashboard loads and records a model price. The numbers themselves are the
 * backend suite's reconciliation tests.
 */

async function register(page: Page): Promise<string> {
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
  return user.email;
}

test.describe("Resultados", () => {
  test("el negocio ve sus números, cambia de período y guarda sus minutos", async ({ page }) => {
    await register(page);
    // In the sidebar on a desktop; on a phone it is in the menu, not the bottom bar.
    await page.goto("/resultados");
    await expect(page.getByRole("heading", { name: "Resultados" })).toBeVisible();

    await expect(page.getByTestId("kpi-conversations")).toContainText("0");
    await expect(page.getByTestId("kpi-hours")).toContainText("A 4 min por conversación");
    await expect(page.getByTestId("kpi-sales")).toContainText("—");
    await expect(page.getByText("Ninguna en este período.")).toBeVisible();
    await expect(page.getByTestId("conversations-chart")).toBeVisible();

    await page.getByRole("button", { name: "Ver como tabla" }).click();
    await expect(page.getByTestId("conversations-table").locator("tbody tr")).toHaveCount(30);
    await page.getByRole("button", { name: "7 días" }).click();
    await expect(page.getByTestId("conversations-table").locator("tbody tr")).toHaveCount(7);

    const form = page.getByRole("form", { name: "Minutos por conversación" });
    await form.getByLabel("Minutos que tardaría una persona").fill("6");
    await form.getByRole("button", { name: "Guardar" }).click();
    await expect(page.getByTestId("kpi-hours")).toContainText("A 6 min por conversación");

    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Descargar CSV" }).click();
    expect((await download).suggestedFilename()).toMatch(/^leo-resultados-.*\.csv$/);
  });

  test("la plataforma ve su salud y fija el precio de un modelo", async ({ page }) => {
    const email = await register(page);
    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    await promisify(execFile)(
      process.platform === "win32" ? "uv.exe" : "uv",
      ["run", "--directory", "backend", "python", "-m", "scripts.grant_platform_admin", email],
      { cwd: ".." },
    );

    await page.goto("/admin/metricas");
    await expect(page.getByRole("heading", { name: "Métricas" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("platform-kpis")).toContainText("MRR");

    const model = `e2e-modelo-${Date.now()}`;
    const form = page.getByRole("form", { name: "Nuevo precio" });
    await form.getByLabel("Modelo").fill(model);
    await form.getByLabel("Entrada").fill("0.00025");
    await form.getByLabel("Salida").fill("0.002");
    await form.getByRole("button", { name: "Guardar precio" }).click();
    await expect(page.getByTestId("model-costs")).toContainText(model);
  });
});
