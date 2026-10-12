/**
 * Shared set-up for the simulator specs.
 */

import { expect } from "./fixtures";
import { freshIdentity, verificationPath, waitForEmail } from "./outbox";

/** Registers, verifies, signs in, and completes the checklist through the
 * wizard so the sandbox unlocks. */
export async function readyToTest(page: import("@playwright/test").Page) {
  const user = freshIdentity();

  await page.goto("/register");
  await page.getByLabel("Tu nombre").fill(user.fullName);
  await page.getByLabel("Nombre de tu negocio").fill(user.businessName);
  await page.getByLabel("Correo electrónico").fill(user.email);
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

  await page.goto("/onboarding?paso=1");
  await page.getByLabel("Nombre comercial").fill("Panadería Quispe");
  await page.getByLabel("Rubro").selectOption("retail");
  await expect(page.getByText("Guardado", { exact: true })).toBeVisible({ timeout: 15_000 });

  await page.goto("/onboarding?paso=2");
  await page.getByLabel("¿Cómo se llama tu agente?").fill("Lucía");
  await expect(page.getByText("Guardado", { exact: true })).toBeVisible({ timeout: 15_000 });

  await page.goto("/onboarding?paso=3");
  await page.getByLabel("Nombre", { exact: true }).fill("Pan ciabatta");
  await page.getByLabel("Precio", { exact: true }).fill("8.50");
  await page.getByRole("button", { name: "Agregar", exact: true }).click();
  await expect(page.getByText("Pan ciabatta")).toBeVisible({ timeout: 15_000 });

  await page.goto("/onboarding?paso=5");
  await page.getByLabel("Método").selectOption("yape");
  await page.getByLabel("Instrucciones").fill("Yape al 987654321 a nombre de Ana Quispe.");
  await page.getByRole("button", { name: "Agregar", exact: true }).click();
  await expect(page.getByText(/Yape al 987654321/)).toBeVisible({ timeout: 15_000 });

  return user;
}
